"""Agent #7 -- Drafting Agent (Multi-Step Pipeline)

Court-filing quality legal document generation using a multi-step pipeline:
  Step 1: BM25 keyword search for matching templates
  Step 2: GPT-4o-mini selects best template (with content previews + validation)
  Step 3: Fetch full template (no truncation -- templates are 2K-9K chars)
  Step 4: Generate document outline (Gemini 2.5 Flash, structured output)
  Step 5: Generate sections in parallel (Gemini 2.5 Flash, semaphore-limited)
  Step 6: Assemble with section titles + court filing footer

Citations are injected later by the orchestrator's draft-aware synthesis,
which merges results from parallel Judgment/Legislation/Newacts agents.

Handles: "Draft bail application", "Legal notice for property dispute", etc.

Uses: Gemini 2.5 Flash (outline + sections), GPT-4o-mini (template selection)
Data Source: Elasticsearch "drafting" index (497 templates, BM25 keyword search)
"""

from __future__ import annotations

import asyncio
import os
import re
import unicodedata
from datetime import date
from functools import lru_cache
from typing import List

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from core.state import (
    LegalAgentState, AgentResult, SourceMetadata,
    IntegrationContextData, FileContextData,
)
from core.clients import (
    get_es_client, get_gemini_flash, get_drafting_llm,
)
from core.settings import ES_INDICES
from core.language import (
    localize_prompt, localize_number, strip_leading_numeric_prefix,
    detect_source_languages, SUPPORTED_LANGUAGES,
)
from core.logger import get_logger, log_time
from core.progress import progress
from config.prompts import DRAFTING_SYSTEM_PROMPT, DRAFT_OUTLINE_PROMPT
from core.self_refine import self_refine

log = get_logger("Drafting")

# Max concurrent section generations (avoids Gemini rate limits)
_SECTION_CONCURRENCY = 3

# Max sections the outline can contain. Raised from 16 to 28 (2026-06-23) so
# user-authored multi-part skeletons (e.g. the Avachat writ with 15 named
# Parts A-O + GROUNDS + PRAYER + Verification + List of Documents +
# Affidavit + Annexures Index = 21 sections) survive the post-procedural-
# injection re-cap. Civil suits with the full procedural pack routinely
# need 14-16; long-form writs and complex tax appellate submissions can
# need 20-24. We don't slice silently anymore — over-cap triggers a
# loud log and chops; this is a defensive ceiling, not a budget.
_MAX_SECTIONS = 28

# Limit concurrent Drafting/ContinueDraft executions per worker process
_AGENT_SEMAPHORE = asyncio.Semaphore(3)


# --- Input Sanitization ---

_LUCENE_SPECIAL = re.compile(r'([+\-=&|!(){}\[\]^"~*?:\\/])')


def _sanitize_es_input(text: str, max_length: int = 500) -> str:
    """Sanitize user input before embedding in ES query."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = text[:max_length]
    text = _LUCENE_SPECIAL.sub(r"\\\1", text)
    return text


# --- Pydantic Models ---

class SectionPlan(BaseModel):
    title: str = Field(..., description="Section heading (e.g. 'Facts of the Case')")
    description: str = Field(
        ...,
        description=(
            "Brief scope statement (1-3 sentences) describing what content this "
            "section should carry — the key facts, doctrines, or procedural "
            "elements to cover. Write it as a SCOPE NOTE, NOT as a template or "
            "set of instructions for the section LLM. Do NOT include bracketed "
            "placeholders intended to be filled in later (e.g. '[first paragraph "
            "number of Reply on Merits]', '[last paragraph number of Prayer]') — "
            "the section LLM cannot compute those references at the time it sees "
            "this description, and they leak verbatim into the final draft. If "
            "the section needs to reference paragraph ranges of OTHER sections "
            "(e.g. an Affidavit verifying paragraphs 5-23), say so in plain prose "
            "('verify the factual paragraphs that precede the prayer'); the "
            "section LLM will resolve the actual range from the outline summary "
            "it receives at generation time."
        ),
    )
    estimated_paragraphs: int = Field(
        5,
        description="Expected number of paragraphs (3-15)",
    )
    needs_citations: bool = Field(
        False,
        description="Whether this section needs case law citations",
    )


class DraftOutline(BaseModel):
    document_title: str = Field(
        ...,
        description="Short subject heading (e.g. 'SUIT FOR COMPENSATION FOR MEDICAL NEGLIGENCE', 'WRIT PETITION UNDER ARTICLE 226 OF THE CONSTITUTION OF INDIA', 'APPLICATION FOR ANTICIPATORY BAIL UNDER SECTION 483 BNSS'). Will be rendered as the BOLDED SUBJECT HEADING at the END of the cause-title block — NOT as an H1 at top of document.",
    )
    court_details: str = Field(
        ...,
        description="""Pre-formatted markdown opening block of the
document — the LLM MUST emit this with proper blank lines (\\n\\n)
between every element (CommonMark renderers collapse single newlines).

PICK THE LAYOUT BASED ON DOCUMENT TYPE:

=== LAYOUT A: COURT FILING (plaint, petition, writ, complaint,
application, bail application, written statement) ===

Use when the document is filed in court.

**IN THE COURT OF <FORUM>, AT <CITY>**

**<SUIT/PETITION/COMPLAINT> NO. _______ OF <YEAR>**

**IN THE MATTER OF:**

<Plaintiff Full Name>

Age: <age>, Occupation: <occupation>

R/ Address: <residential address>

.....Plaintiff / Petitioner

**Versus**

<Defendant Full Name>

Age: <age>, Occupation: <occupation>

R/ Address: <residential address>

.....Defendant / Respondent

(Subject heading is appended at END by the assembler using
document_title — do NOT add it yourself.)

=== LAYOUT B: LEGAL NOTICE / REPLY TO LEGAL NOTICE ===

Use when the document is a notice (Section 138 NI Act notice, demand
notice, eviction notice, reply to legal notice). NOT filed in court.

**<NOTICE TITLE>**

(Examples: "LEGAL NOTICE", "REPLY TO LEGAL NOTICE", "NOTICE UNDER
SECTION 138 OF THE NEGOTIABLE INSTRUMENTS ACT, 1881")

Date: <date or blank line>

To,

<Recipient Name>

<Recipient Qualifications / Designation if known>

<Recipient Address>

**Subject: <Subject line, e.g. "Reply to your Legal Notice dated
DD/MM/YYYY issued on behalf of [Sender's Client Name]">**

Sir / Madam,

Under instructions from and on behalf of my client, <Client Full Name>,
<Client Description / Designation if applicable>, residing at
<Client Address>, I hereby send / address you the following <notice OR
reply>. The contents of <your notice are denied save and except those
specifically admitted herein / are as follows>.

(NO "IN THE MATTER OF", NO Plaintiff/Defendant blocks, NO Versus, NO
court name. A legal notice is a CORRESPONDENCE, not a pleading. The
title is at the TOP and is NOT re-appended at end by the assembler.)

=== LAYOUT D: POLICE COMPLAINT / FIR REGISTRATION APPLICATION ===

Use when the document is a complaint addressed to the POLICE STATION
(NOT to a magistrate). Common triggers: user says "police complaint" /
"FIR" / mentions Police Inspector / SHO / Station House Officer /
Section 154 CrPC / Section 173 BNSS. This is a LETTER, not a court
pleading. Do NOT use Layout A for these — that produces a Section 200
CrPC magistrate complaint which is filed in court, NOT what the user
asked for.

**<COMPLAINT TITLE>**

(Examples: "POLICE COMPLAINT", "APPLICATION FOR REGISTRATION OF FIR
UNDER SECTION 154 OF THE CODE OF CRIMINAL PROCEDURE, 1973", "COMPLAINT
UNDER SECTION 173 OF THE BHARATIYA NAGARIK SURAKSHA SANHITA, 2023")

Date: <date or blank line>

To,

The Police Inspector / Station House Officer,

<Name of Police Station>,

<Address of Police Station>.

**Subject: <Subject line, e.g. "Complaint regarding [offence] committed
on [date] by [accused name]">**

Sir / Madam,

I, <Complainant Full Name>, son / daughter / wife of <Father / Husband
Name>, aged <age> years, occupation <occupation>, residing at
<Complainant Address>, do hereby state and submit as follows:

(Then the body — numbered paragraphs of facts / sequence of events /
sections of BNS or IPC invoked — is emitted by the section LLM. Close
with "I therefore request your good office to register an FIR / take
cognizance and investigate the matter." The signature block is
appended automatically as footer.)

(NO "IN THE COURT OF", NO "IN THE MATTER OF", NO Plaintiff/Defendant
blocks, NO Versus. A police complaint is a LETTER addressed to the
police, not a pleading filed in court. The title is at the TOP and is
NOT re-appended at end by the assembler. If the user explicitly says
"Section 200 CrPC" / "Section 223 BNSS" / "magistrate complaint", USE
LAYOUT A instead — those ARE court filings.)

=== LAYOUT E: TAX / QUASI-JUDICIAL APPELLATE WRITTEN SUBMISSION ===

Use when the document is a written submission to a tax appellate
authority or other quasi-judicial body. Common triggers:
"CIT(A)" / "Commissioner of Income Tax (Appeals)" / "NFAC" / "ITAT" /
"Income Tax Appellate Tribunal" / "Form 35" / Assessment Year context /
Section 143(3) / Section 144B / Section 250 / "Written Submission" +
appeal context; OR GST appellate: "AAAR" / "GST Appellate Tribunal" /
"CESTAT"; OR other quasi-judicial: "NCLT" / "NCLAT" / "SAT" / "DRT" /
"DRAT". NOT a regular civil court filing — appellate submissions have
a distinct format with (Appellant)/(Respondent) in PARENTHESES (not
dotted), plain "Vs." (not bolded Versus), Subject line citing the
assessment order, and "Most Respectfully Showeth:" opening.

**BEFORE THE HON'BLE <FORUM>, <JURISDICTION>**

(Examples: "BEFORE THE HON'BLE COMMISSIONER OF INCOME-TAX (APPEALS),
NATIONAL FACELESS APPEAL CENTRE (NFAC), DELHI", "BEFORE THE HON'BLE
INCOME TAX APPELLATE TRIBUNAL, MUMBAI BENCH", "BEFORE THE HON'BLE GST
APPELLATE TRIBUNAL, NEW DELHI")

**Appeal No.: <Appeal Number>  |  Assessment Year: <AY> / Period: <Period>**

**IN THE MATTER OF:**

<Appellant Full Name>  PAN: <PAN>  (or GSTIN: <GSTIN> for GST appeals)

(Appellant)

Vs.

<Respondent Designation, e.g. "The Assessing Officer, National Faceless
Assessment Centre, Delhi" / "The Joint Commissioner of GST, Mumbai">

(Respondent)

**Subject: Written Submission in respect of Appeal against the
<Assessment Order / Adjudication Order> dated <DD.MM.YYYY> passed under
Section <X> read with Section <Y> of the <Income-tax Act, 1961 / CGST
Act, 2017 / Customs Act, 1962 / Companies Act, 2013 — pick the right
Act for the forum>.**

Most Respectfully Showeth:

The Appellant files this written submission in support of the grounds of
appeal raised against the impugned <assessment order / impugned order>.

(After this opening block, the body sections follow: "1. STATEMENT OF
FACTS OF THE CASE" → "2. GROUNDS OF APPEAL (as filed in Form 35)" with
each ground listed → "ADDITIONAL GROUND OF APPEAL" if any → "3. DETAILED
WRITTEN SUBMISSION" with groundwise sub-headings using "Re: Ground No.
X" — each sub-section restates the ground, gives detailed legal
argumentation, cites relevant case laws with full citation, and
rebuts the AO's reasoning + distinguishes AO's case laws → "4. PRAYER"
with (a)/(b)/(c) reliefs → "5. REQUEST FOR VIDEO CONFERENCING HEARING".)

(CRITICAL — do NOT use court-filing scaffolding here:
- Use "(Appellant)" / "(Respondent)" in PARENTHESES on their own
  paragraphs — NOT ".....Appellant" / ".....Respondent" with leading
  dots (that's Layout A only).
- Use plain "Vs." on its own paragraph — NOT "**Versus**" bolded.
- Do NOT add party "Age / Occupation / R/ Address" blocks — tax
  appellate convention is just Name + PAN/GSTIN.
- Do NOT label parties as "Plaintiff / Defendant" — they are
  Appellant / Respondent.
- Title is at the TOP and is NOT re-appended at end by the assembler.)

=== LAYOUT F: OFFICE APPLICATION / LETTER TO A NON-COURT AUTHORITY ===

Use when the document is an application addressed to a NON-COURT
authority. Common triggers: RTI application; application for caste /
income / domicile / character / experience / NOC certificate;
application to an employer for leave / NOC; application to a bank /
housing society / university / regulator (SEBI / RBI / IRDAI / TRAI).
NOT a pleading and NOT a court filing. The reader is an administrative
officer, not a judge — court-filing scaffolding ("IN THE COURT OF",
"IN THE MATTER OF", Plaintiff/Defendant blocks, Versus) is wrong
output here.

**<APPLICATION TITLE>**

(Examples: "APPLICATION UNDER SECTION 6 OF THE RIGHT TO INFORMATION
ACT, 2005", "APPLICATION FOR ISSUANCE OF INCOME CERTIFICATE",
"APPLICATION FOR NO OBJECTION CERTIFICATE", "APPLICATION FOR LEAVE
OF ABSENCE")

Date: <date or blank line>

To,

<Recipient Designation, e.g. "The Public Information Officer", "The
Tahsildar", "The Sub-Divisional Magistrate", "The Branch Manager",
"The Principal", "The HR Manager">,

<Name of Office / Department>,

<Office Address>.

**Subject: <Subject line, e.g. "Application for information under the
Right to Information Act, 2005" / "Application for issuance of income
certificate">**

Sir / Madam,

I, <Applicant Full Name>, son / daughter / wife of <Father / Husband
Name>, aged <age> years, residing at <Applicant Address>, respectfully
submit as follows:

(Then the body — numbered paragraphs of facts + the specific rule /
section / entitlement invoked (e.g. Section 6 RTI Act, 2005; State
Government circular dated <date>; service rule) — is emitted by the
section LLM. Close with "I therefore request you to kindly <issue the
certificate / furnish the information / grant the leave / accord
approval>." The signature block is appended automatically as footer.)

(NO "IN THE COURT OF", NO "IN THE MATTER OF", NO Plaintiff/Defendant
blocks, NO Versus, NO Prayer-clause-style numbering. An office
application is a LETTER addressed to an administrative authority, not
a pleading filed in court. The title is at the TOP and is NOT
re-appended at end by the assembler. If the user explicitly asks for
a bail application / anticipatory bail / IA under Order XXXIX CPC /
application under Section 482 BNSS / transfer application — those ARE
court filings and use LAYOUT A instead.)

=== LAYOUT C: AGREEMENT / MOU / DEED / LEASE / SALE DEED / WILL ===

Use when the document is a private contract or testamentary instrument.

**<AGREEMENT TITLE>**

(Examples: "AGREEMENT TO SELL", "MEMORANDUM OF UNDERSTANDING",
"LEASE DEED", "POWER OF ATTORNEY", "LAST WILL AND TESTAMENT")

**THIS <AGREEMENT / DEED / WILL> IS MADE AND EXECUTED ON THIS <date>**

**BETWEEN**

<First Party Full Name>

Age: <age>, Occupation: <occupation>

R/ Address: <residential address>

(hereinafter referred to as the "<First Party Role>")

**AND**

<Second Party Full Name>

Age: <age>, Occupation: <occupation>

R/ Address: <residential address>

(hereinafter referred to as the "<Second Party Role>")

(The title is at the TOP and is NOT re-appended at end by the
assembler.)

=== UNIVERSAL RULES ===

- Every element above must have a BLANK LINE after it (use \\n\\n).
- Do NOT prefix the block with `# ` (the H1) — bolded `**markdown**`
  only for titles and headers.
- Use 'R/ Address' (Indian-court convention for 'Residing at') in
  party blocks.
- If you are unsure which layout, decide in this order:
  1. "CIT(A)" / "Commissioner of Income Tax (Appeals)" / "ITAT" / "GST
     Appellate" / "AAAR" / "CESTAT" / "NCLT" / "NCLAT" / "SAT" / "DRT" /
     "Form 35" / "Written Submission" + Assessment Year / Section 143(3)
     / Section 144B / Section 250 of Income Tax Act → Layout E.
  2. "Police complaint" / "FIR" / mentions Police Inspector / SHO /
     Section 154 CrPC / Section 173 BNSS → Layout D.
  3. "Notice" / "Reply to legal notice" / "demand notice" → Layout B.
  4. "Agreement" / "MOU" / "Deed" / "Will" / "Lease" / "Sale deed" /
     "Power of attorney" → Layout C.
  5. An "application" — DECIDE BY ADDRESSEE, not by the word
     "application":
     * Addressed to a Court / Magistrate / Tribunal / Sessions Judge /
       High Court / Supreme Court / Family Court / Consumer Forum /
       Labour Court → Layout A. Examples: bail application,
       anticipatory-bail application, IA under Order XXXIX CPC,
       Section 482 BNSS application, transfer application under
       Section 24 CPC, Section 156(3) CrPC application.
     * Addressed to a Public Information Officer / government
       department / Tahsildar / Collector / SDM / Registrar / Municipal
       Corporation / employer / bank / housing society / university /
       regulator → Layout F. Examples: RTI application, application
       for income / caste / domicile / character / experience
       certificate, application for NOC, application for leave,
       application for ration card.
     If the user does not name an addressee, INFER from purpose using
     common sense (an "RTI application" addresses a PIO; an
     "application for income certificate" addresses a Tahsildar / SDM;
     a "bail application" addresses a court).
  6. Everything else (Suit, Petition, Writ, Section 200 CrPC
     magistrate complaint) → Layout A.""",
    )
    sections: List[SectionPlan] = Field(
        ...,
        description=f"Ordered list of all document sections (max {_MAX_SECTIONS} substantive sections)",
    )


class TemplateSource(BaseModel):
    source: str = Field(..., description="The most relevant source file path")
    match_quality: str = Field(
        "good",
        description=(
            "How well the chosen template matches the user's request. One of:\n"
            "  - 'good': the template is a genuine match for the user's "
            "specific doc type, statute, and subject. The drafter can use it "
            "as the structural reference confidently.\n"
            "  - 'marginal': the template is for the right family of "
            "documents but misses the specific statute/section, or the "
            "subject matter is adjacent rather than exact. The drafter can "
            "still imitate its structure, but the substantive sections will "
            "need to lean more on doctrine than on the template's prose.\n"
            "  - 'none': none of the candidates is actually a usable "
            "template for what the user asked. A bail application template "
            "selected for an arbitration petition request is 'none', not "
            "'marginal'. When the user's request names a specific statute / "
            "section / forum and no candidate covers that statute or any "
            "close cousin of it, return 'none'."
        ),
    )
    reason: str = Field(
        "",
        description=(
            "ONE SHORT SENTENCE explaining the match decision. For 'good' / "
            "'marginal': what makes this the best of the candidates. For "
            "'none': what specifically the user asked for that NO candidate "
            "covers (statute, forum, subject)."
        ),
    )


# --- Step 0.5: Doc-type classifier (pre-search gate) ---
#
# WHY: A wide census of the drafting OpenSearch index (2026-06-20) confirmed
# that all 136 "Application"-titled templates are COURT FILINGS — zero are
# office letters (RTI / income certificate / leave / NOC / employer / bank).
# So when a user asks "draft an RTI application", BM25 has nothing letter-shaped
# to retrieve and silently force-fits a court template, producing wrong output
# (Prayer + Verification + IN THE COURT OF on a one-page letter).
#
# This classifier runs BEFORE template search and gates the rest of the
# pipeline so non-court doc types skip the index entirely and use a synthetic
# skeleton instead. Single Flash-Lite call, ~150 input tokens.

DOC_TYPES = (
    "court_filing",        # plaint, petition, written statement, bail, IA, magistrate complaint
    "tribunal_appellate",  # CIT(A), ITAT, GST appellate, NCLT, CESTAT written submissions
    "office_letter",       # RTI, certificate request, leave, NOC, employer/bank/society/regulator
    "police_complaint",    # letter to SHO / Inspector for FIR registration
    "legal_notice",        # Sec 138 NI Act notice, demand notice, reply to legal notice
    "agreement_deed",      # agreement, MOU, deed, lease, will, sale, gift
    "affidavit",           # standalone affidavit (usually filed with court)
)

# Maps doc_type → footer_kind used by _build_footer / _generate_section.
DOC_TYPE_TO_FOOTER_KIND = {
    "court_filing": "court_filing",
    "tribunal_appellate": "tax_submission",
    "office_letter": "office_application",
    "police_complaint": "police_complaint",
    "legal_notice": "legal_notice",
    "agreement_deed": "agreement",
    "affidavit": "court_filing",
}


class DocTypeChoice(BaseModel):
    doc_type: str = Field(
        ...,
        description=(
            "One of: " + ", ".join(DOC_TYPES) + ". Decide by ADDRESSEE, "
            "not by the keyword 'application'. A 'bail application' addresses "
            "a court → court_filing. An 'RTI application' addresses a Public "
            "Information Officer → office_letter. An 'application for income "
            "certificate' addresses a Tahsildar → office_letter."
        ),
    )
    reasoning: str = Field(
        "",
        description="One short sentence: who is the addressee and what is the document's purpose.",
    )


_DOC_TYPE_CLASSIFIER_PROMPT = """You classify Indian-law drafting requests into ONE of these doc types:

  - court_filing: pleadings filed in a court (suit, plaint, petition, written
    statement, bail application, anticipatory bail, IA under Order XXXIX CPC,
    application under Section 482 BNSS / Section 156(3) CrPC, transfer
    application under Section 24 CPC, Section 200 CrPC / Section 223 BNSS
    magistrate complaint, writ, PIL, SLP, succession-certificate application
    to District Judge, probate petition).
  - tribunal_appellate: written submissions to tax / quasi-judicial appellate
    bodies (CIT(A), ITAT, NFAC, GST Appellate Tribunal, AAAR, CESTAT, NCLT,
    NCLAT, SAT, DRT, DRAT).
  - office_letter: letters to NON-COURT administrative authorities (RTI to
    Public Information Officer; application for income / caste / domicile /
    character / experience / NOC certificate to Tahsildar / SDM / Collector;
    application for leave / NOC to employer; application to bank / housing
    society / university / regulator like SEBI / RBI / IRDAI / TRAI).
  - police_complaint: letter to a Police Station / SHO / Inspector for FIR
    registration. Trigger words: "police complaint", "FIR", "SHO", "Section
    154 CrPC", "Section 173 BNSS" addressed to POLICE not magistrate.
  - legal_notice: pre-litigation notice (Section 138 NI Act notice, demand
    notice, eviction notice, reply to legal notice).
  - agreement_deed: private contracts / testamentary instruments (agreement
    to sell, MOU, lease deed, sale deed, power of attorney, gift deed, will).
  - affidavit: standalone affidavit (usually filed with a court — most common
    is affidavit for change of name, affidavit for passport, affidavit of support).

DECIDE BY ADDRESSEE, NOT BY THE WORD "application":
  - "bail application" → court_filing (addressee = court)
  - "RTI application" → office_letter (addressee = PIO)
  - "application for income certificate" → office_letter (addressee = Tahsildar/SDM)
  - "leave application to my manager" → office_letter (addressee = employer)
  - "leave of absence application to my employer" → office_letter
  - "leave application" without other context → office_letter (default addressee = employer)
  - "application for NOC to society" / "NOC application to bank" → office_letter
  - "application for grant of probate" → court_filing (addressee = District Judge)
  - "succession certificate application" → court_filing (addressee = District Judge)
  - "complaint to police" → police_complaint
  - "complaint under Section 200 CrPC" → court_filing (Magistrate)

USER QUERY:
{query}

CASE CONTEXT (may be empty):
{case_context}

Return doc_type AND one short reasoning sentence."""


async def _classify_doc_type(query: str, case_facts: str = "") -> str:
    """Pre-search classifier — returns one of DOC_TYPES.

    Single Flash-Lite call. Used to gate template search: office_letter /
    police_complaint / legal_notice skip the drafting index entirely and use
    a synthetic skeleton; court_filing / tribunal_appellate / agreement_deed
    keep the current BM25 path.

    Falls back to "court_filing" on any error so production traffic never
    breaks — the worst case is the current behaviour.
    """
    try:
        with log_time(log, "Doc-type classification"):
            from langchain.chat_models import init_chat_model
            llm = init_chat_model(
                "google_genai:gemini-2.5-flash-lite",
                temperature=0.0,
            ).with_structured_output(DocTypeChoice, include_raw=True)
            prompt = ChatPromptTemplate.from_template(_DOC_TYPE_CLASSIFIER_PROMPT)
            chain = prompt | llm
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({
                    "query": query[:2000],
                    "case_context": (case_facts or "(none)")[:1500],
                }),
                timeout=10,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "classify_doc_type", raw_and_parsed.get("raw"))
        choice = raw_and_parsed["parsed"]
        doc_type = (choice.doc_type or "").strip().lower()
        if doc_type not in DOC_TYPES:
            log.warning("Doc-type classifier returned unknown value, falling back",
                        returned=doc_type)
            return "court_filing"
        log.info("Doc-type classified",
                 doc_type=doc_type,
                 reasoning=(choice.reasoning or "")[:160])
        return doc_type
    except Exception as e:
        log.warning("Doc-type classifier failed -- falling back to court_filing",
                    error=str(e)[:200])
        return "court_filing"


# --- Step 0.6: Synthetic skeletons for non-court doc types ---
#
# When the classifier picks office_letter / police_complaint / legal_notice,
# the drafting index has no letter-shaped templates to feed the outline LLM.
# Instead, we hand it a short markdown skeleton lifted from Layout B/D/F (the
# format-block in court_details of `DraftOutline`). The outline LLM imitates
# this structure — body-only, no court scaffolding, no Prayer.

_OFFICE_LETTER_SKELETON = """**<APPLICATION TITLE>**

Date: <date>

To,

<Recipient Designation, e.g. "The Public Information Officer", "The
Tahsildar", "The Sub-Divisional Magistrate", "The Branch Manager">,

<Name of Office / Department>,

<Office Address>.

**Subject: <Subject line — what this application is about, e.g.
"Application for information under the Right to Information Act, 2005" /
"Application for issuance of income certificate">**

Sir / Madam,

I, <Applicant Full Name>, son / daughter / wife of <Father / Husband Name>,
aged <age> years, residing at <Applicant Address>, respectfully submit as
follows:

1. <Fact / ground 1 — who I am and why I am writing>

2. <Fact / ground 2 — the specific rule / section / entitlement invoked
   (e.g. Section 6 RTI Act, 2005; State Government circular dated <date>;
   service rule)>

3. <Fact / ground 3 — supporting documents enclosed>

I therefore request you to kindly <issue the certificate / furnish the
information / grant the leave / accord approval>.

Thanking you,

Yours faithfully,

<Applicant Name>
"""

_POLICE_COMPLAINT_SKELETON = """**<COMPLAINT TITLE>**

(Examples: "POLICE COMPLAINT", "APPLICATION FOR REGISTRATION OF FIR UNDER
SECTION 154 OF THE CODE OF CRIMINAL PROCEDURE, 1973", "COMPLAINT UNDER
SECTION 173 OF THE BHARATIYA NAGARIK SURAKSHA SANHITA, 2023")

Date: <date>

To,

The Police Inspector / Station House Officer,

<Name of Police Station>,

<Address of Police Station>.

**Subject: <Subject line, e.g. "Complaint regarding [offence] committed on
[date] by [accused name]">**

Sir / Madam,

I, <Complainant Full Name>, son / daughter / wife of <Father / Husband
Name>, aged <age> years, occupation <occupation>, residing at <Complainant
Address>, do hereby state and submit as follows:

1. <Sequence of events — what happened, when, where>

2. <Identity of accused — name, description, address if known>

3. <Offences invoked — sections of BNS / IPC, brief reasoning>

4. <Witnesses, if any>

5. <Documents / evidence enclosed>

I therefore request your good office to register an FIR and investigate
the matter at the earliest.

Thanking you,

Yours faithfully,

<Complainant Name>
"""

_LEGAL_NOTICE_SKELETON = """**<NOTICE TITLE>**

(Examples: "LEGAL NOTICE", "REPLY TO LEGAL NOTICE", "NOTICE UNDER SECTION
138 OF THE NEGOTIABLE INSTRUMENTS ACT, 1881")

Date: <date>

To,

<Recipient Name>

<Recipient Qualifications / Designation if known>

<Recipient Address>

**Subject: <Subject line, e.g. "Notice under Section 138 of the Negotiable
Instruments Act, 1881 / Reply to Legal Notice dated DD/MM/YYYY">**

Sir / Madam,

Under instructions from and on behalf of my client, <Client Full Name>,
<Client Description / Designation>, residing at <Client Address>, I hereby
serve upon you the following notice:

1. <Facts / background of the transaction or dispute>

2. <Statutory / contractual basis for the claim>

3. <Specific demand: pay X amount within 15 days / cease and desist /
   vacate premises / reply to specific allegations>

4. <Consequence on non-compliance: civil suit / criminal complaint /
   eviction proceedings>

Take notice accordingly.

Yours faithfully,

<Advocate Name>
Advocate for <Client>
"""

SYNTHETIC_SKELETONS = {
    "office_letter": _OFFICE_LETTER_SKELETON,
    "police_complaint": _POLICE_COMPLAINT_SKELETON,
    "legal_notice": _LEGAL_NOTICE_SKELETON,
}

# Doc types whose templates are NOT in our drafting index at all (per the
# 2026-06-20 census). For these, skip ES search and feed the synthetic
# skeleton above directly to the outline LLM.
DOC_TYPES_USE_SKELETON = frozenset(SYNTHETIC_SKELETONS.keys())


# --- Step 1: Hybrid Template Search (BM25 + kNN, Python-side RRF merge) ---

async def _search_templates(query: str) -> list[dict]:
    """Search drafting index with BM25 keyword matching.

    Returns top 15 candidates sorted by relevance.
    """
    es = get_es_client()
    index = ES_INDICES["drafting"]
    sanitized = _sanitize_es_input(query)

    bm25_query = {
        "size": 20,
        "query": {"match": {"page_content": sanitized}},
        "_source": ["source", "page_content"],
    }

    with log_time(log, "BM25 template search"):
        response = await asyncio.to_thread(
            es.search, index=index, body=bm25_query,
        )
        return response["hits"]["hits"][:15]


# --- Step 1.5: Extract key facts from uploaded document for fact-grounded drafting ---
#
# When the user has attached a document (PDF, DOCX) or pasted long context, the
# raw 30K-char text is too long for parallel section-generation LLMs to reliably
# extract specific facts (names, amounts, dates) — they default to placeholders.
# So we run ONE fast Gemini Flash call up front to pull the structured entities
# and prepend them to every per-section prompt as a non-negotiable list.
#
# This is the key fix for BUG-02 (drafting agent ignores PDF facts).

_CASE_FACTS_PROMPT = """You are a legal entity extractor. Read the user-provided
document/context and produce a CONCISE list of the case-specific entities the
drafter MUST use verbatim. Pull only what is present; do NOT invent.

INPUT:
{facts_text}

Return a Markdown bullet list with these labels (omit any that are absent):
- **Court**: full court name as stated
- **Case Number**: case/suit number as stated
- **Plaintiff**: full name(s) — first occurrence's wording
- **Plaintiff Address**: as stated (one line)
- **Defendant**: full name(s)
- **Defendant Address**: as stated (one line)
- **Cause of Action / Claim**: 1-line summary (e.g. "recovery of Rs. 10L friendly loan")
- **Principal Amount**: with figure and words as stated
- **Interest Rate**: as claimed
- **Key Date - Loan/Agreement**: DD-Mon-YYYY
- **Key Date - Demand/Notice**: DD-Mon-YYYY
- **Key Date - Cause of Action accrual**: DD-Mon-YYYY
- **Witnesses**: comma-separated names
- **Statutory Provisions invoked**: e.g. "Order VII Rule 1 CPC"
- **Counsel**: as stated
- **Filing/Verification Date**: DD-Mon-YYYY
- **Other key facts**: any other specific details (sections, addresses, IDs)

Output ONLY the bullet list. No preamble, no explanations.
"""


async def _extract_case_facts(user_facts: str) -> str:
    """Pull structured entities from the user-provided document/context.

    Returns a markdown bullet list of case-specific facts (names, amounts, dates,
    court, etc.) that the section-generation LLMs must use verbatim. Used to
    prevent placeholder leakage when the underlying PDF text is long enough that
    parallel LLM calls might skim past specific values.

    Returns "" on failure (caller should treat as no extracted facts).
    """
    if not user_facts.strip():
        return ""
    try:
        with log_time(log, "Case-fact extraction"):
            llm = get_gemini_flash(temperature=0.0)
            prompt = ChatPromptTemplate.from_template(_CASE_FACTS_PROMPT)
            chain = prompt | llm
            response = await asyncio.wait_for(
                chain.ainvoke({"facts_text": user_facts[:30000]}),
                timeout=20,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "extract_case_facts", response)
        extracted = response.content.strip()
        log.info("Case facts extracted",
                 chars=len(extracted), bullets=extracted.count("- **"))
        return extracted
    except Exception as e:
        log.warning("Case-fact extraction failed; falling back to raw text",
                    error=str(e)[:200])
        return ""


# --- Step 2: Template Selection (GPT-4o-mini with previews + validation) ---

TEMPLATE_SELECTION_PROMPT = """You are a legal AI assistant selecting the best legal document template for an Indian law drafting task.

User wants to draft: {query}

UPSTREAM DOC-TYPE CLASSIFICATION (from a separate classifier — trust this when picking a candidate's family):
{doc_type_directive}

{facts_summary}Select the MOST relevant template based on (a) the user's instruction, (b) the case context (when supplied above). The case context tells you what KIND of dispute/matter the user is dealing with (e.g. money recovery, divorce, bail, property dispute) — use that to pick a template whose document type matches the user's actual case, NOT just keyword overlap with the question.

Each candidate shows the file path and a content preview:

{candidates}

PICK THE FILE PATH of the best candidate AND grade the match honestly:

  - 'good': the candidate genuinely matches the user's specific doc type,
    statute, and subject. Use it as the structural reference confidently.
  - 'marginal': right family of documents, but the specific statute /
    section / forum isn't the same. Still imitable for structure; doctrine
    will carry the substance.
  - 'none': none of the candidates is actually a usable template for what
    the user asked. Return 'none' in any of these cases:
      * The picked candidate is in a DIFFERENT DOC-TYPE FAMILY from the
        upstream classification (e.g. classifier says 'court_filing' but
        the closest candidate is an agreement/deed/contract template, OR
        classifier says 'agreement_deed' but the closest candidate is a
        court pleading). Topic overlap (e.g. both mention "arbitration")
        is NOT enough — the family must match.
      * The user names a specific statute / section / forum and no
        candidate covers that statute or any close cousin of it.
      * A bail application template was picked for an arbitration
        petition request. A divorce petition template was picked for a
        company-law winding-up petition. A sale deed template was picked
        for a will. These are all 'none'.

Be honest. 'none' triggers a web search for the real template — that's
the correct outcome when the corpus genuinely lacks the requested format.
Forcing a 'marginal' label onto a 'none' case produces a wrong-shaped
draft and is worse than admitting the gap."""


def _select_best_template(
    query: str, candidates: list[dict], user_facts: str = "",
    doc_type: str = "court_filing",
) -> tuple[str, list[str], str, str]:
    """Use Gemini Flash to select the most relevant template.

    Shows content previews alongside file paths for better selection.
    When `user_facts` is provided (PDF text, pasted context), a brief summary
    of the case context is included in the selection prompt so the LLM picks
    a template matching the user's actual case (BUG-05) — not just one whose
    preview shares keywords with the user's question.

    Returns (selected_source, all_valid_paths, match_quality, reason).
    `match_quality` is one of 'good' / 'marginal' / 'none' and drives the
    web-fallback decision in `drafting_node`.
    """
    valid_paths = list(dict.fromkeys(c["_source"]["source"] for c in candidates))

    # Brief context from user_facts (first 1500 chars). Plenty for the LLM to
    # spot the case-type signals — court name, party titles, claim, statutory
    # references — without bloating the selection prompt.
    facts_summary = ""
    if user_facts.strip():
        facts_summary = (
            f"USER CASE CONTEXT (from uploaded document or pasted content — "
            f"use this to identify the case type, not the template's example):\n"
            f"{user_facts.strip()[:1500]}\n\n"
        )

    # Short directive for the upstream doc-type classification. The
    # selector uses this to refuse cross-family picks (e.g. agreement
    # template selected for a court_filing request → grade as 'none').
    doc_type_directive = (
        f"The upstream classifier graded the user's request as **{doc_type}**. "
        f"If the only candidates the corpus offers are clearly in a different "
        f"doc-type family (e.g. private agreements/deeds when {doc_type} is "
        f"court_filing, or court pleadings when {doc_type} is agreement_deed), "
        f"grade 'none' regardless of topic overlap."
    )

    with log_time(log, "Template selection (LLM)"):
        # Build candidate list with previews
        candidate_lines = []
        for i, c in enumerate(candidates, 1):
            path = c["_source"]["source"]
            preview = c["_source"]["page_content"][:200].replace("\n", " ")
            candidate_lines.append(f"{i}. {path}\n   Preview: {preview}...")

        llm = get_gemini_flash(temperature=0.1).with_structured_output(
            TemplateSource, include_raw=True,
        )
        prompt = ChatPromptTemplate.from_template(TEMPLATE_SELECTION_PROMPT)
        chain = prompt | llm
        raw_and_parsed = chain.invoke({
            "query": query,
            "candidates": "\n".join(candidate_lines),
            "facts_summary": facts_summary,
            "doc_type_directive": doc_type_directive,
        })
    from core.token_tracker import record as _record_tokens
    _record_tokens("Drafting", "select_template", raw_and_parsed.get("raw"))
    result = raw_and_parsed["parsed"]

    selected = result.source.strip()
    match_quality = (result.match_quality or "good").strip().lower()
    if match_quality not in ("good", "marginal", "none"):
        log.warning("Template selector returned unknown match_quality, defaulting to 'marginal'",
                    returned=match_quality)
        match_quality = "marginal"
    reason = (result.reason or "")[:300]

    # Validate: ensure selected path exists in candidates
    if selected not in valid_paths:
        # Fuzzy match
        matches = [p for p in valid_paths if selected in p or p in selected]
        if matches:
            log.warning("Template path fuzzy-matched",
                        returned=selected, matched=matches[0])
            selected = matches[0]
        else:
            log.warning("Template path not found, using top candidate",
                        returned=selected)
            selected = valid_paths[0]

    log.info("Template selected",
             template=selected[-60:],
             match_quality=match_quality,
             reason=reason[:160])

    return selected, valid_paths, match_quality, reason


# --- Step 2.5: Web-template fallback (when corpus has no match) ---
#
# Fires only when the template-selector LLM grades all candidates as 'none'.
# Same Gemini 2.5 Flash + Google Search grounding primitive used by other
# domain agents (see core/agent_fallback.py), but specialized for fetching a
# document format/template rather than a substantive legal answer.
#
# The output is a markdown skeleton that the outline LLM can imitate the
# same way it imitates a corpus template. A separate LLM validator
# (`_validate_web_template`) gates whether the web result is actually a
# usable template or just a prose explanation / refusal — without the
# validator we'd happily feed "I cannot find a template" into the outline
# LLM and produce garbage. On any failure the caller falls back to a
# doc-type-specific generic skeleton (see `GENERIC_COURT_SKELETONS`).

_WEB_TEMPLATE_FETCH_PROMPT = """You are a senior Indian-law draftsman. The
user wants a draft format that is NOT in our local template library, so we
need to fetch a real one from authoritative legal sources on the open web.

USER REQUEST:
{query}

CLASSIFIED DOC TYPE: {doc_type}

YOUR TASK:
1. Use web search to find authentic Indian-law sample formats / templates
   for this specific request. Prefer authoritative sources: court websites
   (delhihighcourt.nic.in, bombayhighcourt.nic.in, etc.), legal databases
   (indiankanoon.org, scconline.com, livelaw.in, barandbench.com,
   manupatra.com), and reputed legal blogs (lawbhoomi.com,
   advocatekhoj.com). AVOID generic SEO-template sites that sell drafts.
2. Synthesize the BEST TEMPLATE FORMAT for the user's specific request.
   The format must follow ESTABLISHED Indian-law conventions for that
   doc type — correct forum / cause-title / statute citation / section
   structure / prayer style (or "request" style for letters) / verification.
3. Emit the template as a MARKDOWN SKELETON. Use `<PLACEHOLDER>` style
   blanks for the user-specific values (`<Petitioner Name>`,
   `<Address>`, `<Date>`, `<Section X>`, `<Forum>`, etc.). Do NOT invent
   facts; this is a TEMPLATE, not a filled-in draft.
4. Cover, in order: title block, cause-title / addressee block, opening
   paragraph, body sections (numbered, with headers), prayer / request
   / closing, verification or signature block — whatever the doc type
   conventionally carries.

OUTPUT FORMAT:
Emit ONLY the markdown template body. No preamble, no "Here is the
template:", no closing commentary. The first character of your response
must be the title block of the template (e.g. `**IN THE COURT OF ...**`
or `**APPLICATION FOR ...**`).

If you cannot find authoritative source material for this specific
template, emit the literal string `NO_TEMPLATE_FOUND` and nothing else.
We have a fallback for that case."""


async def _fetch_template_from_web(
    query: str,
    doc_type: str,
    timeout_sec: float = 45.0,
) -> tuple[str, list[str]]:
    """Fetch a template skeleton from the open web using Gemini + Google Search.

    Returns (markdown_skeleton, grounding_urls). On failure or
    NO_TEMPLATE_FOUND, returns ("", []).
    """
    from core.clients import get_genai_client
    from core.settings import MODELS
    log.info("Web template fetch started",
             query=query[:120], doc_type=doc_type)
    try:
        client = get_genai_client()
        prompt_text = _WEB_TEMPLATE_FETCH_PROMPT.format(
            query=query[:2000], doc_type=doc_type,
        )

        _primary = MODELS["scenario_web_grounded"]
        response = None
        for model in [_primary, "gemini-2.5-pro"]:
            try:
                with log_time(log, f"Web template fetch ({model})"):
                    response = await asyncio.wait_for(
                        asyncio.to_thread(
                            client.models.generate_content,
                            model=model,
                            contents=[prompt_text],
                            config={
                                "tools": [{"google_search": {}}],
                                "max_output_tokens": 6000,
                                "temperature": 0.3,
                                "top_p": 0.9,
                            },
                        ),
                        timeout=timeout_sec,
                    )
                break
            except Exception as model_err:
                if "503" in str(model_err) and model == _primary:
                    log.warning("Web template fetch — Flash 503, retrying Pro",
                                error=str(model_err)[:120])
                    await asyncio.sleep(1)
                    continue
                raise

        if response is None:
            return "", []
        if not response.candidates or not response.candidates[0].content.parts:
            log.warning("Web template fetch returned empty content")
            return "", []

        text = (response.candidates[0].content.parts[0].text or "").strip()

        # Strip optional code fences the model sometimes wraps templates in
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        if not text or text == "NO_TEMPLATE_FOUND":
            log.info("Web template fetch — model declined / no source",
                     doc_type=doc_type)
            return "", []

        # Pull grounding URLs for attribution
        grounding_urls: list[str] = []
        try:
            candidate = response.candidates[0]
            grounding_meta = getattr(candidate, "grounding_metadata", None)
            if grounding_meta:
                seen = set()
                for chunk in getattr(grounding_meta, "grounding_chunks", None) or []:
                    web = getattr(chunk, "web", None)
                    if web:
                        url = getattr(web, "uri", None)
                        if url and url not in seen:
                            seen.add(url)
                            grounding_urls.append(url)
        except Exception as ge:
            log.debug("Web template fetch — grounding extraction failed",
                      error=str(ge)[:120])

        log.info("Web template fetched",
                 doc_type=doc_type,
                 chars=len(text),
                 sources=len(grounding_urls))
        return text, grounding_urls

    except Exception as e:
        log.warning("Web template fetch failed; caller will use synthetic skeleton",
                    error=str(e)[:200])
        return "", []


# --- Step 2.6: Web-template validator (LLM, no regex) ---
#
# The web fetcher may return a refusal ("I couldn't find..."), a prose
# explanation ("In India, a bail application is..."), or an actual
# template. We need to distinguish before feeding the result to the
# outline LLM. Pure-LLM judgment — no regex / structural heuristics — so
# this scales to any future doc type without code changes.

# --- Step 2.55: Family-mismatch verifier (LLM, escalates marginal → none) ---
#
# The template-selector LLM sometimes rationalizes topical overlap into
# 'marginal' when the picked template is actually in a different doc-type
# family from what the user asked for (e.g. arbitration-AGREEMENT template
# selected for an arbitration-COURT-APPLICATION request). The verifier
# runs ONLY on 'marginal' picks, gets a closer look at the selected
# template's actual body, and escalates to 'none' (triggers web fetch)
# when the family is wrong. 'good' picks skip this — they're already
# confirmed; 'none' picks skip this — they already trigger web fetch.

class _FamilyCheckVerdict(BaseModel):
    is_correct_family: bool = Field(
        ...,
        description=(
            "True ONLY if the selected template is in the SAME doc-type "
            "family as the user's request (both court filings; both "
            "agreements; both letters; etc.). False when the family is "
            "wrong — e.g. a private arbitration agreement was picked for "
            "a Section 9 A&C Act court application; a divorce petition "
            "was picked for a company-law winding-up petition. Topic "
            "overlap is NOT enough — the family must match."
        ),
    )
    reason: str = Field("", description="One short sentence justifying the verdict.")


_FAMILY_CHECK_PROMPT = """The template-selector LLM graded the chosen template
as 'marginal'. Verify whether the selected template is in the SAME doc-type
family as the user's request, or whether it's in a DIFFERENT family
(family-mismatch → escalate to 'none' → web fetch).

USER REQUEST:
{query}

UPSTREAM CLASSIFICATION (doc-type family the user actually asked for):
{doc_type}

SELECTED TEMPLATE PREVIEW (first 1500 chars of the template body):
{template_preview}

SELECTOR'S REASON FOR PICKING IT:
{selector_reason}

A doc-type family is one of:
  - court_filing (plaints, petitions, written statements, court applications,
    bail, IA, magistrate complaints, writs, SLPs)
  - tribunal_appellate (CIT(A), ITAT, GST appellate, NCLT written submissions)
  - office_letter (RTI, certificate request, leave / NOC letters to admin authority)
  - police_complaint (letter to SHO for FIR)
  - legal_notice (Section 138 NI Act, demand notice, reply to legal notice)
  - agreement_deed (agreement, MOU, deed, lease, sale, gift, will)
  - affidavit (standalone sworn statement)

EXAMPLES OF FAMILY-MISMATCH (return is_correct_family=false):
  - User wants a Section 9 A&C Act court application (court_filing). Template
    is an "Agreement of Reference to Arbitrator" (agreement_deed). Topic
    overlaps (arbitration) but family is WRONG.
  - User wants a winding-up petition under Companies Act (court_filing).
    Template is a "Memorandum of Association" (agreement_deed). Topic overlaps
    (company law) but family is WRONG.
  - User wants an RTI application (office_letter). Template is a "Section
    200 CrPC complaint" (court_filing). Wrong family.

Return is_correct_family=true ONLY when the user's family and the template's
family are the same.
"""


async def _verify_template_family(
    query: str,
    doc_type: str,
    template_preview: str,
    selector_reason: str,
) -> tuple[bool, str]:
    """Second-pass check for 'marginal' template picks. Returns
    (is_correct_family, reason). On failure → returns (True, ...) — keep
    the corpus pick if the verifier itself errors. We never silently
    escalate to web on a verifier error.
    """
    if not template_preview.strip():
        return True, "empty preview"
    try:
        with log_time(log, "Template family verification"):
            from langchain.chat_models import init_chat_model
            llm = init_chat_model(
                "google_genai:gemini-2.5-flash-lite",
                temperature=0.0,
            ).with_structured_output(_FamilyCheckVerdict, include_raw=True)
            prompt = ChatPromptTemplate.from_template(_FAMILY_CHECK_PROMPT)
            chain = prompt | llm
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({
                    "query": query[:1500],
                    "doc_type": doc_type,
                    "template_preview": template_preview[:1500],
                    "selector_reason": (selector_reason or "")[:400],
                }),
                timeout=12,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "verify_template_family",
                       raw_and_parsed.get("raw"))
        verdict = raw_and_parsed["parsed"]
        log.info("Template family verified",
                 doc_type=doc_type,
                 is_correct_family=verdict.is_correct_family,
                 reason=(verdict.reason or "")[:160])
        return bool(verdict.is_correct_family), (verdict.reason or "")
    except Exception as e:
        log.warning("Template family verification failed -- keeping corpus pick",
                    error=str(e)[:200])
        return True, f"verifier error: {str(e)[:120]}"


class _WebTemplateVerdict(BaseModel):
    is_usable_template: bool = Field(
        ...,
        description=(
            "True ONLY if the input is a structured legal draft format/"
            "template that follows established Indian-law conventions for "
            "the named doc type — has a cause-title or addressee block, "
            "numbered body sections, and a closing convention. False for "
            "prose explanations, model refusals, blog posts, partial "
            "fragments, or templates that are clearly for a different "
            "doc type than what the user asked for."
        ),
    )
    reason: str = Field("", description="One short sentence justifying the verdict.")


_WEB_TEMPLATE_VALIDATOR_PROMPT = """You are quality-checking a TEMPLATE we
pulled from the open web before using it to draft an Indian-law document.

USER REQUEST:
{query}

EXPECTED DOC TYPE (from upstream classifier):
{doc_type}

WEB-FETCHED CONTENT (first 4000 chars):
{template_text}

Verdict: is this a USABLE template? A usable template:
  - is a structured draft FORMAT (markdown skeleton, placeholders for
    user-specific values), NOT a prose explanation of how to draft
    something.
  - follows established Indian-law conventions for the expected doc type
    (court cause-title for a court_filing; To, addressee + Subject for an
    office_letter; appellate "BEFORE THE HON'BLE" + Vs. for tribunal_
    appellate; etc.).
  - covers the document end-to-end: opening block + body sections +
    closing/prayer/request/verification — NOT a fragment.
  - matches the user's specific doc type. A bail-application template
    when the user asked for an arbitration petition is NOT usable.

Return is_usable_template=true ONLY if all four hold. Otherwise false.
Be honest — false triggers a synthetic-skeleton fallback that produces
a clean (if generic) draft, which is better than a wrong-shaped one.
"""


async def _validate_web_template(
    template_text: str, doc_type: str, query: str,
) -> tuple[bool, str]:
    """Ask Flash-Lite whether the web-fetched content is actually a usable
    template. Returns (is_usable, reason). On any failure → (False, ...) so
    the caller falls back to the generic skeleton.
    """
    if not template_text.strip():
        return False, "empty template"
    try:
        with log_time(log, "Web template validation"):
            from langchain.chat_models import init_chat_model
            llm = init_chat_model(
                "google_genai:gemini-2.5-flash-lite",
                temperature=0.0,
            ).with_structured_output(_WebTemplateVerdict, include_raw=True)
            prompt = ChatPromptTemplate.from_template(_WEB_TEMPLATE_VALIDATOR_PROMPT)
            chain = prompt | llm
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({
                    "query": query[:2000],
                    "doc_type": doc_type,
                    "template_text": template_text[:4000],
                }),
                timeout=15,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "validate_web_template",
                       raw_and_parsed.get("raw"))
        verdict = raw_and_parsed["parsed"]
        log.info("Web template validated",
                 is_usable=verdict.is_usable_template,
                 reason=(verdict.reason or "")[:160])
        return bool(verdict.is_usable_template), (verdict.reason or "")
    except Exception as e:
        log.warning("Web template validation failed -- treating as unusable",
                    error=str(e)[:200])
        return False, f"validation error: {str(e)[:120]}"


# --- Step 2.7: Generic court skeletons (terminal fallback) ---
#
# When the corpus has no match AND the web fetch fails / is unusable, we
# still produce a usable draft by feeding the outline LLM one of these
# generic court skeletons — picked by doc_type. They are intentionally
# bare-bones (just enough scaffolding for the outline LLM to produce a
# coherent structure) so the LLM has to do the doctrinal work itself.

_GENERIC_COURT_FILING_SKELETON = """**IN THE HON'BLE COURT OF <FORUM>, AT <CITY>**

**<CASE TYPE> NO. _______ OF <YEAR>**

**IN THE MATTER OF:**

<Petitioner / Applicant Full Name>

Age: <age>, Occupation: <occupation>

R/ Address: <residential address>

.....Petitioner / Applicant

**Versus**

<Respondent Full Name>

Age: <age>, Occupation: <occupation>

R/ Address: <residential address>

.....Respondent

**<SUBJECT HEADING — describe the specific application/petition>**

MOST RESPECTFULLY SHOWETH:

1. <Identity and standing of the petitioner / applicant>

2. <Brief facts giving rise to the cause of action, chronological>

3. <Statutory / jurisdictional basis for invoking this court — cite the
   exact section, rule, or article>

4. <Substantive grounds, one per paragraph>

5. <Cause of action and limitation>

6. <Jurisdiction (territorial + pecuniary) with statutory citations>

**PRAYER:**

It is therefore most respectfully prayed that this Hon'ble Court may be
pleased to:

(a) <Primary relief sought, with statutory citation>

(b) <Secondary / interim relief, if any>

(c) <Costs of the proceeding>

(d) Pass such other and further orders as this Hon'ble Court may deem
fit and proper in the facts and circumstances of the case, in the
interest of justice.

**VERIFICATION:**

I, <Petitioner Name>, the Petitioner above-named, do hereby verify that
the contents of paragraphs <X> to <Y> are true to my personal knowledge,
those of paragraphs <X> to <Y> are based on legal advice received which
I believe to be true, and nothing material has been concealed therefrom.

Verified at <City> on this <Date>.

Petitioner
"""

_GENERIC_TRIBUNAL_APPELLATE_SKELETON = """**BEFORE THE HON'BLE <APPELLATE FORUM>, <JURISDICTION>**

**Appeal No. <Number>  |  Assessment Year: <AY> / Period: <Period>**

**IN THE MATTER OF:**

<Appellant Full Name>  PAN/GSTIN: <ID>

(Appellant)

Vs.

<Respondent Designation, e.g. "The Assessing Officer / Joint Commissioner">

(Respondent)

**Subject: Written Submission in respect of Appeal against the
<Impugned Order Type> dated <DD.MM.YYYY> passed under Section <X> of the
<Statute Name>.**

Most Respectfully Showeth:

The Appellant files this written submission in support of the grounds of
appeal raised against the impugned <assessment order / adjudication order>.

## 1. STATEMENT OF FACTS OF THE CASE

1. <Chronological narrative of how the impugned order was passed,
   8-12 numbered paragraphs.>

## 2. GROUNDS OF APPEAL (as filed in Form 35)

<List each ground as filed; one short paragraph per ground.>

## 3. DETAILED WRITTEN SUBMISSION

**Re: Ground No. 1 — <ground title>**

<Detailed legal argumentation, full citation of supporting case laws,
rebuttal of the lower authority's reasoning, distinguishing case laws
relied upon.>

**Re: Ground No. 2 — <ground title>**

<As above.>

## 4. PRAYER

It is respectfully prayed that the Hon'ble <Forum> may be pleased to:

(a) Allow the appeal in its entirety.

(b) Annul / set aside the impugned order dated <date>.

(c) Delete the additions / disallowances / penalties.

(d) Pass any other order that the Hon'ble Forum deems fit in the
interest of justice.

## 5. REQUEST FOR VIDEO CONFERENCING HEARING

The Appellant respectfully prays for an opportunity of personal hearing
through video conferencing before final disposal of this appeal.

Place: <City>

Date: <Date>

<Appellant Name>
"""

_GENERIC_AGREEMENT_SKELETON = """**<AGREEMENT TITLE>**

**THIS <AGREEMENT / DEED / MOU / WILL> IS MADE AND EXECUTED ON THIS <date>**

**BETWEEN**

<First Party Full Name>

Age: <age>, Occupation: <occupation>

R/ Address: <residential address>

(hereinafter referred to as the "<First Party Role>")

**AND**

<Second Party Full Name>

Age: <age>, Occupation: <occupation>

R/ Address: <residential address>

(hereinafter referred to as the "<Second Party Role>")

**WHEREAS:**

A. <Recital 1 — background fact relevant to the agreement>

B. <Recital 2>

C. <Recital 3>

**NOW THIS DEED WITNESSETH AS FOLLOWS:**

## 1. DEFINITIONS AND INTERPRETATION

<Terms used in the agreement with their defined meanings.>

## 2. SUBJECT MATTER

<Description of the property / service / obligation being transacted.>

## 3. CONSIDERATION

<Amount / mode / time of payment of consideration.>

## 4. COVENANTS AND OBLIGATIONS

<Specific covenants undertaken by each party.>

## 5. REPRESENTATIONS AND WARRANTIES

<Each party's representations as to capacity, title, and authority.>

## 6. TERM AND TERMINATION

<Duration of the agreement and circumstances of termination.>

## 7. DISPUTE RESOLUTION AND GOVERNING LAW

This agreement shall be governed by and construed in accordance with
the laws of India. Any dispute arising out of or in connection with
this agreement shall be referred to arbitration / the courts at <city>.

## 8. INDEMNITY

<Indemnification clauses.>

## 9. EXECUTION

IN WITNESS WHEREOF the parties hereto have set their hands on the day,
month and year first above written.

<First Party Signature>                  <Second Party Signature>

WITNESSES:

1. <Witness 1 Name and Signature>

2. <Witness 2 Name and Signature>
"""

_GENERIC_AFFIDAVIT_SKELETON = """**AFFIDAVIT**

I, <Deponent Full Name>, son / daughter / wife of <Father / Husband Name>,
aged <age> years, occupation <occupation>, residing at <Deponent Address>,
do hereby solemnly affirm and declare as under:

1. <Identification of the deponent and standing to swear this affidavit>

2. <Fact 1 being sworn to>

3. <Fact 2 being sworn to>

4. <Fact 3 being sworn to — add as many numbered paragraphs as the facts require>

5. I state that the contents of the above paragraphs are true to my
personal knowledge and nothing material has been concealed therefrom.

Verified at <City> on this <Date> day of <Month>, <Year> that the
contents of the above affidavit are true and correct to the best of my
knowledge and belief.

<Deponent Signature>

DEPONENT

Attested before me on <Date>:

<Notary Public / Oath Commissioner>
"""

# Maps doc_type → generic skeleton for terminal fallback.
# office_letter / police_complaint / legal_notice already use the
# (different) SYNTHETIC_SKELETONS — those are the FIRST-LINE fallback for
# doc types our corpus never covers. GENERIC_COURT_SKELETONS is the
# LAST-LINE fallback for the remaining doc types when corpus AND web both
# fail.
GENERIC_COURT_SKELETONS = {
    "court_filing": _GENERIC_COURT_FILING_SKELETON,
    "tribunal_appellate": _GENERIC_TRIBUNAL_APPELLATE_SKELETON,
    "agreement_deed": _GENERIC_AGREEMENT_SKELETON,
    "affidavit": _GENERIC_AFFIDAVIT_SKELETON,
}


def _generic_skeleton_for(doc_type: str) -> str:
    """Return the terminal-fallback skeleton for `doc_type`.

    First checks the office-letter-family synthetic skeletons (the gate from
    the previous commit), then the generic court skeletons. Returns the
    `court_filing` skeleton as a defensive default if `doc_type` is unknown.
    """
    return (
        SYNTHETIC_SKELETONS.get(doc_type)
        or GENERIC_COURT_SKELETONS.get(doc_type)
        or _GENERIC_COURT_FILING_SKELETON
    )


# --- Step 3: Generate Document Outline ---

async def _generate_outline(
    query: str, template_text: str, user_language: str = "en",
    user_facts: str = "", case_facts: str = "",
    format_block: str = "",
    template_language: str | None = None,
    doc_type: str = "court_filing",
    intent=None,
) -> DraftOutline:
    """Generate a structured outline with all sections for the document.

    Uses Gemini 2.5 Flash with structured output for reliable section list.

    When `case_facts` (structured bullets) and/or `user_facts` (raw text) are
    non-empty, the outline is tailored to those specific facts so e.g. a
    "Suit For Recovery Of Money" outline knows to include sections referring
    to the actual loan amount and dates.

    `format_block` carries layout/typographic conventions extracted from the
    chosen template (see _extract_format_spec). Empty string disables the
    block; the outline still works on template_text alone.

    `doc_type` (one of `DOC_TYPES`) selects the doc-type-specific rule branch
    from `DRAFT_OUTLINE_RULES_BY_TYPE` that gates whether Prayer / Verification
    / cause-title are included. For office_letter / police_complaint /
    legal_notice this drops them entirely.
    """
    # Import here to avoid circular import at module load time.
    from config.prompts import DRAFT_OUTLINE_RULES_BY_TYPE, DRAFT_OUTLINE_RULES_COURT_FILING
    doc_type_rules = DRAFT_OUTLINE_RULES_BY_TYPE.get(
        doc_type, DRAFT_OUTLINE_RULES_COURT_FILING,
    )
    facts_block = ""
    if case_facts.strip() or user_facts.strip():
        parts = [
            "USER-PROVIDED FACTS — the outline must be tailored to THIS "
            "specific case, not the generic template scenario:"
        ]
        if case_facts.strip():
            parts.append("\nKEY ENTITIES:\n" + case_facts.strip())
        if user_facts.strip():
            parts.append("\nFULL DOCUMENT TEXT:\n" + user_facts[:25000])
        facts_block = "\n".join(parts) + "\n\n"

    # Detect the script of the user-supplied facts so localize_prompt can
    # escalate to the STRICT directive when the response language differs
    # from the source-document language (e.g. English target with Marathi
    # PDF facts). Without this, the drafting agent leaks short Marathi
    # phrases like "दस्त क्र." into otherwise-English replies even though
    # the prompt asks for English.
    _source_langs_outline = detect_source_languages(user_facts, case_facts)

    with log_time(log, "Outline generation"):
        llm = get_drafting_llm().with_structured_output(
            DraftOutline, include_raw=True,
        )
        # Substitute {doc_type_rules} into the system prompt BEFORE handing
        # off to ChatPromptTemplate; otherwise it would be treated as an
        # unfilled template variable. Use .replace (not .format) so any
        # incidental braces in the INDIAN_LEGAL_* blocks don't blow up.
        system_prompt = DRAFT_OUTLINE_PROMPT.replace(
            "{doc_type_rules}", doc_type_rules,
        )
        prompt = ChatPromptTemplate.from_messages([
            ("system", localize_prompt(
                system_prompt, user_language,
                intent=intent,
                source_languages=_source_langs_outline,
            )),
            ("user", "{facts_block}USER QUERY:\n{query}"),
            # Template-language warning, empty string when the chosen
            # template's body language matches user_language.
            ("user", "{template_lang_warning}"),
            ("user",
             "Reference Template (use ONLY for STRUCTURE/section names — "
             "do NOT copy the template's facts/parties/amounts):\n{template}"),
            # Layout-spec block: distilled visual conventions from the
            # template, separated from the full template_text so the LLM
            # sees a clear "imitate these layout patterns" signal divorced
            # from the fact-isolation warning. Empty string when extraction
            # failed -- prompt still works on template_text alone.
            ("user", "{format_block}Current Date: {date}"),
        ])
        chain = prompt | llm
        try:
            raw_and_parsed = await chain.ainvoke({
                "query": query,
                "template": template_text,
                "date": str(date.today()),
                "facts_block": facts_block,
                "format_block": format_block,
                "template_lang_warning": _build_template_language_warning(
                    template_language, user_language,
                ),
            })
        except Exception as e:
            log.error("Structured outline generation failed, using fallback",
                      error=str(e)[:200])
            # Fallback: 3-section outline (Facts/Arguments/Prayer)
            outline = DraftOutline(
                document_title="Legal Document",
                court_details="[Court Details]",
                sections=[
                    SectionPlan(
                        title="Facts and Background",
                        description=f"State the facts and background for: {query[:300]}",
                        estimated_paragraphs=8,
                        needs_citations=False,
                    ),
                    SectionPlan(
                        title="Legal Arguments and Grounds",
                        description=f"Present legal arguments and statutory grounds for: {query[:300]}",
                        estimated_paragraphs=10,
                        needs_citations=True,
                    ),
                    SectionPlan(
                        title="Prayer and Relief Sought",
                        description="State the relief sought and prayer to the court.",
                        estimated_paragraphs=3,
                        needs_citations=False,
                    ),
                ],
            )
            raw_and_parsed = None  # no token usage to record on fallback path
        else:
            from core.token_tracker import record as _record_tokens
            _record_tokens("Drafting", "outline", raw_and_parsed.get("raw"))
            outline = raw_and_parsed["parsed"]

    # Remove meta-sections that don't need LLM generation
    META_SECTION_KEYWORDS = ("index", "table of contents", "contents page")
    filtered = [s for s in outline.sections
                if not any(kw in s.title.lower() for kw in META_SECTION_KEYWORDS)]
    if len(filtered) < len(outline.sections):
        removed = [s.title for s in outline.sections if s not in filtered]
        log.info("Removed meta-sections from outline", removed=removed)
        outline.sections = filtered

    # NOTE: Procedural-section injection moved out of outline generation.
    # It now runs AFTER the doctrinal stance is generated (which enumerates
    # the procedural sections the pleading must carry) -- see
    # `_inject_procedural_sections` and its call after `_generate_doctrinal_stance`
    # in `drafting_node`.

    # Cap sections (aligned with prompt: bail 10-12, suits 12-15)
    if len(outline.sections) > _MAX_SECTIONS:
        log.warning("Outline has too many sections, capping",
                     original=len(outline.sections), capped=_MAX_SECTIONS)
        outline.sections = outline.sections[:_MAX_SECTIONS]

    log.info("Outline generated",
             title=outline.document_title[:80],
             sections=len(outline.sections),
             total_paragraphs=sum(s.estimated_paragraphs for s in outline.sections))
    return outline


# --- Pre-return validator ---
#
# Runs over the fully assembled draft, fixes cheap mojibake / placeholder
# leakage in-place, and returns a list of structural warnings for callers
# (logged + surfaced in the AgentResult). Designed to never raise -- a
# validator bug must not block delivery of the draft.

# Common UTF-8 -> cp1252 -> UTF-8 round-trip artefacts in Indian legal text.
_MOJIBAKE_REPLACEMENTS = [
    ("â€“", "–"),   # â€" -> en dash
    ("â€”", "—"),   # â€" -> em dash
    ("â€˜", "‘"),   # â€˜ -> left single quote
    ("â€™", "’"),   # â€™ -> right single quote
    ("â€œ", "“"),   # â€œ -> left double quote
    ("â€", "”"),   # â€ -> right double quote
    ("â€¦", "…"),   # â€¦ -> ellipsis
    ("Â ",       " "),   # Â  -> nbsp
    ("ï¿½", "?"),        # replacement char (no-info fallback)
    (" â ", " – "),  # bare â between spaces -> en-dash (surviving fragment of
                          # truncated 3-byte UTF-8 dash E2 80 93/94;
                          # codec roundtrip cannot fix because 0xE2 alone
                          # is invalid UTF-8 lead. Symptom: "Pune â 412307".
]

# Internal LLM artefact placeholders that should never survive to the user.
# The section prompt forbids `[CITE: ...]` markers; this is the defensive
# fallback for the rare survivor. Substantive citation completeness checks
# (orphan tails, forbidden statute pairings, trailing prepositions) are now
# done by `core/self_refine.self_refine` against the typed DoctrinalStance.
_CITE_PLACEHOLDER_RE = re.compile(r"\[CITE:[^\]]*\]", flags=re.IGNORECASE)

# Empty numbered paragraphs — a bare `N.` line with no body on its own line.
# Symptom: section LLM started a paragraph but emitted nothing for it before
# the next paragraph number. Match BOTH the paragraph marker and the
# preceding/trailing whitespace so removal doesn't leave double blank lines.
_EMPTY_NUMBERED_PARA_RE = re.compile(
    r"(?m)^\s*\d+\.\s*$\n?",
)

# Substantive numbered paragraph at the START of a line. Latin and Devanagari
# digits because Hindi / Marathi drafts use Devanagari numerals natively.
# We rewrite `N. ` → `<new>. ` where `<new>` is the position in the global
# substantive-paragraph counter.
_NUMBERED_PARA_LINE_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?P<num>[\d०-९]+)\.(?P<sep>[ \t]+)(?P<rest>\S)",
)

# Section heading inserted by the assembler. Matches both Latin and
# Devanagari digits — same range we used for paragraphs.
_SECTION_HEADING_RE = re.compile(
    r"(?m)^##\s+(?P<num>[\d०-९]+)\.\s+(?P<title>.+)$",
)

# Procedural section keywords — title hints that mean the section uses
# its own local numbering scheme rather than the global body counter.
# Mirrors `_PROCEDURAL_TITLE_KEYWORDS` in `_generate_sections_parallel`
# (drafting.py:3490) — keep these two lists in sync. They're separate
# because the renumber pass needs a free-standing constant.
_PROCEDURAL_HEADING_KEYWORDS_FOR_RENUMBER = (
    "prayer", "relief", "verification", "court fee", "court-fee",
    "schedule", "list of documents", "list of document",
    "affidavit", "memo of parties", "annexures index",
    "interim application", "ia under order",
)


def _renumber_global_paragraphs(full_draft: str) -> str:
    """Walk the assembled draft and rewrite paragraph numbers across
    substantive body sections so the global counter is continuous from 1.

    Procedural sections (Prayer, Verification, Schedule, Court Fee, List of
    Documents, Affidavit, etc.) keep whatever numbering scheme they have —
    typically (a)/(b)/(c) or 1-based local — because they aren't part of
    the global body counter. We detect procedural sections by title-keyword
    and skip them.

    Why this is needed: section generation runs in parallel and each
    section receives a precomputed `start_para_num` derived from the
    outline LLM's `estimated_paragraphs`. When actual paragraph count
    drifts from the estimate, every downstream section's start offset is
    wrong. Symptom in the Avachat writ: Sec 5 jumped to para 23 (expected
    ~17), Sec 9 jumped to 59 (expected 51), Sec 11 restarted at 74
    overlapping with Sec 10's last paragraph. The renumber pass walks the
    assembled output AFTER it's all in hand and rewrites the global
    counter deterministically — no LLM, no estimate, no drift.
    """
    lines = full_draft.split("\n")
    counter = 1
    # `outline.court_details` (the cause-title block) sits ABOVE the first
    # `## N. TITLE` heading. Its numbered lines are PARTY NUMBERS in the
    # plaintiffs / defendants list, NOT substantive body paragraphs.
    # Start in skip-mode so those party numbers don't get folded into the
    # global body counter — symptom otherwise was Manjri Greens WS body
    # opening at paragraph 11 because 8 plaintiffs + 2 defendants
    # consumed counter slots 1..10.
    in_procedural = True
    rewrote = 0

    for i, line in enumerate(lines):
        heading_m = _SECTION_HEADING_RE.match(line)
        if heading_m:
            title_lower = heading_m.group("title").lower()
            in_procedural = any(
                kw in title_lower
                for kw in _PROCEDURAL_HEADING_KEYWORDS_FOR_RENUMBER
            )
            continue

        if in_procedural:
            continue

        m = _NUMBERED_PARA_LINE_RE.match(line)
        if m:
            new_num = str(counter)
            new_line = (
                m.group("indent")
                + new_num
                + "."
                + m.group("sep")
                + m.group("rest")
                + line[m.end():]
            )
            if new_line != line:
                lines[i] = new_line
                rewrote += 1
            counter += 1

    if rewrote:
        log.info("Validator: renumbered global paragraphs",
                 rewrote=rewrote, final_counter=counter - 1)
    return "\n".join(lines)

# HTML tag cleanup. Gemini occasionally tries to fake centering/alignment in
# legal drafts by emitting <p align="center">TITLE</p>, <p align="right">...,
# or wrapping content in <div>/<span>/<center>. Our frontend renders markdown
# only — raw HTML shows up as ugly literal text. Strip every HTML-looking
# tag while preserving the inner content. Structural tags (<br>, <hr>) get
# converted to the markdown equivalent first so we don't lose line breaks.
_HTML_BR_RE = re.compile(r"<br\s*/?>", flags=re.IGNORECASE)
_HTML_HR_RE = re.compile(r"<hr\s*/?>", flags=re.IGNORECASE)
_HTML_ANY_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?/?>")


def validate_draft(
    full_draft: str,
    stance: "DoctrinalStance | None" = None,
) -> tuple[str, list[str]]:
    """Run cheap repairs and rule checks on the assembled draft.

    Returns (possibly-cleaned draft, list of warning messages).
    Never raises -- failures inside individual rules are logged and skipped.
    """
    warnings: list[str] = []
    cleaned = full_draft

    # --- Auto-fix: mojibake ---
    # Canonical fix: re-encode the string under the codec that misread the
    # bytes, then decode as utf-8 to recover the original characters.
    # Try cp1252 first (stricter — has unmapped bytes 0x81/0x8D/0x8F/0x90/
    # 0x9D that raise, protecting some edge cases), then latin-1 (every
    # byte 0x00-0xFF round-trips, so it catches mojibake whose hidden
    # chars fall in the cp1252 unmapped range — e.g. em-dash via E2 80 94
    # contains 0x80, which decodes to € in cp1252 but a non-printing C1
    # control char in latin-1; the rupee ₹ via E2 82 B9 likewise contains
    # 0x82). errors="strict" is its own gate: the encode raises on chars
    # outside the codec range (Devanagari, already-clean ₹, etc.) and the
    # decode raises when the resulting bytes aren't valid utf-8 (clean
    # "café" → bytes 63 61 66 E9, E9 alone is invalid utf-8 lead). Both
    # raise paths leave the string untouched. Substring table below is a
    # safety net for mixed-encoding strings where the roundtrip aborts.
    for codec in ("cp1252", "latin-1"):
        try:
            roundtripped = cleaned.encode(codec, errors="strict").decode("utf-8", errors="strict")
            if roundtripped != cleaned:
                cleaned = roundtripped
                log.info("Validator: mojibake auto-fixed", codec=codec)
                break
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue

    fixed_count = 0
    for bad, good in _MOJIBAKE_REPLACEMENTS:
        if bad in cleaned:
            cleaned = cleaned.replace(bad, good)
            fixed_count += 1
    if fixed_count:
        log.info("Validator: mojibake auto-fixed (substring fallback)",
                 patterns_fixed=fixed_count)

    # --- Auto-fix: strip HTML tags (frontend doesn't render raw HTML) ---
    # Order matters: convert <br>/<hr> to markdown equivalents first so we
    # don't lose line breaks, then strip any remaining tag (e.g. <p>, <div>,
    # <span>, <center>, attributes like align="center"/"right" that the LLM
    # uses to fake centering markdown can't produce). Inner text is always
    # preserved -- we never drop content, only the tag scaffolding.
    cleaned = _HTML_BR_RE.sub("\n", cleaned)
    cleaned = _HTML_HR_RE.sub("\n---\n", cleaned)
    cleaned, html_strip_count = _HTML_ANY_TAG_RE.subn("", cleaned)
    if html_strip_count:
        warnings.append(
            f"Stripped {html_strip_count} HTML tag(s) from draft. The "
            "section prompt forbids HTML -- if this keeps happening, the "
            "LLM is drifting; tighten Rule 12 in DRAFTING_SYSTEM_PROMPT."
        )

    # --- Auto-fix: strip [CITE: ...] markers + log if any survived ---
    cite_hits = _CITE_PLACEHOLDER_RE.findall(cleaned)
    if cite_hits:
        cleaned = _CITE_PLACEHOLDER_RE.sub("", cleaned)
        warnings.append(
            f"Stripped {len(cite_hits)} leftover [CITE: ...] placeholder(s). "
            "Drafting prompt was meant to prevent this -- check section "
            "prompts if it keeps happening."
        )

    # --- Auto-fix: empty numbered paragraphs ---
    # A numbered paragraph like "69." with no body is a section LLM
    # omission. Symptom in the Avachat writ (Sec 10): paragraph 69 rendered
    # as a bare "69." line between paras 68 and 70. We strip such lines
    # rather than leaving them in — paragraph-renumbering downstream
    # closes the gap.
    empty_para_hits = _EMPTY_NUMBERED_PARA_RE.findall(cleaned)
    if empty_para_hits:
        cleaned = _EMPTY_NUMBERED_PARA_RE.sub("", cleaned)
        warnings.append(
            f"Removed {len(empty_para_hits)} empty numbered paragraph(s) "
            f"(bare `N.` lines with no body). Section LLM omitted the body "
            f"for these paragraphs."
        )

    # NOTE: orphan citation tails, forbidden statute pairings, trailing
    # prepositions, and stance compliance were retired from this validator.
    # They now flow through `core/self_refine.self_refine`, which audits
    # the draft against the typed DoctrinalStance + UserIntent (this is the
    # same dynamic critique loop used elsewhere). Adding a new "rule" =
    # extend the self_refine critic prompt, not this validator.

    if warnings:
        log.warning("Validator surfaced issues",
                    count=len(warnings),
                    sample=warnings[0][:120])

    return cleaned, warnings


# --- Step 3.25: Mandatory procedural section injection ---
#
# The LLM-generated outline sometimes drops procedural blocks that a real
# civil suit filing cannot omit (Schedule, Court Fee, List of Docs, etc.).
# This injector classifies the document type from query + outline title and
# appends any missing sections from the appropriate pack so every filing has
# the full skeleton -- regardless of which template was selected.

# Mandatory procedural sections are now enumerated by the doctrinal-stance
# LLM call (see `DoctrinalStance.procedural_sections`) instead of a per-doc-
# type hardcoded `_MANDATORY_PACKS` dict + regex doc-type classifier. The
# stance has full context (query + facts + template) and decides what the
# pleading needs under Indian procedural law — including whether a separate
# Interim Application under Order XXXIX CPC is required. Adding support
# for a new pleading type = extend the stance prompt's `procedural_sections`
# guidance; no doc-type substring matching, no per-type pack maintenance.


def _section_already_present(sections: list[SectionPlan], title: str) -> bool:
    """Fast same-language head-word check: True if any existing section's title
    shares the head word with `title`.

    Catches in-language overlaps cheaply ("Schedule A & B" vs "Schedule of
    Properties" — both start with "schedule"). DOES NOT catch cross-lingual
    overlaps ("गवाहों के बयान" vs "Witness Testimonies" — different head
    words despite identical meaning); for those, the LLM-driven
    `_dedup_procedural_sections_via_llm` pass is the safety net.
    """
    incoming = title.lower().split()
    if not incoming:
        return False
    head = incoming[0]
    for s in sections:
        existing = s.title.lower()
        if head in existing:
            return True
    return False


class _DedupVerdict(BaseModel):
    """Per-candidate keep/drop decision from the semantic-dedup LLM call."""
    title: str = Field(..., description="The candidate procedural-section title as supplied.")
    keep: bool = Field(
        ...,
        description="True if this candidate is TRULY procedurally distinct from "
                    "every section already in the outline. False if any outline "
                    "section already covers the same content (in any language / "
                    "any phrasing).",
    )
    matched_outline_title: str = Field(
        "",
        description="When keep=False, the outline title that semantically "
                    "overlaps this candidate. Empty string when keep=True.",
    )


class _DedupVerdictList(BaseModel):
    verdicts: list[_DedupVerdict] = Field(default_factory=list)


_SEMANTIC_DEDUP_SYSTEM = """You are a deduplication judge for Indian legal-
document section lists. You are given:

  1. An EXISTING OUTLINE list of section titles (substantive sections the
     outline LLM already produced). Titles may be in ANY language —
     English, Hindi, Marathi, Tamil, Telugu, Bengali, etc.
  2. A list of CANDIDATE procedural sections (mandatory procedural blocks
     the stance LLM thinks are needed: Schedule, Verification, Affidavit,
     List of Documents, etc.).

For EACH candidate, decide whether to KEEP it (it covers a TRULY procedural
block the outline doesn't already carry) or DROP it (any outline section
already covers the same purpose — same meaning, even if the phrasing or
language differs).

Cross-lingual matching MUST work:
  - "गवाहों के बयान" ↔ "Witness Testimonies" ↔ "Statement of Witnesses"
    → all the same purpose, DROP the candidate
  - "स्वामित्व का आधार" ↔ "Details of the Will" ↔ "Ownership Basis"
    → same substantive section, DROP the candidate
  - "प्रार्थना" ↔ "Prayer" ↔ "Relief Sought" ↔ "Request to Gram Sabha"
    → same purpose, DROP the candidate
  - "विषय" ↔ "Subject" ↔ "Introduction" → same, DROP

Only KEEP candidates whose purpose is TRULY missing from the outline. A
candidate like "Schedule of Properties" with NO matching outline section
should be KEPT. A candidate like "Verification" when the outline already
has a "Verification" / "सत्यापन" / "प्रमाणीकरण" section should be DROPPED.

Return a JSON object with a `verdicts` list — one entry per candidate, in
the SAME ORDER as the candidates were supplied. Each entry has:
  - title: the candidate's title (verbatim).
  - keep: true/false.
  - matched_outline_title: when keep=false, the outline title that
    semantically overlapped (helps debugging). Empty string when keep=true.

Be aggressive about dropping duplicates — the cost of an unnecessary
section in the draft is high (visible duplication confuses the user); the
cost of dropping a truly-needed procedural block is recoverable later.
"""


async def _dedup_procedural_sections_via_llm(
    existing_outline_titles: list[str],
    candidate_sections: list[ProceduralSectionPlan],
) -> list[ProceduralSectionPlan]:
    """LLM-driven semantic dedup of stance procedural_sections vs outline.

    Returns the candidates that should actually be appended. On failure
    (timeout, parse error), returns the input list unchanged — the head-
    word `_section_already_present` check downstream is still applied,
    so we never amplify duplicates beyond what was already there.

    Single batched call (one LLM round-trip per draft, NOT one per candidate)
    so the cost is bounded.
    """
    if not candidate_sections:
        return candidate_sections
    if not existing_outline_titles:
        return candidate_sections

    candidates_block = "\n".join(
        f"  {i+1}. {c.title}" for i, c in enumerate(candidate_sections)
    )
    outline_block = "\n".join(
        f"  {i+1}. {t}" for i, t in enumerate(existing_outline_titles)
    )

    with log_time(log, "Semantic dedup (stance vs outline)"):
        try:
            llm = get_gemini_flash(temperature=0.0).with_structured_output(
                _DedupVerdictList, include_raw=True,
            )
            prompt = ChatPromptTemplate.from_messages([
                ("system", _SEMANTIC_DEDUP_SYSTEM),
                ("user",
                 "EXISTING OUTLINE TITLES:\n{outline}\n\n"
                 "CANDIDATE PROCEDURAL SECTIONS (from doctrinal stance):\n"
                 "{candidates}\n\n"
                 "Return one verdict per candidate, in order."),
            ])
            chain = prompt | llm
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({
                    "outline": outline_block,
                    "candidates": candidates_block,
                }),
                timeout=20,
            )
        except Exception as e:
            log.warning(
                "Semantic dedup failed; falling back to head-word dedup only",
                error=str(e)[:200],
            )
            return candidate_sections

    from core.token_tracker import record as _record_tokens
    _record_tokens("Drafting", "semantic_dedup_procedural",
                   raw_and_parsed.get("raw"))
    parsed = raw_and_parsed["parsed"]
    verdicts = parsed.verdicts or []

    # Defensive: when the LLM returned fewer / more verdicts than candidates,
    # fall back to the input unchanged. We need a 1:1 mapping for safety.
    if len(verdicts) != len(candidate_sections):
        log.warning(
            "Semantic dedup verdict count mismatch; keeping all candidates",
            candidates=len(candidate_sections), verdicts=len(verdicts),
        )
        return candidate_sections

    kept: list[ProceduralSectionPlan] = []
    dropped: list[tuple[str, str]] = []
    for cand, v in zip(candidate_sections, verdicts):
        if v.keep:
            kept.append(cand)
        else:
            dropped.append((cand.title, v.matched_outline_title))
    if dropped:
        log.info("Semantic dedup dropped overlapping candidates",
                 dropped_count=len(dropped),
                 kept_count=len(kept),
                 sample=[f"{c} ↔ {m}" for c, m in dropped[:3]])
    return kept


def _inject_procedural_sections(
    outline: DraftOutline, stance: DoctrinalStance | None,
) -> list[SectionPlan]:
    """Append missing procedural sections from the doctrinal stance.

    When the stance call succeeded, its `procedural_sections` list is the
    source of truth for what the pleading must carry. We append any item
    the LLM-generated outline didn't already cover. Insertion order
    preserves the stance's enumeration (which the prompt asks be put in
    correct procedural order).

    When stance is None / has no procedural sections (e.g. the user asked
    for a simple legal notice that has no procedural attachments, or the
    stance call timed out), we leave the outline as the section-gen LLM
    produced it. No fallback regex; no hardcoded pack.
    """
    sections = list(outline.sections)
    if stance is None or not stance.procedural_sections:
        return sections

    appended: list[str] = []
    for ps in stance.procedural_sections:
        if _section_already_present(sections, ps.title):
            continue
        sections.append(SectionPlan(
            title=ps.title,
            description=ps.description,
            estimated_paragraphs=ps.estimated_paragraphs,
            needs_citations=ps.needs_citations,
        ))
        appended.append(ps.title)

    if appended:
        log.info("Injected procedural sections from doctrinal stance",
                 count=len(appended), titles=appended,
                 total_sections=len(sections))

    return sections


# --- Step 3.5: Doctrinal Stance (shared legal lane across sections) ---
#
# Sections are generated in parallel with no shared legal context, which led
# to contradictions within one draft — e.g. para 2.2 calling property
# "self-acquired" while para 4.3 called it "ancestral", or sections citing
# Sec 38 SRA for temporary injunction when Order XXXIX CPC is the right
# authority. The doctrinal stance is a one-shot Gemini Flash call that, given
# the facts + template + query, picks ONE legal lane for the draft and
# enumerates statutes/cases to use vs avoid. Every parallel section call gets
# this stance JSON prepended, so all sections plead from the same theory.

_DOCTRINAL_STANCE_SYSTEM = """You are a senior Indian litigator setting the
legal lane for ONE draft. Read the facts and the user's query, then commit to
a single coherent theory of the case. Other section-writers will follow your
stance verbatim — contradictions in their output are caused by ambiguity in
yours, so be decisive.

Return a JSON object with these keys:

- "property_lane" (string): for partition / inheritance / property suits, one
  of "self_acquired_intestate", "self_acquired_testamentary",
  "coparcenary_ancestral_2005", "coparcenary_ancestral_pre_2005",
  "joint_tenancy", "not_applicable". Pick exactly ONE.
- "injunction_lane" (string): "temporary_only", "permanent_only", "both",
  "not_applicable". A temporary injunction restrains conduct during
  pendency (Order XXXIX CPC); a permanent injunction is the final relief
  (Section 38 SRA). Pick what the user actually asked for.
- "applicable_statutes" (list of strings): every statute the draft SHOULD
  cite. Use full names + section numbers, e.g.
  "Section 8 + Schedule, Hindu Succession Act, 1956".
- "non_applicable_statutes" (list of strings): statutes that LOOK related
  but are wrong for this fact pattern. Always include here the alternatives
  to your chosen lanes (e.g. if injunction_lane is "temporary_only", list
  "Section 38, Specific Relief Act, 1963 — applies only to permanent
  injunction, not the temporary injunction sought here").
- "key_cases" (list of {{name, citation, holding}}): 3-6 real Indian SC/HC
  cases you will cite. Use confident citations only; if unsure, omit.
  Suggested anchors (cite only if relevant to the fact pattern):
    * Vineeta Sharma v. Rakesh Sharma, (2020) 9 SCC 1 — daughter coparcenary
    * Smt. Sitabai v. Ramchandra, AIR 1970 SC 343 — adopted child equal rights
    * Dalpat Kumar v. Prahlad Singh, (1992) 1 SCC 719 — three-fold injunction test
    * Sawarni v. Inder Kaur, (1996) 6 SCC 223 — mutation does not confer title
    * Wander Ltd. v. Antox India, 1990 (Supp) SCC 727 — interim injunction principles
  HARD RULE — return EMPTY key_cases ([]) when footer_kind is
  "legal_notice" or "office_application". A legal notice is correspondence
  asserting a position with statutory references — case-law citations make
  it look like a pleading and dilute the demand. An office application
  (RTI / department / employer / bank) is a request to an authority — it
  cites rules and entitlements, not case law. Treating these like pleadings
  is the exact client complaint we are fixing. For "agreement" and "will"
  also return []; contracts cite clauses, not precedents.
- "must_plead" (list of strings): specific facts/elements every section
  writer must include where relevant (e.g. "Section 11 HAMA giving-and-taking
  ceremony with date and adoptive parents named").
- "must_not_plead" (list of strings): things to avoid (e.g. "do not invoke
  the 2005 HSA amendment when property_lane is self_acquired_intestate;
  the amendment governs coparcenary, not self-acquired property").
- "court_fee_rule" (string): one sentence on the court fee basis (e.g.
  "Section 6(vii) Maharashtra Court Fees Act, 1959 — ad valorem on share's
  market value since plaintiff is dispossessed from one flat").
- "footer_kind" (string): the footer convention this draft must end with.
  Pick exactly ONE based on the document type:
    "court_filing"  → plaints, petitions, written statements, bail
                      applications, anticipatory-bail applications,
                      interim / interlocutory applications under Order
                      XXXIX CPC, transfer applications under Section
                      24 CPC, applications under Section 482 BNSS /
                      482 CrPC, applications to a Magistrate, writ
                      petitions, appeals, revisions, reviews — anything
                      FILED IN COURT or before a Tribunal. Footer:
                      Place / Date / Signature of the Petitioner /
                      Applicant / Through Counsel: [Advocate Name].
    "legal_notice"  → Section 138 NI notice, demand notice, eviction
                      notice, statutory notices. NOT filed in court;
                      sent by registered post. Footer signed by counsel
                      directly ("Yours sincerely, Sd. [Advocate Name],
                      [Enrolment No.]"). NO "Petitioner/Applicant" block.
    "office_application" → applications addressed to a NON-COURT
                      authority: RTI applications under the Right to
                      Information Act, 2005; applications to a government
                      department / public officer / Tahsildar / SDM /
                      Collector / Registrar / Sub-Registrar / Municipal
                      Corporation for a certificate, no-objection,
                      mutation, license, ration card, birth/death/
                      domicile certificate; applications to an employer
                      for leave / NOC / experience letter; applications
                      to a bank / housing society / university /
                      regulator (SEBI / RBI / IRDAI / TRAI) that are NOT
                      appeals to a tribunal. These are LETTERS, not
                      pleadings. Use this when the addressee is not a
                      court, magistrate, or tribunal. Footer: signed by
                      the APPLICANT directly with "Yours faithfully /
                      Sd. / [Applicant Name] / Place / Date".
    "agreement"     → contracts, MOUs, lease deeds, sale deeds, NDAs,
                      partnership deeds, settlement agreements. Footer:
                      all parties' signatures + 2 attesting witness
                      blocks. NO court-filing language.
    "will"          → wills, codicils. Footer: testator's signature +
                      2 attesting witness blocks per Section 63 of the
                      Indian Succession Act, 1925.
    "none"          → other document types where no automatic footer
                      should be added (e.g. legal opinion memo).
  DO NOT default to "court_filing" — a legal notice with a court-filing
  footer is broken output. APPLICATION DISAMBIGUATION (do not guess —
  reason from the addressee): the word "application" alone is not enough
  to pick a footer. Decide by WHO the application is ADDRESSED TO:
    - Addressed to a Court / Magistrate / Tribunal / Sessions Judge /
      High Court / Supreme Court / Family Court / Consumer Forum /
      Labour Court / Authority that adjudicates → "court_filing".
      Examples: bail application, anticipatory bail, IA under Order
      XXXIX CPC, application under Section 482 BNSS, transfer
      application under Section 24 CPC.
    - Addressed to a Public Information Officer / Government
      Department / Tahsildar / Collector / Registrar / Municipal
      Corporation / Police Commissioner (administrative, not for FIR) /
      Employer / Bank / Housing Society / University / Regulator
      → "office_application". Examples: RTI application, application
      for caste certificate, application for income certificate,
      application for leave, application for experience letter,
      application for NOC, application for ration card.
    - Addressed to a Police Inspector / SHO / Station House Officer
      for FIR registration / cognisable offence → "police_complaint".
  When the user's query does not explicitly name an addressee, INFER
  from purpose: a "bail application" presupposes a court; an "RTI
  application" presupposes a Public Information Officer; an "application
  for income certificate" presupposes a Tahsildar/SDM. Use common sense,
  not regex.
- "procedural_sections" (list of {{title, description, estimated_paragraphs,
  needs_citations}}): NARROWLY scoped — list ONLY the mandatory procedural
  blocks REQUIRED under the Indian Code (CPC / BNSS-CrPC / SRA / Court
  Fees Act / Order XXXIX, etc.) that the outline you'll see below DOES
  NOT already carry. The outline is the source of truth for substantive
  sections (Statement of Facts, Cause of Action, Grounds, Witness
  Testimonies, Prayer, etc.); your job here is only to top up with
  REQUIRED procedural blocks the outline missed.

  IMPORTANT — DO NOT list anything that any of these substantive
  categories already covers (the outline LLM owns these):
    * "Introduction" / "Background" / "Preamble" / "Synopsis"
    * "Details of <the will / the agreement / the property / etc.>"
    * "Statement of Facts" / "Brief Facts" / "Cause of Action"
    * "Witness Testimonies" / "Witness List" / "Witness Statements"
    * "Prayer" / "Relief Sought" / "Request to <authority>"
    * "Grounds" / "Submissions" / "Arguments"
    * "Possession and Enjoyment" / "Use of Property"
    * "Ownership Basis" / "Title"
  If the outline already has a section matching one of those purposes
  (in ANY language — Hindi याचिका के तथ्य, Marathi विनंती, Tamil
  உரிமைகோரல், English Prayer, etc.), DO NOT re-list it as a
  procedural_section. Duplicate sections in different languages
  produce the worst draft output we've seen — they confuse the user
  AND inflate token cost.

  TRULY PROCEDURAL blocks (the ONLY ones this field should carry):
    * Civil suit / plaint → Schedule of Properties; Valuation and Court
      Fee; List of Documents (Order VII Rule 14 / Order XI Rule 14 CPC);
      Verification (Order VI Rule 15 CPC); Affidavit in Support (Order
      XIX Rule 3 CPC, notarised).
    * When ANY temporary / interim / ad-interim injunction is prayed for
      (whether in a suit, writ, or appeal) → add a separate Interim
      Application under Order XXXIX Rules 1 & 2 CPC with its own
      affidavit and the three-fold test (prima facie case, balance of
      convenience, irreparable injury).
    * Bail application → Verification by accused; List of Documents
      (FIR copy, prior bail orders).
    * Writ / PIL → Verification; List of Documents; Affidavit; Annexures
      Index.
    * Legal notice → procedural_sections: []. A notice carries no
      court-procedural attachments.
    * Office application (RTI / department / employer / bank / society /
      university / regulator) → procedural_sections: []. The outline
      already covers the body; nothing procedural to top up.
    * Appeal / revision / review → Memo of grounds; Application for
      condonation of delay if filed beyond limitation; Index; Verification.

  Include description + estimated paragraphs + needs_citations for each.
  TITLE LANGUAGE: emit each procedural_section title in the same language
  as the outline you'll see in the user message. If the outline titles are
  in Hindi, write "अनुसूची संपत्तियों की" (Schedule of Properties), "सत्यापन"
  (Verification), "शपथ-पत्र" (Affidavit). If Marathi: "मालमत्तेची अनुसूची",
  "प्रमाणीकरण", "शपथपत्र". If Tamil: "சொத்துகளின் அட்டவணை", "சத்தியம்".
  If the outline is in English, keep titles in English. Do NOT mix
  languages within a single draft.

Be terse. JSON only. No prose around it.
"""


class DoctrinalCase(BaseModel):
    name: str = Field(..., description="Case name (e.g. 'Vineeta Sharma v. Rakesh Sharma')")
    citation: str = Field(..., description="Full citation (e.g. '(2020) 9 SCC 1')")
    holding: str = Field(..., description="One-line holding")


class ProceduralSectionPlan(BaseModel):
    """A mandatory procedural section the pleading must carry.

    Read by `_inject_procedural_sections` and appended to the outline if
    not already present. Replaces the hardcoded `_MANDATORY_PACKS` dict.
    """
    title: str = Field(..., description="Section title (e.g. 'Schedule of Properties')")
    description: str = Field(..., description="One-paragraph guidance for the section writer")
    estimated_paragraphs: int = Field(2, ge=1, le=10)
    needs_citations: bool = Field(False)


class DoctrinalStance(BaseModel):
    property_lane: str = Field("not_applicable")
    injunction_lane: str = Field("not_applicable")
    applicable_statutes: List[str] = Field(default_factory=list)
    non_applicable_statutes: List[str] = Field(default_factory=list)
    key_cases: List[DoctrinalCase] = Field(default_factory=list)
    must_plead: List[str] = Field(default_factory=list)
    must_not_plead: List[str] = Field(default_factory=list)
    court_fee_rule: str = Field("")
    procedural_sections: List[ProceduralSectionPlan] = Field(
        default_factory=list,
        description="Mandatory procedural sections this pleading must carry. "
                    "Populated by the doctrinal-stance LLM call. The drafting "
                    "pipeline injects any missing section from this list into "
                    "the outline before parallel section generation.",
    )
    footer_kind: str = Field(
        "court_filing",
        description="What kind of footer this draft needs. One of: "
                    "'court_filing' (plaints, petitions, written statements, "
                    "bail applications, appeals, writs, Section 200 CrPC / "
                    "Section 223 BNSS magistrate complaints — anything filed "
                    "in court): Place / Date / Signature of the Petitioner / "
                    "Through Counsel; "
                    "'legal_notice' (Section 138 NI Act notice, demand notice, "
                    "vacate notice, reply to legal notice): no court footer; "
                    "signed by counsel directly with 'Yours sincerely / Sd. / "
                    "[Advocate Name] / [Enrolment No.]'; "
                    "'office_application' (application addressed to a NON-"
                    "court authority — RTI to a Public Information Officer; "
                    "application to a government department / Tahsildar / "
                    "Collector / SDM / Registrar / Municipal Corporation for "
                    "a certificate / no-objection / mutation / licence; "
                    "application to an employer / bank / housing society / "
                    "university / regulator): no court footer; signed by the "
                    "APPLICANT directly (not counsel) with 'Yours faithfully "
                    "/ Sd. / [Applicant Name] / Place / Date'; "
                    "'police_complaint' (FIR registration application under "
                    "Section 154 CrPC / Section 173 BNSS, complaint addressed "
                    "to Station House Officer / Police Inspector — NOT a "
                    "magistrate complaint): no court footer; signed by the "
                    "COMPLAINANT (not counsel) with 'Yours faithfully / Sd. / "
                    "[Complainant Name] / Place / Date'; "
                    "'tax_submission' (written submission to a tax / "
                    "quasi-judicial appellate authority: CIT(A) / "
                    "Commissioner of Income Tax (Appeals) / NFAC / ITAT / "
                    "Income Tax Appellate Tribunal / GST Appellate "
                    "Authorities (AAAR / GSTAT) / CESTAT / NCLT / NCLAT / "
                    "SAT / DRT / DRAT): NOT a court filing, NOT a notice; "
                    "signed by the APPELLANT directly with 'For and on "
                    "behalf of the Appellant / Sd. / (Name) / Place / Date'; "
                    "'agreement' (contracts, MOUs, leases, sale deeds, NDAs): "
                    "no court footer; signed by all parties with witness lines; "
                    "'will' (wills, codicils): testator + 2 attesting witnesses; "
                    "'none' (other / unknown): no automatic footer. "
                    "Pick based on the document type — DO NOT default to "
                    "'court_filing' for a notice, police complaint, tax "
                    "appeal, or agreement. DISAMBIGUATION: When the user says "
                    "'police complaint' / 'FIR' / mentions Police Inspector / "
                    "SHO / Station House Officer / Section 154 CrPC / Section "
                    "173 BNSS, use 'police_complaint' (letter format). When "
                    "the user says 'CIT(A)' / 'Commissioner of Income Tax "
                    "(Appeals)' / 'ITAT' / 'Income Tax Appellate Tribunal' / "
                    "'Written Submission' + Assessment Year context / 'Form "
                    "35' / 'NFAC' / 'Section 143(3)' / 'Section 144B' / "
                    "'Section 250 Income Tax Act' / 'GST Appellate' / 'AAAR' "
                    "/ 'CESTAT' / 'NCLT' / 'NCLAT' / 'SAT' / 'DRT', use "
                    "'tax_submission' (appellate submission format). Only use "
                    "'court_filing' for a 'complaint' when the user explicitly "
                    "says 'Section 200 CrPC' / 'Section 223 BNSS' / "
                    "'magistrate complaint' — those are filed in court. "
                    "APPLICATION DISAMBIGUATION: the word 'application' is "
                    "ambiguous; pick footer_kind by the ADDRESSEE inferred "
                    "from the query. Court / Magistrate / Tribunal addressee "
                    "→ 'court_filing' (bail / anticipatory bail / IA Order "
                    "XXXIX / Section 482 BNSS / transfer / Section 156(3) "
                    "CrPC). Public Information Officer / government dept / "
                    "Tahsildar / Collector / SDM / Registrar / Municipal "
                    "Corporation / employer / bank / housing society / "
                    "university / regulator → 'office_application' (RTI, "
                    "certificate, NOC, mutation, leave, experience letter, "
                    "ration card, character certificate). If the addressee "
                    "is ambiguous, infer from purpose using common sense "
                    "(an 'RTI application' addresses a PIO; an 'application "
                    "for income certificate' addresses a Tahsildar / SDM).",
    )


async def _generate_doctrinal_stance(
    query: str,
    doc_title: str,
    user_facts: str = "",
    case_facts: str = "",
    user_language: str = "en",
    existing_outline_titles: list[str] | None = None,
) -> DoctrinalStance | None:
    """One-shot Flash call producing the legal lane the whole draft will follow.

    Returns None on failure — section generation falls back to template-only
    guidance, same as before this step existed.

    `existing_outline_titles` lets the stance LLM see what substantive sections
    the outline already carries, so it doesn't propose overlapping procedural
    sections (the leading cause of duplicate sections in the assembled draft).
    `user_language` tells the stance LLM what language to emit
    procedural_section titles in, so they match the rest of the draft.
    """
    facts_block = ""
    if case_facts.strip():
        facts_block += "KEY ENTITIES:\n" + case_facts.strip() + "\n\n"
    if user_facts.strip():
        facts_block += "FULL DOCUMENT TEXT:\n" + user_facts[:8000]

    outline_titles_block = ""
    if existing_outline_titles:
        outline_titles_block = (
            "EXISTING OUTLINE SECTIONS (the outline LLM already produced "
            "these — do NOT propose overlapping procedural_sections; emit "
            "your procedural_section titles in the SAME language as these):\n"
            + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(existing_outline_titles))
            + "\n"
        )

    language_name = SUPPORTED_LANGUAGES.get(user_language, "English")
    target_language_block = (
        f"TARGET LANGUAGE for procedural_section titles + descriptions: "
        f"{language_name} ({user_language}). Use the conventional Indian "
        f"legal vocabulary for this language; do NOT mix languages within "
        f"a single procedural_section entry."
    )

    with log_time(log, "Doctrinal stance generation"):
        try:
            llm = get_drafting_llm().with_structured_output(
                DoctrinalStance, include_raw=True,
            )
            prompt = ChatPromptTemplate.from_messages([
                ("system", _DOCTRINAL_STANCE_SYSTEM),
                ("user",
                 "USER QUERY:\n{query}\n\n"
                 "DOCUMENT TYPE (from template selection): {doc_title}\n\n"
                 "{target_language_block}\n\n"
                 "{outline_titles_block}"
                 "USER-PROVIDED FACTS (may be empty):\n{facts_block}"),
            ])
            chain = prompt | llm
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({
                    "query": query,
                    "doc_title": doc_title,
                    "facts_block": facts_block or "(none -- proceed from query alone)",
                    "outline_titles_block": outline_titles_block,
                    "target_language_block": target_language_block,
                }),
                timeout=30,
            )
        except Exception as e:
            log.warning("Doctrinal stance generation failed -- continuing without it",
                        error=str(e)[:200])
            return None

        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "doctrinal_stance", raw_and_parsed.get("raw"))
        stance = raw_and_parsed["parsed"]
        log.info("Doctrinal stance generated",
                 property_lane=stance.property_lane,
                 injunction_lane=stance.injunction_lane,
                 applicable_count=len(stance.applicable_statutes),
                 non_applicable_count=len(stance.non_applicable_statutes),
                 cases=len(stance.key_cases))
        return stance


async def _verify_stance_cases(stance: DoctrinalStance) -> DoctrinalStance:
    """Verify stance.key_cases against the judgment ES corpus in parallel.

    The doctrinal-stance LLM picks "real Indian SC/HC cases" without
    retrieval grounding, so a confident-sounding fabricated citation
    (e.g. "S.B. Gurbaksh Singh v. Union of India, (1976) 2 SCC 104")
    can land in stance.key_cases. The drafting section prompt then
    USES those cases verbatim, and self_refine WHITELISTS the stance
    cases — so a fabrication slips end-to-end with no corpus check.

    Per case, fire two parallel ES lookups:
      * search_by_citation(c.citation) — exact lookup on the citation /
        case_number keyword fields
      * search_by_party_names(petitioner, respondent) — fallback when
        the citation format is unusual but the parties are real

    Drop any case where BOTH lookups return zero hits AND the case is
    not in our small high-confidence anchor allowlist (the cases the
    stance prompt explicitly names as exemplars — these are vetted).

    Returns a new DoctrinalStance with key_cases filtered. Never raises.
    """
    if not stance.key_cases:
        return stance

    # Cases the stance prompt itself names as anchor exemplars are
    # known-real and don't need ES verification (saves 5 ES calls).
    _STANCE_PROMPT_ANCHORS = {
        ("vineeta sharma", "rakesh sharma"),
        ("smt. sitabai", "ramchandra"),
        ("dalpat kumar", "prahlad singh"),
        ("sawarni", "inder kaur"),
        ("wander", "antox"),
    }

    def _parties(name: str) -> tuple[str, str]:
        # "X v. Y" or "X vs Y" — return (petitioner, respondent) lowercased
        norm = name.lower().replace(" v. ", " v ").replace(" vs ", " v ")
        if " v " in norm:
            pet, _, res = norm.partition(" v ")
            return pet.strip(), res.strip()
        return name.lower().strip(), ""

    async def _verify_one(case) -> tuple[bool, str]:
        pet, res = _parties(case.name)
        # Anchor allowlist — skip ES round-trip.
        for a_pet, a_res in _STANCE_PROMPT_ANCHORS:
            if a_pet in pet and (not a_res or a_res in res):
                return True, "anchor_allowlist"
        # ES verification in parallel
        try:
            from tools.shared.judgment_search import (
                search_by_citation, search_by_party_names,
            )
            cite_hits = await asyncio.to_thread(
                search_by_citation, case.citation, 2,
            )
            party_hits: list = []
            if pet and res:
                try:
                    party_hits = await asyncio.to_thread(
                        search_by_party_names, pet, res, None, None, 2,
                    )
                except Exception:
                    party_hits = []
            if cite_hits or party_hits:
                return True, f"es_hits={len(cite_hits)}+{len(party_hits)}"
            return False, "es_zero_hits"
        except Exception as e:
            # If ES is unreachable, fail-OPEN (keep the case). We'd
            # rather have a potentially-fabricated citation than a
            # citation-less draft when ES is down.
            log.warning("Stance verification ES call failed; keeping case",
                        case=case.name[:80], error=str(e)[:120])
            return True, "es_unreachable"

    verdicts = await asyncio.gather(
        *[_verify_one(c) for c in stance.key_cases],
        return_exceptions=False,
    )

    verified_cases = []
    dropped = []
    for case, (ok, reason) in zip(stance.key_cases, verdicts):
        if ok:
            verified_cases.append(case)
        else:
            dropped.append(f"{case.name[:60]} ({reason})")

    if dropped:
        log.warning("Dropped unverified case citations from stance",
                    dropped_count=len(dropped),
                    kept_count=len(verified_cases),
                    sample=dropped[:3])

    # Return a NEW stance object with filtered cases (Pydantic immutable
    # via model_copy + update); preserves everything else verbatim.
    verified_stance = stance.model_copy(update={"key_cases": verified_cases})
    return verified_stance


def _format_stance_for_section(stance: DoctrinalStance | None) -> str:
    """Render the stance as a compact prompt block for each section call."""
    if stance is None:
        return ""
    lines = ["DOCTRINAL STANCE — every section in this draft must follow this lane:\n"]
    if stance.property_lane and stance.property_lane != "not_applicable":
        lines.append(f"- PROPERTY LANE: {stance.property_lane}")
    if stance.injunction_lane and stance.injunction_lane != "not_applicable":
        lines.append(f"- INJUNCTION LANE: {stance.injunction_lane}")
    if stance.court_fee_rule:
        lines.append(f"- COURT FEE: {stance.court_fee_rule}")
    if stance.applicable_statutes:
        lines.append("- USE THESE STATUTES (cite by name + section):")
        for s in stance.applicable_statutes:
            lines.append(f"    * {s}")
    if stance.non_applicable_statutes:
        lines.append("- DO NOT CITE THESE STATUTES (wrong for this fact pattern):")
        for s in stance.non_applicable_statutes:
            lines.append(f"    * {s}")
    # Non-pleading drafts must NOT cite case law. Legal notices are
    # correspondence; office applications are requests to a non-court
    # authority. Suppress the case-law list AND emit an explicit ban so
    # the section LLM cannot reach for its parametric case memory.
    footer_kind = getattr(stance, "footer_kind", "") or ""
    case_law_banned = footer_kind in ("legal_notice", "office_application")
    if case_law_banned:
        lines.append(
            "- CASE-LAW BAN — this draft is "
            + ("a legal notice" if footer_kind == "legal_notice" else "an office application")
            + ". DO NOT cite, paraphrase, or reference any case law / judgment "
            "/ precedent in ANY section. No "
            "*Party v. Party*, no (Year) Reporter Vol Page, no 'as held in', "
            "no '[CITE: ...]'. Statutes and rules are fine (e.g. Section 138 "
            "NI Act, Section 6 RTI Act, 2005) — case-law is not. The reader "
            "is not a court."
        )
    elif stance.key_cases:
        lines.append("- USE THESE CASE LAWS (cite by name + citation; never as [CITE: ...]):")
        for c in stance.key_cases:
            lines.append(f"    * {c.name}, {c.citation} -- {c.holding}")
    if stance.must_plead:
        lines.append("- MUST PLEAD where relevant:")
        for m in stance.must_plead:
            lines.append(f"    * {m}")
    if stance.must_not_plead:
        lines.append("- MUST NOT PLEAD:")
        for m in stance.must_not_plead:
            lines.append(f"    * {m}")
    return "\n".join(lines) + "\n\n"


# --- Step 3.7: Layout/Format extractor (per-template, cached) ---
#
# The chosen template_text is a fully-formatted exemplar of an Indian legal
# document -- it carries layout signals (centered "PRAYER" / "VERIFICATION"
# labels, right-aligned "______Plaintiff" tags, numbered "That ..." paragraphs,
# verbatim prayer/verification clauses, signature blocks). The outline + section
# prompts already pass the full template_text, but they tell the LLM "use it
# only for structure" because of BUG-02 (the LLM used to copy fake names and
# placeholder amounts straight from the template into the draft).
#
# This extractor distills the LAYOUT separately from the facts: one Flash call
# reads the template, produces a FormatSpec (alignment, numbering, openers,
# signature/verification blocks), which is then injected as its own block in
# the outline + section prompts. The LLM gets a clear "imitate this layout
# verbatim" signal divorced from the fact-isolation warnings, dramatically
# improving visual fidelity to real Indian court conventions.
#
# Cached per template_source (the ES `source` key) -- one extraction per
# template ever, then near-zero cost across all subsequent drafts that pick
# the same template.

class FormatSpec(BaseModel):
    """Layout/typographic conventions extracted from a template's exemplar.

    All fields are short strings or compact patterns -- never contain actual
    facts (names, dates, amounts). Placeholders like `[Plaintiff Name]` or
    `___` are used where the real document would carry case-specific data.
    """
    court_header_alignment: str = Field(
        "centered",
        description="Alignment of the court name and case-number header. "
                    "One of 'centered', 'left', 'right'.",
    )
    section_label_style: str = Field(
        "uppercase_centered",
        description="How section labels like PRAYER / VERIFICATION are styled. "
                    "Examples: 'uppercase_centered', 'titlecase_left', "
                    "'bold_left', 'underlined_centered'.",
    )
    paragraph_numbering: str = Field(
        "1., 2., 3.",
        description="Exact glyph pattern for numbered paragraphs. Examples: "
                    "'1., 2., 3.' or '(1), (2), (3)' or 'i, ii, iii'.",
    )
    paragraph_opener: str = Field(
        "",
        description="Verbatim opener that prefixes each numbered paragraph "
                    "(e.g. 'That '). Empty if no opener.",
    )
    sub_point_style: str = Field(
        "a., b., c.",
        description="Glyph pattern for sub-points within a paragraph or prayer "
                    "clause. Examples: 'a., b., c.' or '(a), (b), (c)'.",
    )
    party_block_tag_alignment: str = Field(
        "right",
        description="Alignment of the '______Plaintiff' / '______Defendant' "
                    "tags that close each party's block.",
    )
    party_block_separator: str = Field(
        "VERSUS",
        description="The divider phrase between plaintiff and defendant blocks.",
    )
    prayer_opener: str = Field(
        "",
        description="Verbatim sentence that opens the Prayer section. Use "
                    "[Hon'ble Court] etc. placeholders for any names. Example: "
                    "'It is therefore most humbly prayed that this Hon'ble "
                    "Court may be pleased to:'",
    )
    prayer_section_label: str = Field(
        "PRAYER",
        description="Exact label used for the Prayer section header.",
    )
    verification_label: str = Field(
        "VERIFICATION",
        description="Exact label for the verification section.",
    )
    verification_template: str = Field(
        "",
        description="1-3 line verification clause skeleton with [Plaintiff Name] "
                    "placeholders for case-specific data.",
    )
    signature_block: str = Field(
        "",
        description="Multi-line signature block skeleton (alignment hint may "
                    "be embedded as `[right-aligned]` etc.).",
    )
    place_date_format: str = Field(
        "PLACE: [City]\nDATE: [Date]",
        description="Format of the PLACE/DATE footer line(s).",
    )
    schedule_notation: str = Field(
        "",
        description="How Schedule annexes are referenced (e.g. 'Schedule A: "
                    "Description of property...'). Empty if not applicable.",
    )
    other_conventions: List[str] = Field(
        default_factory=list,
        description="Any other notable layout patterns (e.g. 'capitalised "
                    "RESPECTFULLY SHOWETH: before paragraph 1', 'each prayer "
                    "clause indented under sub-letter'). Short observations only.",
    )


_FORMAT_EXTRACTOR_SYSTEM = """You are a legal-document layout analyst for
Indian court filings. Extract ONLY the FORMATTING and LAYOUT conventions
from the supplied document exemplar.

CRITICAL: Do NOT extract any facts, names, dates, amounts, court locations,
party details, case numbers, or substantive content -- those belong to a
DIFFERENT case and would contaminate the user's draft. Where the exemplar
has specific values, substitute placeholders like `[Plaintiff Name]`,
`[Court Name]`, `[Date]`, `[Amount]`, `[Address]`.

Capture only the visual / typographic / structural conventions:
- Alignment patterns (centered / left / right) for headers, party tags,
  signature blocks
- Exact glyph pattern for paragraph numbering and sub-points
- Verbatim opening phrases for paragraphs, prayer, verification (with
  placeholders for any specific values)
- Section label styling (caps, alignment, decoration)
- Signature and PLACE/DATE block format
- Schedule annexure notation
- Any other unusual layout patterns

The output will be used to guide the LAYOUT of a NEW draft about a
DIFFERENT case. Imitable patterns ONLY -- no facts.
"""


@lru_cache(maxsize=256)
def _format_spec_cache_key(template_source: str, content_fingerprint: str) -> str:
    """Cache key combining template path + a short content hash so that
    re-ingesting a template into ES invalidates its cached FormatSpec."""
    return f"{template_source}::{content_fingerprint}"


_FORMAT_SPEC_STORE: dict[str, "FormatSpec | None"] = {}


async def _extract_format_spec(
    template_source: str, template_text: str,
) -> FormatSpec | None:
    """One-shot Flash call producing the template's layout conventions.

    Cached per (template_source, content_hash) so re-runs of the same
    template are free. Returns None on failure -- outline + section gen
    fall back to format-block-less prompts (same as before this step
    existed).
    """
    if not template_source or not template_text:
        return None

    import hashlib
    fp = hashlib.sha256(template_text[:200].encode("utf-8")).hexdigest()[:8]
    cache_key = _format_spec_cache_key(template_source, fp)
    if cache_key in _FORMAT_SPEC_STORE:
        log.debug("Format spec cache hit", source=template_source[-40:])
        return _FORMAT_SPEC_STORE[cache_key]

    with log_time(log, "Format spec extraction"):
        try:
            llm = get_drafting_llm().with_structured_output(
                FormatSpec, include_raw=True,
            )
            prompt = ChatPromptTemplate.from_messages([
                ("system", _FORMAT_EXTRACTOR_SYSTEM),
                ("user", "EXEMPLAR (analyse layout only, IGNORE all facts):\n{template}"),
            ])
            chain = prompt | llm
            # Cap the template fed to extractor at 6000 chars -- enough to
            # see headers + first paragraphs + prayer + verification.
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({"template": template_text[:6000]}),
                timeout=30,
            )
        except Exception as e:
            log.warning("Format spec extraction failed -- continuing without",
                        error=str(e)[:200], source=template_source[-40:])
            _FORMAT_SPEC_STORE[cache_key] = None
            return None

        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "format_spec", raw_and_parsed.get("raw"))
        spec = raw_and_parsed["parsed"]
        _FORMAT_SPEC_STORE[cache_key] = spec
        log.info("Format spec extracted",
                 source=template_source[-40:],
                 numbering=spec.paragraph_numbering,
                 opener=spec.paragraph_opener[:30],
                 prayer_label=spec.prayer_section_label)
        return spec


# --- Template language detection ---
#
# Hindi/Marathi/Sanskrit share Devanagari script, so the BM25 template search
# regularly picks a Marathi-corpus template for a Hindi query (or vice
# versa). The section LLM then imitates the template's specific vocabulary
# (Marathi वय / रा. / चा-possessives vs Hindi आयु / निवासी / का-possessives)
# even with a strict-language directive in place — the template's exemplar
# vocabulary is too strong a signal.
#
# This detector runs a one-shot Gemini Flash Lite call per template (cached
# per template_source like FormatSpec) to identify the template's PRIMARY
# language. The outline + section prompts then carry a warning block when
# template_language != user_language, instructing the LLM to write fresh in
# user_language and NOT borrow language-specific vocabulary from the
# template.
#
# Returns None on failure -- callers treat None as "same as user_language"
# (no warning), which matches the prior behaviour.

_TEMPLATE_LANG_STORE: dict[str, str | None] = {}


class _TemplateLanguage(BaseModel):
    language_code: str = Field(
        ...,
        description="ISO 639-1 code of the template's PRIMARY content "
                    "language. The template may carry English headers / "
                    "section labels and proper-noun citations; ignore those "
                    "and identify the language the BODY paragraphs are "
                    "written in. Examples: 'en' for fully English templates, "
                    "'mr' for Marathi-body templates, 'hi' for Hindi-body, "
                    "'ta' for Tamil-body. When the template body is mixed "
                    "or unclear, return the language of the MAJORITY of "
                    "the prose. When you genuinely can't tell, return 'en'.",
        pattern=r"^[a-z]{2}$",
    )


_TEMPLATE_LANG_SYSTEM = """You are a language identifier for Indian legal
documents. Look at the body paragraphs of the supplied exemplar and identify
its PRIMARY language. Indian legal templates are typically in English, Hindi,
Marathi, Tamil, Telugu, Kannada, Malayalam, Gujarati, Bengali, Punjabi, Urdu,
or Odia. Hindi and Marathi (and Sanskrit) share Devanagari script — when the
script is Devanagari, DISAMBIGUATE by grammar:

- Marathi markers: possessive suffixes चा / ची / चे (e.g. "वादीचा अर्ज"),
  verb आहे (is), क्रमांक (number), तालुका, जिल्हा, कोर्टात (in court), वय (age),
  रा. (resident of), म्हणून (as / because), तसेच (also), यांचे (their),
  ची नोंद (note of), अधिनियम कलम (act section), सन (year).
- Hindi markers: possessive suffixes का / की / के (e.g. "वादी का आवेदन"),
  verb है (is), संख्या (number), तहसील / जिला, न्यायालय में (in court), आयु
  / उम्र (age), निवासी (resident), क्योंकि (because), तथा (and), उनका / उनके
  (their), के तहत (under), अधिनियम की धारा (act section), वर्ष / साल (year).

Return ONLY the ISO 639-1 code, no prose. When mixed or unclear, return the
MAJORITY-language code; if you genuinely can't tell, return 'en'.
"""


async def _detect_template_language(
    template_source: str, template_text: str,
) -> str | None:
    """Identify the primary content language of a drafting template.

    Cached per (template_source, content_hash). Returns ISO 639-1 string or
    None on failure (caller treats None as "no warning needed").
    """
    if not template_source or not template_text:
        return None

    import hashlib
    fp = hashlib.sha256(template_text[:200].encode("utf-8")).hexdigest()[:8]
    cache_key = f"{template_source}::{fp}"
    if cache_key in _TEMPLATE_LANG_STORE:
        log.debug("Template language cache hit",
                  source=template_source[-40:],
                  lang=_TEMPLATE_LANG_STORE[cache_key])
        return _TEMPLATE_LANG_STORE[cache_key]

    with log_time(log, "Template language detection"):
        try:
            llm = get_gemini_flash(temperature=0.0).with_structured_output(
                _TemplateLanguage, include_raw=True,
            )
            prompt = ChatPromptTemplate.from_messages([
                ("system", _TEMPLATE_LANG_SYSTEM),
                ("user", "EXEMPLAR (identify the BODY language only):\n{template}"),
            ])
            chain = prompt | llm
            # 3000 chars is enough to see body paragraphs without spending
            # tokens on signature blocks at the tail.
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({"template": template_text[:3000]}),
                timeout=20,
            )
        except Exception as e:
            log.warning(
                "Template language detection failed -- continuing without",
                error=str(e)[:200], source=template_source[-40:],
            )
            _TEMPLATE_LANG_STORE[cache_key] = None
            return None

        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "template_language",
                       raw_and_parsed.get("raw"))
        parsed = raw_and_parsed["parsed"]
        lang = parsed.language_code
        _TEMPLATE_LANG_STORE[cache_key] = lang
        log.info("Template language detected",
                 source=template_source[-40:], lang=lang)
        return lang


def _build_template_language_warning(
    template_language: str | None,
    user_language: str,
) -> str:
    """When the template's body language differs from what the user wants,
    emit a strong warning that the section LLM (and outline LLM) should NOT
    borrow vocabulary from the template.

    Returns empty string when there's no mismatch (so the prompt template
    var is always safe to interpolate even when this layer is off).
    """
    if not template_language:
        return ""
    if template_language == user_language:
        return ""
    from core.language import SUPPORTED_LANGUAGES
    tlang_name = SUPPORTED_LANGUAGES.get(template_language, template_language)
    ulang_name = SUPPORTED_LANGUAGES.get(user_language, user_language)
    # Devanagari-vs-Devanagari (Hindi <-> Marathi <-> Sanskrit) is the most
    # common confusion in the corpus. Surface the exact grammar differences
    # so the LLM has zero excuse to copy the wrong forms.
    _DEVANAGARI = {"hi", "mr", "sa"}
    devanagari_hint = ""
    if template_language in _DEVANAGARI and user_language in _DEVANAGARI:
        if user_language == "hi" and template_language == "mr":
            devanagari_hint = (
                "Hindi and Marathi share Devanagari script but DIFFER in "
                "grammar and vocabulary. The template is Marathi; the "
                "output must be Hindi. Replace Marathi forms with Hindi "
                "forms throughout:\n"
                "  - Possessive: Marathi चा / ची / चे  →  Hindi का / की / के\n"
                "  - 'is' verb: Marathi आहे  →  Hindi है\n"
                "  - 'are' verb: Marathi आहेत  →  Hindi हैं\n"
                "  - 'age': Marathi वय  →  Hindi आयु or उम्र\n"
                "  - 'resident of' (short): Marathi रा.  →  Hindi निवासी\n"
                "  - 'district': Marathi जिल्हा  →  Hindi जिला\n"
                "  - 'tehsil': Marathi तालुका  →  Hindi तहसील\n"
                "  - 'number': Marathi क्रमांक  →  Hindi संख्या\n"
                "  - 'year': Marathi सन  →  Hindi वर्ष or साल\n"
                "  - 'in this matter': Marathi या प्रकरणी  →  Hindi इस मामले में\n"
                "  - 'applicant / petitioner': Marathi अर्जदार / याचिकाकर्ता  →  Hindi याचिकाकर्ता / आवेदक\n"
                "  - 'address': 'राजूचा पत्ता' (Marathi)  →  'राजू का पता' (Hindi)\n"
                "  - 'of section': Marathi कलम X च्या  →  Hindi धारा X की\n"
                "  - Connective 'and': Marathi व / तसेच  →  Hindi और / तथा\n"
                "  - Connective 'because': Marathi कारण की  →  Hindi क्योंकि\n"
            )
        elif user_language == "mr" and template_language == "hi":
            devanagari_hint = (
                "Hindi and Marathi share Devanagari script but DIFFER in "
                "grammar and vocabulary. The template is Hindi; the "
                "output must be Marathi. Replace Hindi forms with Marathi "
                "forms throughout:\n"
                "  - Possessive: Hindi का / की / के  →  Marathi चा / ची / चे\n"
                "  - 'is' verb: Hindi है  →  Marathi आहे\n"
                "  - 'are' verb: Hindi हैं  →  Marathi आहेत\n"
                "  - 'age': Hindi आयु / उम्र  →  Marathi वय\n"
                "  - 'resident of' (short): Hindi निवासी  →  Marathi रा.\n"
                "  - 'district': Hindi जिला  →  Marathi जिल्हा\n"
                "  - 'tehsil': Hindi तहसील  →  Marathi तालुका\n"
                "  - 'number': Hindi संख्या  →  Marathi क्रमांक\n"
                "  - 'year': Hindi वर्ष / साल  →  Marathi सन\n"
                "  - 'in this matter': Hindi इस मामले में  →  Marathi या प्रकरणी\n"
                "  - 'of section': Hindi धारा X की  →  Marathi कलम X च्या\n"
                "  - Connective 'because': Hindi क्योंकि  →  Marathi कारण की\n"
            )
    return (
        "=== TEMPLATE LANGUAGE WARNING ===\n"
        f"The reference template below is written in {tlang_name} "
        f"({template_language}). The user's required output language is "
        f"{ulang_name} ({user_language}). USE THE TEMPLATE FOR STRUCTURE / "
        f"LAYOUT / SECTION ORDERING ONLY. Do NOT copy any "
        f"{tlang_name}-specific vocabulary, possessive suffixes, verbs, "
        f"or grammatical constructions into your output — write fresh in "
        f"{ulang_name}.\n\n"
        + devanagari_hint
        + "=== END TEMPLATE LANGUAGE WARNING ===\n\n"
    )


def _format_layout_block(spec: FormatSpec | None) -> str:
    """Render the FormatSpec as a compact prompt block for outline + section gen.

    Empty string when spec is None -- callers pass it as a template var so
    the prompt remains valid even when extraction failed.
    """
    if spec is None:
        return ""
    lines = [
        "TEMPLATE LAYOUT CONVENTIONS (use these for visual style only -- "
        "all facts must come from the USER QUERY / FACTS sections, NEVER "
        "from the template's example values):",
        f"- Court header alignment: {spec.court_header_alignment}",
        f"- Section labels: {spec.section_label_style} "
        f"(e.g. \"{spec.prayer_section_label}\", \"{spec.verification_label}\")",
        f"- Numbered paragraphs: {spec.paragraph_numbering}"
        + (f" -- prefix each with \"{spec.paragraph_opener}\"" if spec.paragraph_opener else ""),
        f"- Sub-points within paragraphs/prayer: {spec.sub_point_style}",
        f"- Party-block tag alignment: {spec.party_block_tag_alignment} "
        f"(divider: \"{spec.party_block_separator}\")",
    ]
    if spec.prayer_opener:
        lines.append(f"- Prayer opener (verbatim): {spec.prayer_opener}")
    if spec.verification_template:
        lines.append(f"- Verification skeleton: {spec.verification_template}")
    if spec.signature_block:
        # Indent multi-line signature for readability
        sig_lines = spec.signature_block.split("\n")
        lines.append(f"- Signature block:")
        for sl in sig_lines:
            lines.append(f"    {sl}")
    if spec.place_date_format:
        lines.append(f"- Footer (PLACE/DATE) format: {spec.place_date_format}")
    if spec.schedule_notation:
        lines.append(f"- Schedule annexure notation: {spec.schedule_notation}")
    if spec.other_conventions:
        lines.append("- Other conventions:")
        for c in spec.other_conventions:
            lines.append(f"    * {c}")
    lines.append(
        "Apply these conventions verbatim where applicable. Substitute "
        "[placeholders] for any case-specific values."
    )
    # IMPORTANT separation: this block governs LAYOUT only. Substantive
    # depth (paragraph count, detail, statutory citations, case-law
    # quotes) must follow the per-section targets the system prompt
    # specifies -- NOT the template's brevity. Real court templates are
    # terse exemplars; user drafts must be rich pleadings.
    lines.append(
        "DEPTH RULE: these conventions govern visual LAYOUT only. Do NOT "
        "let the template's brevity reduce the substantive depth of your "
        "pleading -- each section must hit its target paragraph count and "
        "include the full statutory + factual + case-law analysis the "
        "system prompt specifies."
    )
    return "\n".join(lines) + "\n\n"


# --- Step 4: Generate Each Section ---

async def _generate_section(
    query: str,
    template_text: str,
    section: SectionPlan,
    section_index: int,
    total_sections: int,
    outline: DraftOutline,
    user_language: str = "en",
    user_facts: str = "",
    case_facts: str = "",
    stance_block: str = "",
    format_block: str = "",
    start_para_num: int = 1,
    footer_kind: str = "court_filing",
    template_language: str | None = None,
    intent=None,
) -> tuple[str, int]:
    """Generate one section of the document in full detail.

    Returns (section_text, tokens_consumed).

    Prompt order is critical: user-provided facts MUST appear before the
    reference template, otherwise the LLM apes the template's placeholders
    instead of inserting the real names/dates/amounts (BUG-02). When
    `case_facts` (a pre-extracted structured bullet list) is supplied, it is
    placed ABOVE the raw text so the LLM cannot miss key entities.
    """
    outline_summary = "\n".join(
        f"  {i+1}. {s.title}" for i, s in enumerate(outline.sections)
    )

    facts_block = ""
    if case_facts.strip() or user_facts.strip():
        parts = ["USER-PROVIDED FACTS — MANDATORY VALUES YOU MUST USE VERBATIM."]
        parts.append(
            "These are the REAL names, dates, amounts, addresses, courts, "
            "and statutory references for THIS case. The reference template "
            "below contains DIFFERENT (illustrative or fictional) values — "
            "you MUST IGNORE the template's specifics in favor of these:"
        )
        if case_facts.strip():
            parts.append("\nKEY ENTITIES (extracted from user's document):\n" + case_facts.strip())
        if user_facts.strip():
            parts.append(
                "\nFULL DOCUMENT TEXT (for additional context — refer back to "
                "this for any detail not in the KEY ENTITIES list):\n"
                + user_facts[:25000]
            )
        facts_block = "\n".join(parts) + "\n\n"

    facts_reminder = (
        "USE THE USER-PROVIDED FACTS ABOVE for all names, dates, amounts, "
        "addresses, court details, statutory references. Do NOT wrap any "
        "real value from the FACTS in [brackets]. Use [placeholder] only "
        "for information that is genuinely missing from the FACTS. "
        if (case_facts.strip() or user_facts.strip()) else ""
    )

    # Section-specific override for the cause-title block. Sagar's bug #6
    # (2026-06-16): the layout rules buried at rule #11 in DRAFTING_SYSTEM_PROMPT
    # were being skimmed past — drafts came back with squashed party blocks,
    # no "IN THE MATTER OF:" header, no bolded subject line, no R/ Address
    # convention. The reminder below repeats the must-have layout in the
    # section prompt itself, where the LLM is paying full attention.
    _CAUSE_TITLE_KEYWORDS = (
        "cause title", "memo of parties", "memorandum of parties",
        "memo of party", "parties", "title of the suit",
        "title of the petition", "court header",
    )
    _section_title_lc = (section.title or "").lower()
    _is_cause_title_section = any(
        kw in _section_title_lc for kw in _CAUSE_TITLE_KEYWORDS
    )
    cause_title_override = ""
    if _is_cause_title_section:
        cause_title_override = (
            "\n\n=== CRITICAL SECTION-SPECIFIC LAYOUT OVERRIDE — "
            "THE CAUSE TITLE BLOCK MUST FOLLOW THIS EXACT MARKDOWN ===\n\n"
            "Every element gets its OWN paragraph (i.e. blank line after it).\n"
            "CommonMark renderers collapse single newlines — DO NOT use them.\n\n"
            "Mandatory layout (Sagar's bug #6, 2026-06-16):\n\n"
            "```\n"
            "**IN THE COURT OF <FORUM NAME>, AT <CITY>**\n"
            "\n"
            "**<SUIT/PETITION/COMPLAINT> NO. _______ OF <YEAR>**\n"
            "\n"
            "**IN THE MATTER OF:**\n"
            "\n"
            "<Plaintiff Full Name>\n"
            "\n"
            "Age: <age>, Occupation: <occupation>\n"
            "\n"
            "R/ Address: <residential address>\n"
            "\n"
            ".....Plaintiff / Petitioner\n"
            "\n"
            "**Versus**\n"
            "\n"
            "<Defendant Full Name>\n"
            "\n"
            "Age: <age>, Occupation: <occupation>\n"
            "\n"
            "R/ Address: <residential address>\n"
            "\n"
            ".....Defendant / Respondent\n"
            "\n"
            "**<SUBJECT HEADING — e.g. SUIT FOR COMPENSATION FOR MEDICAL "
            "NEGLIGENCE / WRIT PETITION UNDER ARTICLE 226 OF THE "
            "CONSTITUTION OF INDIA / COMPLAINT UNDER SECTION 138 NI ACT>**\n"
            "```\n\n"
            "Rules:\n"
            "1. Court name on its OWN line, BOLDED.\n"
            "2. Suit/Petition number on its OWN line, BOLDED.\n"
            "3. 'IN THE MATTER OF:' header, BOLDED, between case number "
            "   and parties — MANDATORY.\n"
            "4. Party blocks: name → age+occupation → R/ Address → "
            "   '.....Plaintiff/Defendant' designation, each its own "
            "   paragraph (blank line between).\n"
            "5. 'Versus' BOLDED as `**Versus**` (NOT in backticks, NOT in "
            "   a code fence).\n"
            "6. Subject heading at the END (after defendant designation), "
            "   BOLDED, its own paragraph.\n"
            "7. Use 'R/' (Residing at) — Indian court convention.\n"
            "8. Do NOT put the subject heading at the TOP as an H1 title.\n"
            "9. Do NOT use H1 (#) anywhere in the cause title — only "
            "   bolded markdown (`**...**`).\n"
            "10. PARTY DESIGNATIONS — the '.....Plaintiff / Petitioner' "
            "    and '.....Defendant / Respondent' lines in the template "
            "    above are PLACEHOLDERS showing common slash pairs. If "
            "    the USER DIRECTIVES block above names a specific party "
            "    label (e.g. user said 'on behalf of Respondent no. 1', "
            "    'for the Applicant', 'arguments for the Accused'), "
            "    REPLACE the slash pair with the user's exact word AND "
            "    the party number where given: e.g. '.....Respondent No. 1' "
            "    (not '.....Defendant / Respondent'), '.....Applicant' "
            "    (not '.....Plaintiff / Petitioner'). The SAME literal "
            "    label must appear in the subject heading at the bottom "
            "    of this block (e.g. 'WRITTEN STATEMENT ON BEHALF OF "
            "    RESPONDENT NO. 1' — not 'DEFENDANT NO. 1').\n"
            "11. PARTY NUMBERING — when multiple plaintiffs / petitioners "
            "    or multiple defendants / respondents exist:\n"
            "    (a) Number the plaintiffs / petitioners as 1, 2, 3, ... "
            "        sequentially.\n"
            "    (b) Number the defendants / respondents INDEPENDENTLY, "
            "        starting fresh at 1, 2, 3, ... — DO NOT continue the "
            "        plaintiff numbering into the defendant list (no "
            "        'Defendant No. 9' when there are 8 plaintiffs).\n"
            "    (c) EVERY party on each side carries a number prefix "
            "        (`1. NAME`, `2. NAME`, ...). DO NOT leave the first "
            "        party unnumbered as a 'lead' / 'head of family' / "
            "        'karta' even if the source plaint did so — every "
            "        named party gets a list number.\n"
            "    (d) Any aggregate descriptor such as 'All Nos. 1 to N "
            "        R/at <address>' MUST use the actual count N of "
            "        parties in that list. Count the parties you wrote "
            "        before emitting the descriptor; do not echo a count "
            "        from the source plaint without verifying it.\n"
            "\n"
            "Output ONLY this cause-title block for this section. No "
            "introductory paragraph, no explanation, no closing notes.\n"
            "=== END CAUSE-TITLE OVERRIDE ===\n"
        )

    # Per-section override for the "Detailed Written Submission" section
    # of a CIT(A) / ITAT / GST appellate / NCLT written submission. Sagar
    # feedback 2026-06-18: each ground inside this section must follow the
    # established appellate-argument shape — restate the ground, give
    # detailed legal argumentation, cite case laws with FULL citation
    # (party names + year + reporter + court), rebut the AO's reasoning
    # specifically, AND distinguish any case laws the AO relied upon. The
    # outline LLM produces ONE section called "Detailed Written
    # Submission" (or "Written Submission Groundwise") which then needs
    # the section LLM to internally split into "Re: Ground No. X" sub-
    # headings — the override below teaches the section LLM exactly that
    # shape, so the user doesn't get a flat narrative.
    _GROUNDWISE_WS_KEYWORDS = (
        "detailed written submission",
        "written submission groundwise",
        "groundwise written submission",
        "written submission ground-wise",
        "groundwise submission",
        "ground-wise submission",
        "submission on grounds",
    )
    _is_groundwise_ws_section = any(
        kw in _section_title_lc for kw in _GROUNDWISE_WS_KEYWORDS
    )
    if _is_groundwise_ws_section:
        cause_title_override += (
            "\n\n=== CRITICAL SECTION-SPECIFIC SHAPE — DETAILED WRITTEN "
            "SUBMISSION (GROUNDWISE) ===\n\n"
            "This section is the BULK of the written submission. It is "
            "addressed to a tax / quasi-judicial appellate authority "
            "(CIT(A) / ITAT / GST AAAR / CESTAT / NCLT / NCLAT / SAT / "
            "DRT). Each ground gets its OWN sub-heading inside this "
            "section. Use the exact pattern:\n\n"
            "    **Re: Ground No. X – <ground title verbatim from the "
            "GROUNDS OF APPEAL section>**\n\n"
            "Related grounds may be combined: "
            "`**Re: Ground No. 1, 2 & 7 – <combined title>**`.\n\n"
            "For EACH such sub-section, your argument MUST cover all of "
            "these in order:\n\n"
            "  1. **Restate the ground** in the opening sentence (1-2 "
            "lines) so the reader doesn't have to scroll back up.\n"
            "  2. **Legal argumentation** — explain WHY the ground is "
            "well-founded, citing the specific statutory provisions "
            "(with full Act + section + sub-section), CBDT Instructions "
            "/ Circulars / Notifications by number and date, and the "
            "facts on record that support the ground. 3-6 paragraphs.\n"
            "  3. **Case-law support** — cite 2-5 relevant case laws "
            "that support the appellant. Each citation MUST be in the "
            "form `*Party A v. Party B*, (Year) Reporter Volume Page "
            "(Court)` — e.g. `*Andaman Timber Industries v. CCE*, "
            "(2015) 281 ELT 421 (SC)`. After each citation, in 2-3 "
            "lines: (a) the facts of the cited case, (b) the legal "
            "principle laid down, (c) why those facts are similar to "
            "the appellant's case and the principle therefore applies "
            "in the appellant's favour. Do NOT fabricate citations — if "
            "no real case is known, omit the citation and cite the "
            "statutory provision alone.\n"
            "  4. **Rebuttal of the AO's reasoning** on this ground — "
            "open with the line: 'Rebuttal of Assessment Order:' then "
            "1-3 paragraphs explaining specifically how the AO's "
            "conclusion is wrong on facts, wrong on law, or both.\n"
            "  5. **Distinguishing AO's case laws** — if the assessment "
            "order relied on any case law (or the user's facts mention "
            "any), open with the line: 'Distinguishing the case laws "
            "relied upon by the learned AO:' then for each case the AO "
            "cited, in 2-3 lines: (a) the AO's case law (with citation), "
            "(b) the facts of that case, (c) why those facts are MATERIALLY "
            "DIFFERENT from the appellant's case so the principle does "
            "NOT apply against the appellant.\n\n"
            "If the user's stance / outline lists 5+ grounds, group "
            "related grounds (e.g. legal grounds together, then factual "
            "grounds, then without-prejudice grounds) so the section "
            "stays navigable but each ground still gets its own "
            "treatment per the 5-point shape above.\n\n"
            "DO NOT add an introduction / preamble before the first "
            "Re: Ground sub-heading. DO NOT add a summary / "
            "conclusion AFTER the last Re: Ground sub-heading — the "
            "PRAYER section that follows is where reliefs are sought.\n"
            "=== END GROUNDWISE-WS OVERRIDE ===\n"
        )

    # Source-language hint propagated into the section generator so
    # localize_prompt emits the STRICT English directive when the user
    # facts are in another script (the typical "Marathi PDF → English
    # reply notice" case). Detected once per section; cheap.
    _source_langs_section = detect_source_languages(user_facts, case_facts)

    with log_time(log, f"Section {section_index+1}/{total_sections}: {section.title}"):
        llm = get_drafting_llm()
        prompt = ChatPromptTemplate.from_messages([
            ("system", localize_prompt(
                DRAFTING_SYSTEM_PROMPT, user_language,
                intent=intent,
                source_languages=_source_langs_section,
            )),
            # FACTS FIRST — most prominent position (BUG-02)
            ("user", "{facts_block}USER INSTRUCTION:\n{query}"),
            # Stance block: shared legal lane across all parallel sections
            # (empty string when stance generation failed -- silent fallback).
            ("user", "{stance_block}Document: {doc_title}\nCourt: {court_details}"),
            ("user", "Full Document Outline:\n{outline_summary}"),
            # Template-language warning — fires only when the selected
            # template's body language differs from the user's target
            # language (template_lang_warning is "" when they match, so
            # this message is a harmless empty line in that case).
            ("user", "{template_lang_warning}"),
            # Template AFTER facts, explicitly framed as structure-only
            ("user",
             "REFERENCE TEMPLATE (use ONLY for STRUCTURE, formatting style, "
             "section ordering, and clause organization — DO NOT copy any "
             "names, dates, amounts, addresses, or factual content from the "
             "template into the draft. The template's specifics are illustrative "
             "and UNRELATED to the user's case):\n{template}"),
            # Layout-spec block: distilled visual conventions from the
            # template (alignment, numbering glyphs, prayer/verification
            # openers, signature block). Distinct from the template itself
            # so the LLM treats it as "imitate this layout verbatim" rather
            # than getting lost in the warning about template facts.
            ("user", "{format_block}"),
            ("user",
             "NOW WRITE section {section_num} of {total} IN FULL DETAIL.\n"
             "{facts_reminder}\n\n"
             "Section title (for your context only — DO NOT emit it as a "
             "heading; the assembler will inject the canonical numbered "
             "`## N. TITLE` heading itself): \"{section_title}\"\n"
             "Section description: {section_desc}\n\n"
             "DO NOT prefix your output with `## {section_title}` or any "
             "other heading line. Begin directly with the first numbered "
             "paragraph of the section body. Headings emitted by the section "
             "produce duplicated `## N. TITLE` rendering after the assembler "
             "adds its own.\n\n"
             "PARAGRAPH NUMBERING — apply ONE of these schemes based on this "
             "section's role:\n\n"
             "  (A) **Substantive body sections** — Brief Facts, Statement of "
             "Facts, Cause of Action, Issues, Grounds, Pleadings, Defences, "
             "Legal Submissions, Counter-Submissions, Reply on Merits. Use "
             "the GLOBAL paragraph counter: this section's first numbered "
             "paragraph MUST be number {start_para_num}, continue from there "
             "for subsequent paragraphs in this section, DO NOT restart at 1. "
             "Render the number in the response language's native script "
             "(Devanagari for Marathi/Hindi/Sanskrit, Tamil for Tamil, etc.).\n\n"
             "  (B) **Prayer / Relief clause** — use the prayer's OWN scheme: "
             "fresh numbering starting at (a)/(b)/(c) or (i)/(ii)/(iii) or "
             "(1)/(2)/(3). NEVER continue the global body counter — the user "
             "expects clean (a), (b), (c) or (i), (ii), (iii) for relief "
             "clauses, not (34), (35), (36) carrying over from body paras.\n\n"
             "  (C) **Verification block** — single declaratory paragraph "
             "signed by the deponent. NO paragraph number on the verification "
             "statement itself. The deponent's signature line and place/date "
             "line are unnumbered too.\n\n"
             "  (D) **Court Fee Statement, Schedule of Properties, List of "
             "Documents, Affidavit-in-Support, Memo of Parties, Annexures "
             "Index** — descriptive procedural blocks. Either use a local "
             "scheme starting fresh (1, 2, 3 OR A, B, C OR i, ii, iii — "
             "depending on the section's tradition) OR unnumbered prose. "
             "DO NOT continue the global body counter into these — the user "
             "expects 1, 2, 3 fresh per section, not 39, 40, 41 spilling "
             "over from body paragraphs.\n\n"
             "Decide which scheme applies based on the section title above. "
             "When in doubt (e.g. an unfamiliar section name), prefer the "
             "section's own local scheme starting fresh.\n\n"
             "Expected paragraphs: {est_paragraphs}\n"
             "Needs case law citations: {needs_citations}"
             "{cause_title_override}"),
        ])
        chain = prompt | llm

        # Sections generate in parallel (Semaphore(3) + asyncio.gather), so
        # token streaming here produces an interleaved, unattributed stream
        # that the frontend can't reconstruct. token_reset from one section's
        # retry also wipes valid tokens from other concurrent sections. Use
        # chain.ainvoke instead — clients still see per-section status via the
        # drafting_progress events emitted in _gen_one(). Final response
        # streaming happens during orchestrator synthesis. See
        # docs/drafting_ux_improvement_plan.md (Phase A).
        response = await asyncio.wait_for(chain.ainvoke({
            "query": query,
            "doc_title": outline.document_title,
            "court_details": outline.court_details,
            "outline_summary": outline_summary,
            "template": template_text,  # Full template (2K-9K chars, no truncation)
            "section_num": str(section_index + 1),
            "total": str(total_sections),
            "section_title": section.title,
            "section_desc": section.description,
            "start_para_num": str(start_para_num),
            "est_paragraphs": str(section.estimated_paragraphs),
            "needs_citations": (
                # Legal notices and office applications never cite case
                # law (client feedback 2026-06-19). The stance also bans
                # case law via _format_stance_for_section; this is the
                # belt-and-braces reminder at the per-section prompt level
                # where the LLM is paying full attention.
                "No — and DO NOT cite any case law / judgment / precedent "
                "even if this paragraph would naturally take one. This "
                "draft is "
                + ("a legal notice (correspondence stating position with "
                   "statutory references)" if footer_kind == "legal_notice"
                   else "an office application (request to a non-court "
                        "authority citing rules and entitlements)")
                + ". Case-law in this document is wrong output."
            ) if footer_kind in ("legal_notice", "office_application") else (
                "Yes — cite ONLY the cases listed in the DOCTRINAL STANCE "
                "block above under 'USE THESE CASE LAWS' (each is corpus-"
                "verified). If the stance lists no case relevant to this "
                "paragraph's point, cite the doctrine WITHOUT a case label "
                "(e.g. 'as consistently held by the Supreme Court in matters "
                "of partition between Class I heirs'). DO NOT invent case "
                "names or citations — even confident-sounding ones. NEVER "
                "emit [CITE: ...] placeholder markers."
            ) if section.needs_citations else "No",
            "facts_block": facts_block,
            "facts_reminder": facts_reminder,
            "stance_block": stance_block,
            "format_block": format_block,
            "cause_title_override": cause_title_override,
            "template_lang_warning": _build_template_language_warning(
                template_language, user_language,
            ),
        }), timeout=180)

    from core.token_tracker import record as _record_tokens
    tokens = _record_tokens(
        "Drafting", f"section_{section_index + 1}_{section.title[:30]}", response,
    )

    log.debug("Section completed",
              section=section.title,
              content_len=len(response.content), tokens=tokens)
    return response.content, tokens


# --- Step 5: Parallel Section Generation ---

async def _generate_sections_parallel(
    query: str,
    template_text: str,
    outline: DraftOutline,
    writer=None,
    user_language: str = "en",
    user_facts: str = "",
    case_facts: str = "",
    stance_block: str = "",
    format_block: str = "",
    footer_kind: str = "court_filing",
    template_language: str | None = None,
    intent=None,
) -> tuple[list[str], list[int], int]:
    """Generate all sections with bounded parallelism via asyncio.Semaphore.

    Limits to _SECTION_CONCURRENCY concurrent Gemini calls.
    Sections that fail get placeholder text; their indices are tracked.

    Returns: (sections_list, failed_indices, total_tokens)
    """
    sem = asyncio.Semaphore(_SECTION_CONCURRENCY)
    total = len(outline.sections)
    # Each slot: (section_text | None, tokens, error | None)
    results: list[tuple[str | None, int, Exception | None]] = [None] * total
    progress_counter = {"count": 0}

    # Precompute cumulative paragraph offsets so each substantive body
    # section knows the global paragraph number it must start numbering
    # at. PROCEDURAL sections (Prayer, Verification, Court Fee Statement,
    # Schedule, List of Documents, Affidavit, Memo of Parties, Annexures
    # Index) use their OWN local numbering scheme — they don't consume
    # slots in the global body counter and don't shift downstream
    # sections' offsets. The per-section prompt rules (A-D) decide
    # which scheme to apply based on the section title.
    _PROCEDURAL_TITLE_KEYWORDS = (
        "prayer", "relief", "verification", "court fee", "court-fee",
        "schedule", "list of documents", "list of document",
        "affidavit", "memo of parties", "annexures index",
        "interim application", "ia under order",
    )

    def _is_procedural(title: str) -> bool:
        t = (title or "").lower()
        return any(k in t for k in _PROCEDURAL_TITLE_KEYWORDS)

    start_para_offsets: list[int] = []
    running = 1
    for plan in outline.sections:
        start_para_offsets.append(running)
        # Procedural sections don't consume body counter slots, so the
        # running counter stays put for the next section.
        if not _is_procedural(plan.title):
            running += max(1, plan.estimated_paragraphs)
    # start_para_offsets[i] = first global paragraph number for section i
    # (only meaningful for substantive body sections; procedural sections
    # are told to use their own local scheme via prompt rules B/C/D)

    async def _gen_one(i: int, plan: SectionPlan):
        async with sem:
            progress_counter["count"] += 1
            # `section`: NATURAL document index (1-based, stable across runs).
            # The frontend can render a fixed list of N sections and update
            # each slot's status as events arrive in arbitrary order (BUG-13).
            #
            # `start_order`: original ordering by which a section's coroutine
            # actually acquired the semaphore — useful for debugging only.
            section_num = i + 1
            start_order = progress_counter["count"]
            if writer:
                writer({
                    "type": "drafting_progress",
                    "section": section_num,
                    "index": i,                # 0-based for direct array indexing
                    "start_order": start_order,
                    "total": total,
                    "title": plan.title,
                    "status": "in_progress",
                })
            try:
                text, tokens = await _generate_section(
                    query, template_text, plan, i, total, outline, user_language,
                    user_facts=user_facts,
                    case_facts=case_facts,
                    stance_block=stance_block,
                    format_block=format_block,
                    start_para_num=start_para_offsets[i],
                    footer_kind=footer_kind,
                    template_language=template_language,
                    intent=intent,
                )
                results[i] = (text, tokens, None)
                if writer:
                    writer({
                        "type": "drafting_progress",
                        "section": section_num,
                        "index": i,
                        "start_order": start_order,
                        "total": total,
                        "title": plan.title,
                        "status": "completed",
                        "char_count": len(text),
                    })
            except Exception as e:
                # Mandatory procedural sections (Schedule, List of Documents,
                # Verification, Affidavit, Memo of Parties, etc.) get ONE
                # server-side retry before we leak the "could not be
                # generated. Click Continue" placeholder to the user. These
                # sections are not optional — the doc isn't court-ready
                # without them — and the most common failure mode (timeout
                # / transient rate-limit) is fixed by a second attempt. The
                # body sections do NOT auto-retry: they're the heaviest
                # generations, the user can re-run via Continue, and we
                # don't want to double-spend the semaphore on long-tail
                # body failures.
                if _is_procedural(plan.title):
                    log.warning(
                        "Procedural section failed, retrying",
                        section=i + 1, title=plan.title,
                        error_type=type(e).__name__,
                        error=str(e)[:200],
                    )
                    try:
                        text, tokens = await _generate_section(
                            query, template_text, plan, i, total, outline, user_language,
                            user_facts=user_facts,
                            case_facts=case_facts,
                            stance_block=stance_block,
                            format_block=format_block,
                            start_para_num=start_para_offsets[i],
                            footer_kind=footer_kind,
                            template_language=template_language,
                            intent=intent,
                        )
                        results[i] = (text, tokens, None)
                        log.info("Procedural section recovered on retry",
                                 section=i + 1, title=plan.title)
                        if writer:
                            writer({
                                "type": "drafting_progress",
                                "section": section_num,
                                "index": i,
                                "start_order": start_order,
                                "total": total,
                                "title": plan.title,
                                "status": "completed",
                                "char_count": len(text),
                            })
                        return
                    except Exception as e2:
                        # Fall through with the retry error so the leaked
                        # placeholder + log surface the SECOND failure
                        # (more diagnostic than the first if root cause is
                        # not transient).
                        e = e2
                log.error("Section failed", exc_info=True, section=i + 1,
                          title=plan.title, error_type=type(e).__name__,
                          error=str(e)[:200])
                results[i] = (None, 0, e)
                if writer:
                    writer({
                        "type": "drafting_progress",
                        "section": section_num,
                        "index": i,
                        "start_order": start_order,
                        "total": total,
                        "title": plan.title,
                        "status": "failed",
                        "error": str(e)[:200],
                    })

    tasks = [_gen_one(i, plan) for i, plan in enumerate(outline.sections)]
    await asyncio.gather(*tasks)

    # Collect results in order
    sections: list[str] = []
    failed_indices: list[int] = []
    total_tokens = 0

    for i, (text, tokens, err) in enumerate(results):
        if err is not None:
            placeholder = (
                f"\n\n---\n\n"
                f"**[Section {i + 1}: {outline.sections[i].title} -- could not be generated. "
                f"Click \"Continue\" below to complete this section.]**"
                f"\n\n---\n"
            )
            sections.append(placeholder)
            failed_indices.append(i)
        else:
            sections.append(text)
            total_tokens += tokens

    return sections, failed_indices, total_tokens


# --- Step 6: Assemble Document ---
#
# Footer label translations for the 10 P1/P2 Indian languages. This is
# prompt-side translation data, not routing logic — the labels are
# scaffolding the LLM is asked to render in the target language. Removing
# the dict and trusting the self-refine critic to translate the footer
# caused a measurable Marathi WS regression (English "Place:/Date:" leaked
# all the way to the user-visible final response). Per-language footer
# labels are higher-leverage than paragraph numerals for refinement
# because they appear once and the critic is more likely to under-prioritise
# them. The dict stays. Adding a new language is one entry.
_FOOTER_LABELS: dict[str, dict[str, str]] = {
    "hi": {
        "place": "स्थान",
        "date": "दिनांक",
        "signature": "याचिकाकर्ता/आवेदक के हस्ताक्षर",
        "through_counsel": "अधिवक्ता के माध्यम से",
        "name_of_advocate": "अधिवक्ता का नाम",
        "enrolment_no": "नामांकन संख्या",
        "address_of_advocate": "अधिवक्ता का पता",
    },
    "bn": {
        "place": "স্থান",
        "date": "তারিখ",
        "signature": "আবেদনকারীর স্বাক্ষর",
        "through_counsel": "আইনজীবীর মাধ্যমে",
        "name_of_advocate": "আইনজীবীর নাম",
        "enrolment_no": "তালিকাভুক্তি নং",
        "address_of_advocate": "আইনজীবীর ঠিকানা",
    },
    "ta": {
        "place": "இடம்",
        "date": "தேதி",
        "signature": "மனுதாரர்/விண்ணப்பதாரர் கையொப்பம்",
        "through_counsel": "வழக்கறிஞர் மூலம்",
        "name_of_advocate": "வழக்கறிஞரின் பெயர்",
        "enrolment_no": "பதிவு எண்",
        "address_of_advocate": "வழக்கறிஞரின் முகவரி",
    },
    "te": {
        "place": "స్థలం",
        "date": "తేదీ",
        "signature": "పిటిషనర్/దరఖాస్తుదారు సంతకం",
        "through_counsel": "న్యాయవాది ద్వారా",
        "name_of_advocate": "న్యాయవాది పేరు",
        "enrolment_no": "నమోదు సంఖ్య",
        "address_of_advocate": "న్యాయవాది చిరునామా",
    },
    "mr": {
        "place": "ठिकाण",
        "date": "दिनांक",
        "signature": "याचिकाकर्ता/अर्जदाराच्या सह्या",
        "through_counsel": "वकिलांमार्फत",
        "name_of_advocate": "वकिलाचे नाव",
        "enrolment_no": "नोंदणी क्रमांक",
        "address_of_advocate": "वकिलाचा पत्ता",
    },
    "kn": {
        "place": "ಸ್ಥಳ",
        "date": "ದಿನಾಂಕ",
        "signature": "ಅರ್ಜಿದಾರ/ಅರ್ಜಿದಾರರ ಸಹಿ",
        "through_counsel": "ವಕೀಲರ ಮೂಲಕ",
        "name_of_advocate": "ವಕೀಲರ ಹೆಸರು",
        "enrolment_no": "ನೋಂದಣಿ ಸಂಖ್ಯೆ",
        "address_of_advocate": "ವಕೀಲರ ವಿಳಾಸ",
    },
    "ml": {
        "place": "സ്ഥലം",
        "date": "തീയതി",
        "signature": "ഹർജിക്കാരന്റെ/അപേക്ഷകന്റെ ഒപ്പ്",
        "through_counsel": "അഭിഭാഷകൻ വഴി",
        "name_of_advocate": "അഭിഭാഷകന്റെ പേര്",
        "enrolment_no": "എൻറോൾമെന്റ് നം",
        "address_of_advocate": "അഭിഭാഷകന്റെ വിലാസം",
    },
    "gu": {
        "place": "સ્થળ",
        "date": "તારીખ",
        "signature": "અરજદાર/અરજકર્તાની સહી",
        "through_counsel": "વકીલ મારફત",
        "name_of_advocate": "વકીલનું નામ",
        "enrolment_no": "નોંધણી નંબર",
        "address_of_advocate": "વકીલનું સરનામું",
    },
    "pa": {
        "place": "ਸਥਾਨ",
        "date": "ਮਿਤੀ",
        "signature": "ਅਰਜ਼ੀਕਰਤਾ ਦੇ ਦਸਤਖਤ",
        "through_counsel": "ਵਕੀਲ ਰਾਹੀਂ",
        "name_of_advocate": "ਵਕੀਲ ਦਾ ਨਾਮ",
        "enrolment_no": "ਨਾਮਾਂਕਣ ਨੰਬਰ",
        "address_of_advocate": "ਵਕੀਲ ਦਾ ਪਤਾ",
    },
    "ur": {
        "place": "جگہ",
        "date": "تاریخ",
        "signature": "درخواست گزار کے دستخط",
        "through_counsel": "وکیل کے ذریعے",
        "name_of_advocate": "وکیل کا نام",
        "enrolment_no": "اندراج نمبر",
        "address_of_advocate": "وکیل کا پتہ",
    },
}


# Per-(footer_kind, user_language) localized-footer cache. Each entry is the
# already-translated footer text for that combination, so subsequent drafts
# that use the same footer in the same language pay zero LLM cost.
_LOCALIZED_FOOTER_CACHE: dict[tuple[str, str], str] = {}


_FOOTER_LOCALIZE_SYSTEM = """You are a translator for Indian legal-document
boilerplate. You are given a short English footer block (signature line,
witness lines, place/date lines, etc.) and a target language.

Translate the prose to {language_name} ({language_code}). RULES:

1. PRESERVE every `[bracketed placeholder]` VERBATIM — these are slots the
   user will fill in later (names, addresses, contact numbers, etc.).
   Do NOT translate the text inside brackets, do NOT remove the brackets,
   do NOT change their position.
2. PRESERVE every `___________` blank line and every `Sd.` signature
   marker EXACTLY as printed.
3. PRESERVE markdown bolding (`**...**`) and bullet/number markers
   ("1.", "2.") — but render the number in the target script when the
   target language uses a distinct digit family
   (Devanagari १, २ for Hindi/Marathi/Sanskrit; Bengali ১, ২ for Bengali;
   Tamil ௧, ௨ for Tamil; Telugu ౧, ౨ for Telugu; Kannada ೧, ೨;
   Malayalam ൧, ൨; Gujarati ૧, ૨; Gurmukhi ੧, ੨; Eastern Arabic ۱, ۲
   for Urdu).
4. Translate ALL standalone labels and phrases — "Place", "Date",
   "Yours faithfully", "Yours sincerely", "Through Counsel",
   "Signature of the Applicant", "Name of Advocate", "Enrolment No.",
   "WITNESSES", "IN WITNESS WHEREOF", "the parties have executed this
   Agreement on the date first written above", "Signed by the Testator in
   our presence and signed by us in the presence of the Testator and of
   each other", "For and on behalf of the Appellant",
   "[Name of Advocate]" placeholder LABEL (translate the LABEL inside the
   brackets while keeping the brackets — e.g. "[Name of Advocate]" →
   "[वकील का नाम]" in Hindi). Use the conventional Indian legal-
   correspondence register for the target language — match Maharashtra
   bar / Delhi bar / Madras bar conventions as appropriate.
5. Output ONLY the translated footer text. NO preamble, NO explanation,
   NO surrounding commentary, NO code fences.
"""


def _build_footer(footer_kind: str, user_language: str, intent=None) -> str:
    """Return the appropriate footer block for a given artifact kind.

    Always returns the ENGLISH skeleton for kinds other than court_filing.
    The async `_localize_footer_block` is then called from the assembler
    to translate non-English drafts via a cached Gemini Flash Lite call.
    court_filing already uses `_FOOTER_LABELS` (a hand-maintained dict)
    so it bypasses the LLM path entirely.

    `intent` carries the user's `arguments_for_party` so the court-filing
    signature label matches the speaking party — a Written Statement
    drafted on behalf of the defendant ends with "Signature of the
    Defendant/Respondent", not the generic "Petitioner/Applicant" that
    leaked previously. Non-English court filings fall back to the
    language's generic signature label unless the labels dict carries a
    party-specific key (e.g. `signature_defendant`); add those entries
    incrementally as users complain — DO NOT block the English fix on
    full translation coverage.

    Future-proof principle: do NOT add another per-language hardcoded dict
    every time a new footer kind lands. Adding a new footer kind = one
    English skeleton here + zero per-language work; the LLM localizer
    handles every Indian language uniformly.
    """
    if footer_kind == "none":
        return ""
    if footer_kind == "legal_notice":
        # Legal notices are sent by registered post and signed by
        # counsel; no "Petitioner/Applicant" line, no court filing.
        return (
            "Yours sincerely,\n\n"
            "Sd.\n"
            "**[Name of Advocate]**\n"
            "[Enrolment No.]\n"
            "[Address of Advocate]\n"
            "[Contact Details]"
        )
    if footer_kind == "office_application":
        # Office application: letter addressed to a non-court authority
        # (PIO under RTI Act, Tahsildar, SDM, Collector, employer, bank,
        # housing society, university, regulator). Signed by the
        # APPLICANT directly, not by counsel.
        return (
            "Yours faithfully,\n\n"
            "Sd.\n\n"
            "**[Name of Applicant]**\n"
            "[Full Address of Applicant]\n"
            "[Contact Number]\n\n"
            "Place: ___________\n\n"
            "Date: ___________"
        )
    if footer_kind == "police_complaint":
        # Police-station FIR registration application (Section 154 CrPC /
        # Section 173 BNSS) — addressed to SHO / Police Inspector, signed
        # by the COMPLAINANT, not by counsel. Place + date are mandatory
        # because police use them for FIR sequencing.
        return (
            "Yours faithfully,\n\n"
            "Sd.\n"
            "**[Name of Complainant]**\n"
            "[Father's / Husband's Name]\n"
            "[Full Address of Complainant]\n"
            "[Contact Number]\n\n"
            "Place: ___________\n\n"
            "Date: ___________"
        )
    if footer_kind == "tax_submission":
        # Tax / quasi-judicial appellate written submission (CIT(A) /
        # ITAT / GST appellate / NCLT / SAT / DRT, etc.) — signed by the
        # APPELLANT directly, not through counsel (counsel may also sign
        # but the convention is the appellant's name + place/date below
        # "For and on behalf of the Appellant").
        return (
            "For and on behalf of the Appellant,\n\n"
            "Sd.\n\n"
            "**(Name of Appellant)**\n\n"
            "Place: ___________\n\n"
            "Date: ___________"
        )
    if footer_kind == "agreement":
        return (
            "**IN WITNESS WHEREOF**, the parties have executed this "
            "Agreement on the date first written above.\n\n"
            "**FIRST PARTY:**\n"
            "Sd. _________________________\n"
            "[Name of First Party]\n\n"
            "**SECOND PARTY:**\n"
            "Sd. _________________________\n"
            "[Name of Second Party]\n\n"
            "**WITNESSES:**\n\n"
            "1. Sd. _________________________\n"
            "   [Name and Address of Witness 1]\n\n"
            "2. Sd. _________________________\n"
            "   [Name and Address of Witness 2]"
        )
    if footer_kind == "will":
        # Indian Succession Act, 1925 Section 63 — testator + 2 witnesses.
        return (
            "**IN WITNESS WHEREOF**, I have set my hand to this WILL on "
            "the date first written above.\n\n"
            "Sd. _________________________\n"
            "[Name of Testator]\n\n"
            "**Signed by the Testator in our presence and signed by us in "
            "the presence of the Testator and of each other:**\n\n"
            "1. Sd. _________________________\n"
            "   [Name and Address of Attesting Witness 1]\n\n"
            "2. Sd. _________________________\n"
            "   [Name and Address of Attesting Witness 2]"
        )
    # Default: court_filing
    labels = _FOOTER_LABELS.get(user_language, {})
    place_label = labels.get("place", "Place")
    date_label = labels.get("date", "Date")
    party = getattr(intent, "arguments_for_party", "none") if intent else "none"
    _party_english_defaults = {
        "plaintiff": "Signature of the Plaintiff/Petitioner",
        "defendant": "Signature of the Defendant/Respondent",
        "both":      "Signature of the Petitioner/Applicant",
        "none":      "Signature of the Petitioner/Applicant",
    }
    signature_label = (
        labels.get(f"signature_{party}")
        or labels.get("signature")
        or _party_english_defaults.get(party, _party_english_defaults["none"])
    )
    through_counsel_label = labels.get("through_counsel", "Through Counsel")
    name_advocate_label = labels.get("name_of_advocate", "Name of Advocate")
    enrolment_label = labels.get("enrolment_no", "Enrollment No.")
    address_advocate_label = labels.get("address_of_advocate", "Address of Advocate")
    return (
        f"**{place_label}:** [Place]\n\n"
        f"**{date_label}:** [Date]\n\n"
        f"**{signature_label}**\n\n"
        f"{through_counsel_label}:\n\n"
        f"**[{name_advocate_label}]**\n"
        f"[{enrolment_label}]\n"
        f"[{address_advocate_label}]"
    )


async def _localize_footer_block(
    footer_text: str,
    footer_kind: str,
    user_language: str,
) -> str:
    """Translate a footer skeleton to user_language via Gemini Flash Lite.

    Cached per (footer_kind, user_language). Returns the English skeleton
    unchanged when:
      * user_language is English / unsupported, OR
      * the LLM call fails (timeout / network / parse) — we'd rather show
        an English footer than no footer at all.
    """
    if not footer_text:
        return footer_text
    if user_language == "en" or user_language not in SUPPORTED_LANGUAGES:
        return footer_text

    cache_key = (footer_kind, user_language)
    if cache_key in _LOCALIZED_FOOTER_CACHE:
        log.debug("Localized footer cache hit",
                  footer_kind=footer_kind, lang=user_language)
        return _LOCALIZED_FOOTER_CACHE[cache_key]

    language_name = SUPPORTED_LANGUAGES[user_language]
    with log_time(log, f"Footer localization ({footer_kind}→{user_language})"):
        try:
            llm = get_gemini_flash(temperature=0.0)
            system = _FOOTER_LOCALIZE_SYSTEM.format(
                language_name=language_name,
                language_code=user_language,
            )
            prompt = ChatPromptTemplate.from_messages([
                ("system", system),
                ("user",
                 "Translate this footer block to "
                 f"{language_name}. Preserve placeholders and blanks "
                 "verbatim. Output ONLY the translated text.\n\n"
                 "FOOTER:\n{footer}"),
            ])
            chain = prompt | llm
            response = await asyncio.wait_for(
                chain.ainvoke({"footer": footer_text}),
                timeout=20,
            )
        except Exception as e:
            log.warning(
                "Footer localization failed; returning English skeleton",
                footer_kind=footer_kind, lang=user_language,
                error=str(e)[:200],
            )
            return footer_text

        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting",
                       f"footer_localize_{footer_kind}_{user_language}",
                       response)
        translated = (response.content or "").strip()
        if not translated:
            log.warning("Footer localizer returned empty content; keeping English",
                        footer_kind=footer_kind, lang=user_language)
            return footer_text
        # Strip occasional code fences the LLM emits despite the prompt
        if translated.startswith("```"):
            translated = translated.strip("`").strip()
            if translated.startswith(("markdown", "text")):
                translated = translated.split("\n", 1)[1] if "\n" in translated else translated
        _LOCALIZED_FOOTER_CACHE[cache_key] = translated
        log.info("Footer localized",
                 footer_kind=footer_kind, lang=user_language,
                 src_chars=len(footer_text), dst_chars=len(translated))
        return translated


async def _assemble_document(
    outline: DraftOutline,
    sections: list[str],
    user_language: str = "en",
    stance: "DoctrinalStance | None" = None,
    intent=None,
) -> str:
    """Combine all sections into the final document with proper structure.

    Ensures each section has a heading (injects from outline if LLM omitted it).
    Picks the right footer based on `stance.footer_kind` (see `_build_footer`).
    Falls back to court_filing footer when no stance is available, which
    preserves the prior default for backward compatibility.

    Sagar's bug #6 (2026-06-16): for court-filing drafts, the subject
    heading (e.g. "SUIT FOR COMPENSATION FOR MEDICAL NEGLIGENCE") goes
    at the END of the cause-title block as a bolded paragraph, NOT at
    the top as an H1. The previous `# {document_title}` markup is dropped.

    Bug #6 followup (2026-06-17): for legal notices, replies to legal
    notices, and agreements/deeds/wills, the title belongs at the TOP
    of court_details (the LLM emits it inside the layout) — DO NOT
    re-append it at the end. The subject-append logic below is therefore
    gated on footer_kind == "court_filing".
    """
    cause_title = outline.court_details.rstrip()
    subject = (outline.document_title or "").strip()
    # Only append the subject heading at the END for court-filing drafts.
    # Notice/agreement/police_complaint layouts put the title at the TOP
    # of court_details (Layouts B/C/D in DraftOutline.court_details field
    # doc); appending again would produce duplicated title text and
    # pollute the layout with court-filing scaffolding.
    _footer_kind_for_subject = "court_filing"
    if stance is not None:
        _footer_kind_for_subject = (
            getattr(stance, "footer_kind", "court_filing") or "court_filing"
        )
    if (subject
            and _footer_kind_for_subject == "court_filing"
            and subject.upper() not in cause_title.upper()):
        cause_title += f"\n\n**{subject.upper()}**"

    parts = [
        cause_title,
        "---",
    ]

    for i, (section_plan, section_text) in enumerate(zip(outline.sections, sections)):
        text = section_text.strip()

        # Strip any leading `#`-prefixed lines from the section LLM's output
        # (and the optional bare numeric/title lines that sometimes follow)
        # before injecting our canonical numbered heading. Previously the
        # assembler only injected when `text.startswith("#")` was false, so
        # when the section LLM echoed its own `## {section_title}` line
        # (the section prompt at drafting.py:3347 literally includes this
        # in the user message), the assembler skipped its prefix injection
        # AND the LLM-emitted heading stuck around without our numbering.
        # Symptom in the Avachat writ: Sec 3, 5, 8 had numbered headings
        # (`## 3. ARGUMENTS …`) but Sec 1, 2, 4, 6, 7, 9, 10 didn't.
        # Symptom on the Bombay HC appeal re-test: same pattern (Sec 4
        # rendered the title twice with a stray `4.` between).
        # We now strip up to 3 leading heading-shaped lines and ALWAYS
        # inject the canonical heading.
        _stripped_lines = 0
        while _stripped_lines < 3 and text:
            line, _, rest = text.partition("\n")
            stripped = line.strip()
            # `## ...`, `# ...`, bare `N.` numeric stub, or title echo.
            if (
                stripped.startswith("#")
                or re.match(r"^\d+\.\s*$", stripped)
                or stripped == section_plan.title.strip()
                or stripped == section_plan.title.strip().upper()
            ):
                text = rest.lstrip()
                _stripped_lines += 1
                continue
            break

        # Now inject the canonical numbered heading. Localize the section
        # number to the user's digit script (Devanagari १. in Hindi, Tamil
        # ௧. in Tamil, …) and strip any numeric prefix from the title —
        # otherwise you get a doubled-prefix heading like
        # "## 1. १. याचिका...". Bug report 2026-06-19: Hindi draft
        # headings showed "1. १. याचिका के तथ्य" / "2. २. स्वामित्व का आधार"
        # all the way down.
        num = localize_number(i + 1, user_language)
        clean_title = strip_leading_numeric_prefix(section_plan.title)
        text = f"## {num}. {clean_title}\n\n{text}"
        parts.append(text)

    footer_kind = "court_filing"
    if stance is not None:
        footer_kind = getattr(stance, "footer_kind", "court_filing") or "court_filing"

    footer_block = _build_footer(footer_kind, user_language, intent=intent)
    # Localize the footer when the user wants a non-English response and
    # the footer kind doesn't already use the _FOOTER_LABELS labels dict
    # (court_filing handles its own localization). Cached per
    # (footer_kind, user_language) so this is a one-time cost per process.
    if (footer_block
            and footer_kind != "court_filing"
            and user_language != "en"):
        footer_block = await _localize_footer_block(
            footer_block, footer_kind, user_language,
        )
    if footer_block:
        parts.append("---")
        parts.append(footer_block)

    return "\n\n".join(parts)


# --- Continue Draft (regenerate failed sections) ---

async def continue_draft_node(state: LegalAgentState) -> dict:
    """Continue an incomplete draft by regenerating only the failed sections.

    Reads continuation metadata from state (outline, template, completed sections)
    and only generates the sections that previously failed.
    """
    continuation = state.get("draft_continuation")
    if not continuation:
        log.error("continue_draft called but no continuation data in state")
        return {"agent_results": {"Drafting": AgentResult(
            agent_name="Drafting", content="",
            sources=[], tokens_consumed=0,
            error="No continuation data available",
        )}}

    query = continuation["query"]
    template_text = continuation["template_text"]
    template_source = continuation["template_source"]
    completed_sections = continuation["completed_sections"]
    failed_indices = continuation["failed_indices"]
    outline = DraftOutline.model_validate(continuation["outline"])
    # Continuation: persisted facts (if first attempt had a file/context attached)
    user_facts = continuation.get("user_facts", "")
    case_facts = continuation.get("case_facts", "")
    footer_kind = continuation.get("footer_kind", "court_filing") or "court_filing"
    template_language = continuation.get("template_language")

    log.info("Continue draft started",
             failed_sections=len(failed_indices),
             total_sections=len(outline.sections),
             facts_chars=len(user_facts))

    # Acquire concurrency slot; emit queue_status SSE event if at capacity
    try:
        from langgraph.config import get_stream_writer
        _writer = get_stream_writer()
    except (RuntimeError, ImportError):
        _writer = None
    if _AGENT_SEMAPHORE.locked():
        log.warning("Concurrency limit reached, queuing ContinueDraft request")
        if _writer:
            _writer({"type": "queue_status", "status": "queued",
                     "message": "Drafting agent is busy, queuing your request..."})
    await _AGENT_SEMAPHORE.acquire()

    try:
        try:
            from langgraph.config import get_stream_writer
            writer = get_stream_writer()
        except (RuntimeError, ImportError):
            writer = None

        # Reconstruct sections list from completed sections (indexed by string key)
        sections: list[str] = [""] * len(outline.sections)
        for idx_str, text in completed_sections.items():
            idx = int(idx_str)
            if 0 <= idx < len(sections):
                sections[idx] = text

        total_tokens = 0
        new_failed: list[int] = []
        progress_step = 0

        for idx in failed_indices:
            progress_step += 1
            section_plan = outline.sections[idx]

            if writer:
                writer({
                    "type": "drafting_progress",
                    "section": progress_step,
                    "total": len(failed_indices),
                    "title": f"(Retry) {section_plan.title}",
                })

            try:
                section_text, section_tokens = await _generate_section(
                    query, template_text, section_plan,
                    idx, len(outline.sections), outline,
                    state.get("user_language", "en"),
                    user_facts=user_facts,
                    case_facts=case_facts,
                    intent=state.get("user_intent"),
                    footer_kind=footer_kind,
                    template_language=template_language,
                )
                sections[idx] = section_text
                total_tokens += section_tokens
            except Exception as sec_err:
                log.error("Continue: section still failed", exc_info=True,
                          section=idx + 1, title=section_plan.title,
                          error_type=type(sec_err).__name__,
                          error=str(sec_err))
                sections[idx] = (
                    f"\n\n---\n\n"
                    f"**[Section {idx + 1}: {section_plan.title} -- "
                    f"could not be generated after retry.]**"
                    f"\n\n---\n"
                )
                new_failed.append(idx)

        if new_failed and writer:
            writer({
                "type": "draft_incomplete",
                "failed_sections": [
                    {"index": idx, "title": outline.sections[idx].title}
                    for idx in new_failed
                ],
                "total_sections": len(outline.sections),
                "completed_sections": len(outline.sections) - len(new_failed),
            })

        full_draft = await _assemble_document(
            outline, sections, state.get("user_language", "en"),
            intent=state.get("user_intent"),
        )

        # Run the same validate + renumber chain the main drafting path uses
        # so continued drafts also get the duplicate-heading + empty-paragraph
        # + global-renumber treatment. The stance object isn't reachable from
        # continuation state today, so we pass None — validate_draft's stance-
        # specific checks (statute pair warnings) are skipped, mojibake and
        # citation cleanup still run.
        full_draft, _draft_warnings = validate_draft(full_draft, None)
        full_draft = _renumber_global_paragraphs(full_draft)

        if new_failed:
            log.warning("Continue draft: some sections still failed",
                        still_failed=new_failed)
        else:
            log.info("Continue draft completed successfully",
                     sections_regenerated=len(failed_indices))

        template_display = os.path.splitext(os.path.basename(template_source))[0]
        result = AgentResult(
            agent_name="Drafting",
            content=full_draft,
            sources=[SourceMetadata(
                source_type="drafting",
                title=template_display,
                content=[template_text[:300]],
                file_name=template_source,
                agent_name="Drafting",
                template_type=template_display,
            )],
            tokens_consumed=total_tokens,
        )

        state_update: dict = {"agent_results": {"Drafting": result}}
        if new_failed:
            state_update["draft_continuation"] = {
                "outline": outline.model_dump(),
                "template_text": template_text,
                "template_source": template_source,
                "query": query,
                "completed_sections": {
                    str(i): sections[i] for i in range(len(sections))
                    if i not in new_failed
                },
                "failed_indices": new_failed,
            }
        else:
            state_update["draft_continuation"] = None

    except Exception as e:
        log.error("Continue draft failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Drafting", content="",
            sources=[], tokens_consumed=0, error=str(e),
        )
        state_update = {"agent_results": {"Drafting": result}}
    finally:
        _AGENT_SEMAPHORE.release()

    return state_update


# --- Agent Node ---

async def drafting_node(state: LegalAgentState) -> dict:
    """Multi-step legal draft generation pipeline.

    Flow:
    1. Hybrid search ES "drafting" index (BM25 + kNN vector, top 15)
    2. GPT-4o-mini selects best template (with content previews + validation)
    3. Fetch full template document (no truncation)
    4. Generate document outline (Gemini 2.5 Flash, structured output, max 12 sections)
    5. Generate sections in parallel (Gemini 2.5 Flash, 3 concurrent via semaphore)
    6. Assemble with section titles + court filing footer
    """
    agent_queries = state.get("agent_queries", {})
    query = agent_queries.get("Drafting") or state.get("query") or state.get("original_query", "")
    user_context = state.get("user_context", "")
    user_language = state.get("user_language", "en")
    intent_obj = state.get("user_intent")
    integration_ctx = IntegrationContextData.from_state(state)
    fc = FileContextData.from_state(state)

    # Collect ALL fact sources (uploaded PDF, pasted long-form context,
    # third-party integration content) into a single `user_facts` blob.
    # These are passed to outline + section gen as a SEPARATE field so the
    # template doesn't dominate the prompt (BUG-02). The clean `query`
    # (user's actual instruction) is what we use for template search and
    # selection — keeping the BM25 query small and on-target (BUG-16).
    fact_blocks: list[str] = []
    if fc and fc.inline_text:
        # Uploaded document (PDF, DOCX, TXT) extracted text
        fact_blocks.append(
            f"[Uploaded document text — {', '.join(fc.file_names) or 'attached'}]\n"
            f"{fc.inline_text[:30000]}"
        )
    if user_context:
        # Long-form pasted content embedded in the user's typed query
        fact_blocks.append(f"[Pasted context]\n{user_context[:30000]}")
    if integration_ctx and integration_ctx.has_content:
        # Content fetched from Google Docs / Notion
        fact_blocks.append(integration_ctx.as_prompt_prefix().rstrip())
    user_facts = "\n\n".join(fact_blocks)

    log.info("Agent started", query=query[:100],
             has_user_context=bool(user_context),
             has_file_context=bool(fc and fc.inline_text),
             has_integration_context=bool(integration_ctx and integration_ctx.has_content),
             facts_chars=len(user_facts),
             using_agent_query="Drafting" in agent_queries)

    # Acquire concurrency slot; emit queue_status SSE event if at capacity
    try:
        from langgraph.config import get_stream_writer as _get_writer
        _dwriter = _get_writer()
    except (RuntimeError, ImportError):
        _dwriter = None
    if _AGENT_SEMAPHORE.locked():
        log.warning("Concurrency limit reached, queuing Drafting request")
        if _dwriter:
            _dwriter({"type": "queue_status", "status": "queued",
                      "message": "Drafting agent is busy, queuing your request..."})
    await _AGENT_SEMAPHORE.acquire()

    try:
        es = get_es_client()
        index = ES_INDICES["drafting"]

        # Step 0: Extract structured key facts from any uploaded document /
        # pasted context. The structured list is prepended to outline +
        # section-gen prompts so the LLM uses real names/dates/amounts
        # instead of [Plaintiff Name] / [Loan Amount] placeholders. (BUG-02)
        case_facts = ""
        if user_facts:
            progress("drafting", "Extracting key facts from your document...", step="extract")
            case_facts = await _extract_case_facts(user_facts)

        # Step 0.5: Classify doc-type BEFORE template search.
        # The 2026-06-20 census of the drafting index confirmed 0 office-letter
        # templates exist — so for office_letter / police_complaint /
        # legal_notice we skip ES entirely and use a synthetic skeleton.
        progress("drafting", "Classifying document type...", step="classify")
        doc_type = await _classify_doc_type(query, case_facts)
        progress("drafting", f"Doc type: {doc_type}", substep=True, step="classify")

        use_skeleton = doc_type in DOC_TYPES_USE_SKELETON

        # Path-tracking flags. Initialized here so they exist in every
        # branch (skeleton / corpus-hit / web-fallback / generic-fallback)
        # and can be inspected by post-processing (source attribution,
        # outline gen, format-spec skip).
        used_web_template = False
        used_generic_skeleton = False
        web_template_urls: list[str] = []
        match_quality = "good"      # corpus-hit path overrides this
        match_reason = ""           # corpus-hit path overrides this

        if use_skeleton:
            # No ES search. Use the synthetic skeleton lifted from Layout B/D/F.
            template_text = SYNTHETIC_SKELETONS[doc_type]
            selected_source = f"<synthetic:{doc_type}>"
            template_language = user_language
            format_block = ""
            log.info("Bypassing template search for non-court doc type",
                     doc_type=doc_type,
                     reason="drafting index has 0 letter-shaped templates")
        else:
            # Step 1: Hybrid search for templates (BM25 + kNN, top 15)
            progress("drafting", "Searching for document templates...", step="search")
            hits = await _search_templates(query)

            if not hits:
                log.warning("No templates found")
                return {
                    "agent_results": {"Drafting": AgentResult(
                        agent_name="Drafting",
                        content="",
                        sources=[],
                        tokens_consumed=0,
                        error="No matching templates found in our database.",
                    )},
                }

            progress("drafting", f"Found {len(hits)} matching templates", found=len(hits), substep=True, step="search")
            log.info("Template candidates found", count=len(hits))

            # Step 2: Select best template (previews + case-context + validation)
            progress("drafting", "Selecting best template...", step="select")
            selected_source, all_paths, match_quality, match_reason = await asyncio.to_thread(
                _select_best_template, query, hits, user_facts, doc_type,
            )
            template_display_name = os.path.splitext(os.path.basename(selected_source))[0]
            progress(
                "drafting",
                f"Selected: {template_display_name[:60]} ({match_quality})",
                substep=True, step="select",
            )

            # Step 2.5: Niche-format fallback.
            # When the selector says 'none', BM25 returned no genuine match
            # (e.g. an arbitration petition under Section 9 of the A&C Act
            # is not in our corpus). Try the open web first; if Gemini +
            # Google Search returns a usable template AND the LLM validator
            # confirms it's a real format (not a refusal / prose blurb),
            # use it. Otherwise fall through to a doc-type-keyed generic
            # skeleton.
            #
            # 'marginal' picks go through a family-mismatch verifier first.
            # The selector LLM sometimes rationalizes topical overlap into
            # 'marginal' when the picked template is actually in a different
            # doc-type family from what the user asked for (e.g. arbitration
            # AGREEMENT picked for a Section 9 A&C Act court APPLICATION). The
            # verifier reads the picked template's body and escalates to
            # 'none' (triggers web fetch) when the family is wrong.
            web_template_urls = []  # reset for this branch; see top-level init
            if match_quality == "marginal":
                # Fetch the selected template's body first so the verifier
                # can read it. (The same body is reused downstream — no
                # duplicate ES call.)
                _verifier_source_response = es.search(index=index, body={
                    "size": 1,
                    "query": {"term": {"source.keyword": selected_source}},
                    "_source": ["page_content"],
                })
                _verifier_hits = _verifier_source_response["hits"]["hits"]
                _verifier_preview = (
                    _verifier_hits[0]["_source"].get("page_content", "")[:1800]
                    if _verifier_hits else ""
                )
                if _verifier_preview:
                    is_correct_family, family_reason = await _verify_template_family(
                        query=query,
                        doc_type=doc_type,
                        template_preview=_verifier_preview,
                        selector_reason=match_reason,
                    )
                    if not is_correct_family:
                        log.info("Marginal pick escalated to 'none' by family verifier",
                                 doc_type=doc_type,
                                 reason=family_reason)
                        match_quality = "none"
                        match_reason = (
                            f"family-mismatch escalation: {family_reason}"
                        )

            if match_quality == "none":
                log.info("Selector graded all corpus candidates as 'none'; "
                         "trying web fallback",
                         reason=match_reason)
                progress("drafting",
                         "No corpus match — searching the web for a real template...",
                         step="web_template_fetch")
                web_text, web_template_urls = await _fetch_template_from_web(
                    query, doc_type,
                )
                is_usable = False
                if web_text:
                    is_usable, val_reason = await _validate_web_template(
                        web_text, doc_type, query,
                    )
                    if is_usable:
                        template_text = web_text
                        selected_source = f"<web:{doc_type}>"
                        template_language = user_language
                        format_block = ""
                        used_web_template = True
                        log.info("Web template accepted by validator",
                                 chars=len(web_text),
                                 sources=len(web_template_urls))
                        progress(
                            "drafting",
                            f"Web template adopted ({len(web_template_urls)} sources)",
                            substep=True, step="web_template_fetch",
                        )
                    else:
                        log.warning("Web template rejected by validator",
                                    reason=val_reason)
                if not used_web_template:
                    template_text = _generic_skeleton_for(doc_type)
                    selected_source = f"<generic:{doc_type}>"
                    template_language = user_language
                    format_block = ""
                    used_generic_skeleton = True
                    log.info("Falling back to generic skeleton",
                             doc_type=doc_type)
                    progress(
                        "drafting",
                        "Using a generic skeleton for this niche format",
                        substep=True, step="web_template_fetch",
                    )

            # Step 3 (regular path): fetch the full template from the index.
            # Skipped when we already adopted a web template or generic
            # skeleton above.
            if not (used_web_template or used_generic_skeleton):
                source_query = {
                    "size": 1,
                    "query": {"term": {"source.keyword": selected_source}},
                    "_source": ["page_content", "source"],
                }
                source_response = es.search(index=index, body=source_query)
                source_hits = source_response["hits"]["hits"]

                # Fallback: try next-best candidate if selected template not found
                if not source_hits:
                    log.warning("Selected template not found, trying fallback",
                                template=selected_source)
                    for path in all_paths:
                        if path == selected_source:
                            continue
                        fb_resp = es.search(index=index, body={
                            "size": 1,
                            "query": {"term": {"source.keyword": path}},
                            "_source": ["page_content", "source"],
                        })
                        if fb_resp["hits"]["hits"]:
                            source_hits = fb_resp["hits"]["hits"]
                            selected_source = path
                            log.info("Using fallback template", template=path)
                            break

                if not source_hits:
                    # Last-ditch: rather than error out, generic-skeleton it.
                    log.warning("Template source not found in ES; using generic skeleton",
                                template=selected_source)
                    template_text = _generic_skeleton_for(doc_type)
                    selected_source = f"<generic:{doc_type}>"
                    template_language = user_language
                    format_block = ""
                    used_generic_skeleton = True
                else:
                    template_text = source_hits[0]["_source"]["page_content"]
                    log.debug("Template loaded",
                              template=selected_source,
                              template_len=len(template_text))

        # Step 3.7 / 3.75: Layout/format conventions + template-language
        # detection. Skipped entirely when using any synthetic / web /
        # generic skeleton — language is known (= user_language) and the
        # skeleton already encodes its own layout conventions.
        synthetic_path = (
            use_skeleton                              # office_letter family
            or used_web_template                      # web-fetched skeleton
            or used_generic_skeleton                  # terminal generic fallback
        )
        if synthetic_path:
            format_block = ""
            template_language = user_language
        else:
            # Extract format conventions and template language in parallel.
            # Cached per template_source so this only fires once per unique
            # template — afterwards it's an in-memory lookup. format_block
            # carries alignment, numbering glyphs, prayer/verification
            # clauses, signature block, etc. Empty string when extraction
            # fails (silent fallback, outline + sections still run on
            # template_text alone). template_language drives a warning
            # injected into outline + section prompts whenever the template's
            # body language differs from user_language.
            format_spec, template_language = await asyncio.gather(
                _extract_format_spec(selected_source, template_text),
                _detect_template_language(selected_source, template_text),
            )
            format_block = _format_layout_block(format_spec)
            if template_language and template_language != user_language:
                log.info(
                    "Template language differs from user language — warning will "
                    "be injected into outline + section prompts",
                    template_lang=template_language,
                    user_lang=user_language,
                    source=selected_source[-40:],
                )

        # Step 4: Generate document outline (max 12 sections)
        progress("drafting", "Generating document outline...", step="outline")
        outline = await _generate_outline(
            query, template_text, user_language,
            user_facts=user_facts,
            case_facts=case_facts,
            format_block=format_block,
            template_language=template_language,
            doc_type=doc_type,
            intent=intent_obj,
        )
        progress("drafting", f"Outline ready: {len(outline.sections)} sections", found=len(outline.sections), substep=True, step="outline")

        # Step 4.5: Generate doctrinal stance (one-shot Flash call that fixes
        # the legal lane every parallel section must follow). Silent fallback
        # to empty stance_block if it fails — drafting still runs.
        stance = await _generate_doctrinal_stance(
            query, outline.document_title,
            user_facts=user_facts,
            case_facts=case_facts,
            user_language=user_language,
            existing_outline_titles=[s.title for s in outline.sections],
        )

        # Step 4.55: Verify stance.key_cases against the judgment ES corpus.
        # The stance LLM has no retrieval grounding, so confident-sounding
        # fabricated citations can land here and propagate end-to-end. The
        # verifier drops cases with zero ES hits; section prompts then only
        # see verified anchors.
        if stance is not None and stance.key_cases:
            progress("drafting", "Verifying anchor case citations...",
                     step="verify_cases")
            stance = await _verify_stance_cases(stance)

        # Step 4.57: For non-court doc types (office letter, police complaint,
        # legal notice, agreement, will, standalone affidavit) — clear any
        # procedural_sections and key_cases the stance LLM proposed. These
        # are court-pleading attachments (Schedule of Properties, Verification,
        # IA under Order XXXIX, anchor case citations) that DO NOT belong in
        # a one-page office letter or a pre-litigation notice. The outline
        # branch already produced the right shape; if we let injection run,
        # we'd re-introduce the very court scaffolding the gate was meant to
        # avoid.
        if stance is not None and doc_type in DOC_TYPES_USE_SKELETON | {"agreement_deed"}:
            cleared_proc = len(stance.procedural_sections)
            cleared_cases = len(stance.key_cases)
            if cleared_proc or cleared_cases:
                stance = stance.model_copy(update={
                    "procedural_sections": [],
                    "key_cases": [],
                })
                log.info("Cleared stance procedural_sections and key_cases for non-court doc type",
                         doc_type=doc_type,
                         cleared_procedural=cleared_proc,
                         cleared_cases=cleared_cases)

        stance_block = _format_stance_for_section(stance)

        # Step 4.6: Inject any mandatory procedural sections the stance
        # enumerated that the outline missed (Schedule, Court Fee, List of
        # Documents, separate IA for TI, notarised Affidavit, etc.). When
        # the stance call failed or returned no procedural list, the outline
        # is left as-is — no hardcoded fallback pack.
        #
        # Two-stage dedup before injection:
        #   (a) LLM semantic dedup catches cross-lingual overlap that the
        #       head-word check downstream cannot (e.g. "गवाहों के बयान" vs
        #       "Witness Testimonies"). Bug report 2026-06-19: Hindi draft
        #       came back with sections 8-12 in English as duplicates of
        #       sections 1-7 in Hindi — pure cross-lingual overlap.
        #   (b) Head-word dedup inside _inject_procedural_sections is the
        #       second pass; it catches in-language overlaps the LLM may
        #       miss.
        if stance is not None and stance.procedural_sections:
            deduped = await _dedup_procedural_sections_via_llm(
                existing_outline_titles=[s.title for s in outline.sections],
                candidate_sections=stance.procedural_sections,
            )
            if len(deduped) != len(stance.procedural_sections):
                stance = stance.model_copy(update={"procedural_sections": deduped})
        outline.sections = _inject_procedural_sections(outline, stance)
        # Re-cap after injection
        if len(outline.sections) > _MAX_SECTIONS:
            log.warning("Outline expanded past cap after procedural injection",
                        before=_MAX_SECTIONS, after=len(outline.sections))
            outline.sections = outline.sections[:_MAX_SECTIONS]

        # Step 5: Generate sections in parallel (semaphore-limited to 3)
        try:
            from langgraph.config import get_stream_writer
            writer = get_stream_writer()
        except (RuntimeError, ImportError):
            writer = None

        # Section footer kind. Upstream classifier wins over the stance LLM's
        # guess for non-court types — the classifier reads the user's intent
        # directly (e.g. "RTI application" → office_letter), while the stance
        # LLM only sees the outline title (which the outline may have rendered
        # generically). This keeps the synthesized footer aligned with the
        # body shape produced by the doc-type-conditional outline branch.
        _classifier_footer_kind = DOC_TYPE_TO_FOOTER_KIND.get(doc_type, "court_filing")
        _stance_footer_kind = (
            getattr(stance, "footer_kind", "court_filing")
            if stance is not None else "court_filing"
        ) or "court_filing"
        if doc_type in DOC_TYPES_USE_SKELETON:
            _section_footer_kind = _classifier_footer_kind
        else:
            _section_footer_kind = _stance_footer_kind
        sections, failed_indices, total_tokens = await _generate_sections_parallel(
            query, template_text, outline, writer, user_language,
            user_facts=user_facts,
            case_facts=case_facts,
            stance_block=stance_block,
            format_block=format_block,
            footer_kind=_section_footer_kind,
            template_language=template_language,
            intent=intent_obj,
        )

        # Emit incomplete event if needed
        if failed_indices and writer:
            writer({
                "type": "draft_incomplete",
                "failed_sections": [
                    {"index": idx, "title": outline.sections[idx].title}
                    for idx in failed_indices
                ],
                "total_sections": len(outline.sections),
                "completed_sections": len(outline.sections) - len(failed_indices),
            })

        # Step 6: Assemble complete document
        progress("drafting", "Assembling final document...", step="assemble")
        full_draft = await _assemble_document(outline, sections, user_language, stance=stance, intent=intent_obj)

        # Step 6.5: Validator -- auto-fix mojibake + leftover [CITE: ...], log
        # warnings for statute traps and orphan citation tails. Never raises.
        full_draft, draft_warnings = validate_draft(full_draft, stance)

        # Renumber global paragraphs across substantive sections. This MUST
        # run AFTER validate_draft because the validator strips empty
        # numbered paragraphs (`N.` with no body), and after assembly so
        # we can see the canonical `## N. TITLE` headings the assembler
        # injected. See _renumber_global_paragraphs docstring for rationale.
        full_draft = _renumber_global_paragraphs(full_draft)

        if failed_indices:
            log.warning("Agent completed with incomplete sections",
                        template=selected_source,
                        sections_generated=len(sections) - len(failed_indices),
                        sections_failed=len(failed_indices),
                        failed_indices=failed_indices,
                        total_content_len=len(full_draft),
                        total_tokens=total_tokens)
        else:
            log.info("Agent completed -- multi-step pipeline",
                     template=selected_source,
                     sections_generated=len(sections),
                     total_content_len=len(full_draft),
                     total_tokens=total_tokens)

        # Step 7: Auto-inject statute references into the draft.
        # SKIPPED for non-English drafts: add_statute_references uses an
        # English-only prompt (with phrasing examples like "as per Section X
        # of the <Act>, YYYY" and an English statute catalogue) and grafts
        # those English tails onto Marathi/Hindi/Tamil prose. The per-section
        # generator already cites statutes inline in the target language, so
        # the post-hoc enrichment is both unnecessary and a leak source.
        if full_draft and not failed_indices and user_language == "en":
            try:
                from core.statute_refs import add_statute_references
                progress("drafting", "Adding statute references...", step="statute_refs")
                full_draft = await add_statute_references(full_draft)
            except Exception as ref_err:
                log.warning("Statute reference injection skipped",
                            error=str(ref_err))

        # Step 8: Dynamic self-refine — critic audits the assembled draft
        # against the typed UserIntent (strict_language, response_depth,
        # additional_instructions, etc.) and the refiner rewrites on
        # violations. Skips trivial intents internally; cheap when there's
        # nothing to fix. This is what catches Hindi/Marathi WS drafts
        # leaking Latin numerals or English clauses without bespoke
        # numeral substitution code.
        if full_draft and not failed_indices and intent_obj is not None:
            try:
                progress("drafting", "Auditing draft against your directives...", step="self_refine")
                # Pass source_languages so the critic force-runs (and the
                # English-strict rule fires) when the source PDF is in a
                # non-target script. Prod test on 2026-06-24 showed Marathi
                # tokens like "दस्त क्र. 637/2023" surviving in otherwise-
                # English replies; without this, the critic skipped the
                # response because intent.language='en' alone wasn't a
                # directive worth auditing.
                _refine_source_langs = detect_source_languages(user_facts, case_facts)
                refined_draft, refine_history = await self_refine(
                    full_draft,
                    user_query=query,
                    intent=intent_obj,
                    source_languages=_refine_source_langs,
                )
                if refined_draft != full_draft:
                    log.info(
                        "Self-refine altered draft",
                        iterations=len(refine_history),
                        original_len=len(full_draft),
                        refined_len=len(refined_draft),
                    )
                    full_draft = refined_draft
            except Exception as refine_err:
                log.warning("Self-refine skipped due to error",
                            error=str(refine_err))

        template_display = os.path.splitext(os.path.basename(selected_source))[0]
        # Build source attribution. For web-fallback drafts, add a separate
        # SourceMetadata entry per grounding URL so the user can see where
        # the template came from.
        sources = [SourceMetadata(
            source_type="drafting",
            title=template_display,
            content=[template_text[:300]],
            file_name=selected_source,
            agent_name="Drafting",
            template_type=template_display,
        )]
        if used_web_template and web_template_urls:
            for url in web_template_urls[:8]:
                sources.append(SourceMetadata(
                    source_type="drafting",
                    title="Web template source",
                    web_url=url,
                    web_title=url,
                    agent_name="Drafting",
                ))

        result = AgentResult(
            agent_name="Drafting",
            content=full_draft,
            sources=sources,
            tokens_consumed=total_tokens,
            fallback_used=(used_web_template or used_generic_skeleton),
            meta=(
                {"draft_warnings": draft_warnings}
                if draft_warnings else {}
            ),
        )

        # Store continuation metadata for incomplete drafts
        state_update = {"agent_results": {"Drafting": result}}
        if failed_indices:
            state_update["draft_continuation"] = {
                "outline": outline.model_dump(),
                "template_text": template_text,
                "template_source": selected_source,
                "query": query,
                "user_facts": user_facts,
                "case_facts": case_facts,  # preserve extracted entities for retries
                "footer_kind": _section_footer_kind,
                "template_language": template_language,
                "completed_sections": {
                    str(i): sections[i] for i in range(len(sections))
                    if i not in failed_indices
                },
                "failed_indices": failed_indices,
            }

    except Exception as e:
        log.error("Agent failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Drafting",
            content="",
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )
        state_update = {"agent_results": {"Drafting": result}}
    finally:
        _AGENT_SEMAPHORE.release()

    return state_update
