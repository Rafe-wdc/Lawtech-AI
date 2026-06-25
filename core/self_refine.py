"""Self-refine layer — LLM-driven critique + refinement loop.

Replaces the scattered mechanical retry guards (_QUALITY_THRESHOLDS,
_RETRY_PREAMBLE, _passes_quality_gate, hardcoded numeral substitutions,
per-artifact word-count floors) with a single dynamic loop:

    generate → critique → refine → critique → refine → ... (max N)

The critic is a small Gemini Flash call that takes:
    - the original user query (free text)
    - the typed UserIntent (the grounding — derived per-request by the
      orchestrator's intent extractor)
    - the response so far

…and returns a structured Critique listing specific violations of the
intent. The refiner is a Gemini Pro call that rewrites the response to
fix the violations the critic surfaced.

Why this design vs. the old hardcoded gates:
    - The critic derives WHAT to check FROM the intent fields. Add a new
      intent field tomorrow and the critic checks it without code edits.
    - The refiner derives WHAT to fix from the violations the critic
      surfaces. No hardcoded "stronger preamble" per artifact.
    - When the user invents a new directive (e.g. "use Devanagari
      numerals only"), the critic catches Latin digits and the refiner
      replaces them — without me adding a regex.

Cost: 1 critique call (Flash, ~$0.0001) per iteration. Refine call only
when violations found. Max 2 iterations.

Skip-when-trivial: we don't call the critic when intent is missing /
low-confidence / has no explicit directives. Saves the cost for
queries the loop can't help.

Important grounding (from 2026 research on self-correction):
    Self-critique WITHOUT grounding can DEGRADE quality. The critic
    here ALWAYS reasons against the structured UserIntent — never
    against free-form "is this good?" prompts. The intent IS the
    constitution.

See docs/intent_layer_implementation_plan.md (intent layer) for the
typed UserIntent the critic uses as ground truth.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Literal, Optional

from pydantic import BaseModel, Field, ConfigDict
from langchain_core.prompts import ChatPromptTemplate

from config.intent import LegalArtifact, UserIntent, default_intent
from config.prompts import INJECTION_GUARD_PREAMBLE, wrap_untrusted
from core.clients import get_gemini_flash_full, get_gemini_pro
from core.logger import get_logger, log_time
from core.token_tracker import record as _record_tokens

log = get_logger("SelfRefine")


# ---------------------------------------------------------------------------
# Structured critique schema — the critic returns this. Downstream code
# branches on `passes`, logs `violations` for telemetry, and feeds the
# `violations` + `overall_quality_notes` into the refiner.
# ---------------------------------------------------------------------------

class Violation(BaseModel):
    """A single discrepancy between the intent and the response."""
    field: str = Field(
        ...,
        description="Which UserIntent field is violated. Examples: "
                    "'language', 'strict_language', 'response_format', "
                    "'response_depth', 'legal_artifact', 'include_case_law', "
                    "'additional_instructions'. Use 'overall' for issues "
                    "that span multiple fields.",
    )
    issue: str = Field(
        ...,
        description="One-sentence concrete description of what's wrong. "
                    "Be specific: cite the offending substring or pattern. "
                    "Example: \"Paragraph numbers use Latin digits ('1.', "
                    "'2.', '3.') but strict_language=True with language=mr "
                    "requires Devanagari ('१.', '२.', '३.').\"",
    )
    severity: Literal["critical", "major", "minor"] = Field(
        ...,
        description="critical = response is unusable as-is (wrong language, "
                    "wrong artifact, fabricated facts); "
                    "major = user-visible quality drop (missing required "
                    "section, depth not honoured); "
                    "minor = cosmetic / would-be-nice.",
    )
    suggested_fix: str = Field(
        ...,
        description="Actionable rewrite guidance the refiner can act on. "
                    "Example: 'Replace every Latin digit in body paragraph "
                    "numbers and dates with the corresponding Devanagari "
                    "numeral; keep section/article numbers in citation "
                    "context as printed.'",
    )


class Critique(BaseModel):
    """Critic output — drives the refine/stop decision."""
    model_config = ConfigDict(use_enum_values=False)

    passes: bool = Field(
        ...,
        description="True iff the response meets ALL of the user's intent "
                    "directives with no critical or major violations. Minor "
                    "violations may exist when passes=True if the response is "
                    "otherwise correct and complete.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="The critic's own confidence in this judgement. Low "
                    "confidence (< 0.6) means the critic itself wasn't "
                    "sure — downstream code may choose to stop early "
                    "rather than refine on a shaky verdict.",
    )
    violations: list[Violation] = Field(
        default_factory=list,
        description="Specific issues found. Empty when passes=True with "
                    "no minor issues either.",
    )
    overall_quality_notes: str = Field(
        "",
        description="One-paragraph free-text observation about the response "
                    "as a whole — useful for the refiner and for telemetry. "
                    "Kept under 300 chars.",
        max_length=400,  # 100-char slack vs the 300 target
    )


# ---------------------------------------------------------------------------
# Critic prompt — receives query + intent JSON + response, derives the rules
# from the intent, returns a Critique.
# ---------------------------------------------------------------------------

CRITIQUE_PROMPT = INJECTION_GUARD_PREAMBLE + """You are a quality auditor for an Indian Legal AI.

A response was generated for a user query. Your job is to judge whether the
response honours the user's typed INTENT — a structured object the system
extracted from the query before generation.

You DO NOT freelance "is this good?". You judge ONLY against the intent
fields below. Your role is grounded: the intent is the constitution.

## How to reason

For EACH non-default field in the intent, check whether the response complies.
Examples of how intent fields translate to checks:

  language!='en' (any non-English target — Marathi, Hindi, Tamil, Bengali, …)
    → Response prose must be in the target language's native script.
      The ONLY content that may stay in English is a verbatim case-law
      citation block — the printed party names + reporter cite of a real
      decided case (e.g. "Mohan Lal v. State of Punjab, (2018) 17 SCC 627").
      Surrounding clause stays in the target language.
      The following are MAJOR violations regardless of strict_language:
        (a) English citation tails appended to target-language sentences,
            e.g. "...काही मराठी वाक्य, as per Section 480 of the Bharatiya
            Nagarik Suraksha Sanhita, 2023." — the act title + section
            label MUST be translated inline ("...भारतीय नागरिक सुरक्षा
            संहिता, २०२३ चे कलम ४८० नुसार.").
        (b) English act / code / statute titles inside running prose
            ("the Code of Criminal Procedure, 1973", "the Indian Evidence
            Act, 1872", "the NDPS Act, 1985") — translate to the target
            language's standard rendering of the act name.
        (c) English connector clauses like "as per", "in accordance with",
            "under the provisions of", "as per the provisions of" tacked
            onto target-language sentences — every such tail is MAJOR.
      strict_language=True additionally requires native-script numerals
      and translation of placeholder brackets / signature labels (see
      the strict_language section below).

  Devanagari-script language drift (Hindi vs Marathi vs Sanskrit)
    → Hindi, Marathi, and Sanskrit all use Devanagari, so detecting
      drift between them by SCRIPT alone is not enough — disambiguate
      by GRAMMAR and VOCABULARY. When language='hi' (Hindi), flag any
      Marathi-specific forms as a MAJOR violation; when language='mr'
      (Marathi), flag any Hindi-specific forms as a MAJOR violation.
      Concrete markers to audit:

        Marathi-only forms (WRONG when language='hi'):
          - Possessive suffixes: चा / ची / चे / च्या (Marathi);
            Hindi uses का / की / के.  Examples: "वादीचा अर्ज",
            "ग्रामसभेचे नाव", "राजूचा पत्ता", "१९६३ च्या कलम".
          - Verb आहे / आहेत (Marathi 'is/are'); Hindi uses है / हैं.
          - वय (Marathi 'age'); Hindi uses आयु or उम्र.
          - रा. as short for resident (Marathi रहिवासी); Hindi uses
            निवासी.
          - जिल्हा (Marathi 'district'); Hindi spelling is जिला.
          - तालुका (Marathi 'tehsil'); Hindi uses तहसील.
          - क्रमांक (Marathi 'number') used in standalone Hindi prose
            without explanation; Hindi normally uses संख्या.
          - सन (Marathi 'year'); Hindi uses वर्ष or साल.
          - या प्रकरणी (Marathi 'in this matter'); Hindi uses
            इस मामले में / इस प्रकरण में.
          - अर्जदार (Marathi 'applicant'); Hindi uses आवेदक /
            याचिकाकर्ता.
          - तसेच / व (Marathi 'and / also'); Hindi uses तथा / और.

        Hindi-only forms (WRONG when language='mr'):
          - Possessive का / की / के / की; Marathi uses चा / ची /
            चे / च्या.
          - Verb है / हैं; Marathi uses आहे / आहेत.
          - आयु / उम्र; Marathi uses वय.
          - निवासी; Marathi uses रा.
          - जिला; Marathi spelling is जिल्हा.
          - तहसील; Marathi uses तालुका.
          - संख्या used as case-number label; Marathi uses क्रमांक.

      One or two leak words can be ignored as cosmetic if the bulk of
      the document is correct, BUT systematic leakage (5+ instances of
      the wrong-language possessive suffix, or a full Marathi vocabulary
      stack — चा, आहे, वय, रा., जिल्हा, तालुका — in a Hindi draft) is a
      CRITICAL violation. The refiner must translate the affected
      spans into the target language's grammar, not just swap individual
      words.

  language='en' (English target)
    → The response must be written entirely in English using the Latin
      script. Flag any non-Latin script (Devanagari, Tamil, Bengali, Telugu,
      Kannada, Malayalam, Gujarati, Gurmukhi, Odia, Arabic) appearing in the
      response body as a MAJOR violation.

      The narrow exception is TIGHTLY SCOPED to case-law CITATIONS where the
      reported case name is itself in a non-Latin script (e.g. a transliterated
      party-name printed by the Reporter). It does NOT exempt:
        - Document identifiers ("दस्त क्र. 637/2023", "वकालतनामा क्र. ...")
          — these are common-noun document labels, not case names. Translate
          to "Document No. 637/2023" or similar.
        - Statute / act titles ("भारतीय करार अधिनियम") — translate.
        - Place names, district names, taluka names ("मौजे शिवरी", "जिल्हा
          पुणे") — transliterate to Latin ("Mauje Shivri", "District Pune").
        - Party names from a notice / contract / FIR — transliterate to Latin
          ("श्री. कैलास" → "Mr. Kailas"). Party names are NOT case-law
          citations.
        - Section / article numbers in Indic numerals (६३७, २०२३) —
          MUST be Latin (637, 2023).
      In short: the ONLY surviving non-Latin run permitted is inside a
      verbatim case-law citation block such as "ABC v. XYZ (2020) 5 SCC 1",
      where the case-name part as printed by the Reporter happens to be in
      a non-Latin script. Anything else is MAJOR.

      This check fires regardless of strict_language; English-target
      responses must not code-switch into the source document's language.
      When the source content (uploaded files, retrieved passages) is in
      another language, the refiner must TRANSLATE quoted/cited content
      into English (or transliterate proper nouns) instead of leaving them
      in the source script.

  strict_language=True with language!='en'
    → STRICT MODE. Audit ALL of the following — each is a MAJOR violation:
       (a) Any English sentence, clause, or phrase OTHER than verbatim
           case names (e.g. "Kesavananda Bharati v. State of Kerala").
           Statute titles, act names, section labels, and placeholder
           brackets ("[Place]", "[Date]") must be in the target script.
       (b) Any digit-prefixed numbered-list start using Latin digits.
           Examples of MAJOR violations in Marathi (mr) / Hindi (hi) /
           Sanskrit (sa) strict mode:
              "1. दाव्यातील..."   ← MAJOR. Must be "१. दाव्यातील..."
              "2. परिच्छेद..."    ← MAJOR. Must be "२. परिच्छेद..."
              "(3) सदर..."        ← MAJOR. Must be "(३) सदर..."
           Sub-section numbering ALSO must be in target script:
              "2.1. वादीने..."    ← MAJOR. Must be "२.१. वादीने..."
              "(a) कारण..."       ← MAJOR. Must be "(अ) कारण..." (or appropriate native alphabetic ordinal)
              "(i) पुरावा..."     ← MAJOR. Must be appropriate native ordinal
           The same applies to other Indic scripts (Bengali ০-৯, Tamil
           ௦-௯, Telugu ౦-౯, Kannada ೦-೯, Malayalam ൦-൯, Gujarati ૦-૯,
           Gurmukhi ੦-੯, Odia ୦-୯, Eastern Arabic for Urdu ۰-۹).
       (c) Latin digits in dates, amounts, years, paragraph numbers
           ("Section 138", "para 2", "Rs. 50000") — every one is a MAJOR
           violation in strict mode and must be in the target script.
           Inline statute references must be FULLY translated. Common
           leak patterns in Marathi/Hindi drafts the critic MUST catch:
              "..., as per the provisions of the Indian Contract Act, 1872."
                ← MAJOR. Must be: ", भारतीय करार अधिनियम, १८७२ च्या तरतुदींनुसार."
              "..., as per Section 10 of the Specific Relief Act, 1963."
                ← MAJOR. Must be: ", विशिष्ट अनुतोष अधिनियम, १९६३ च्या कलम १० नुसार."
              "..., as per the Hindu Succession Act, 1956"
                ← MAJOR. Must be the native-script equivalent.
              "Limitation Act, 1963 च्या कलम 54 नुसार"
                ← MAJOR. Mixed Latin+Devanagari. Must be either fully
                English OR fully Marathi (preferred in strict mode):
                "मुदत अधिनियम, १९६३ च्या कलम ५४ नुसार".
           Any "as per <English act name + year + section>" pattern is a
           MAJOR violation. The refiner must translate the full reference.
       (d) Even ONE Latin-digit numbered-list start is enough to set
           passes=False. Do NOT pass the response if any survive.
      Only narrow exception: case names ("ABC v. XYZ"), which are proper
      nouns and stay in English. Surrounding clause stays in target lang.

  response_format=TABLE / COMPARISON
    → Response must contain a real `|`-delimited markdown table with a
      separator row and ≥ 2 data rows. Pure prose with the word "table"
      in it does NOT count.

  response_depth='brief'
    → Response should be concise (typically < 200 words / focused).
      Padding to appear thorough is a violation.

  response_depth='detailed'
    → Response should comprehensively cover the topic; thin responses
      are a violation.

  target_word_count is not null (user named a specific number, e.g. 100)
    → Count words in the response BODY (excluding system-added Disclaimer
      and any "## Sources" appendix). Tolerance is ±25%. Examples:
        target=100: acceptable range 75-125 words. 145 words = MAJOR.
        target=500: acceptable range 375-625 words.
        target=50:  acceptable range 38-63 words. Over 100 words = CRITICAL.
      The suggested_fix should be specific: "Trim to ~100 words by removing
      the elaboration on <subsection>." or "Expand to ~500 words by adding
      the <missing aspect> the user implicitly asked for."

  legal_artifact='cross_examination'
    → Response must have a Legal Analysis section, Strategic Objectives
      bullets, and ≥ 20 numbered cross-examination questions using
      Indian courtroom language ("I put it to you that...", "Is it not
      a fact that...").

  legal_artifact='deposition_summary'
    → Six labelled sections: Case ID, Witness ID, Substantive Testimony,
      Key Claims, Contradictions/Omissions, Exhibits. NOT cross-exam
      questions (different artifact).

  legal_artifact='contract_analysis'
    → Sections covering: Identification, Parties, Key Commercial Terms,
      Critical Clauses (issue-by-issue), Risk Flags (HIGH/MEDIUM/LOW),
      Compliance Hooks, Recommended Amendments.

  legal_artifact='legal_notice'
    → Indian legal notice format: addressee block, subject, numbered
      facts, "TAKE NOTICE THAT" demand block citing the correct statute,
      compliance period, signature. NO case-law / judgment citations
      anywhere in the body — notices assert a position with statutory
      references, not precedent.

  legal_artifact='office_application'
    → Letter to a non-court authority (PIO under RTI Act, Tahsildar,
      SDM, Collector, Registrar, Municipal Corporation, employer, bank,
      housing society, university, regulator). Addressee block ("To,
      The <Designation>, <Office>"), Subject line, numbered paragraphs
      of facts + the specific rule / section / entitlement invoked,
      request paragraph, applicant signature. NO court-filing
      scaffolding (no "IN THE COURT OF", no Plaintiff/Defendant, no
      Versus, no Prayer-clause numbering, no Verification, no
      Affidavit). NO case-law / judgment citations.

  legal_artifact='complaint_draft', 'witness_prep', 'opening_statement',
  'closing_argument'
    → Each has its own structural floor — refer to the
      `additional_instructions` field if the system attaches any
      artifact-specific guidance.

  include_case_law=True
    → Response should reference relevant cases. (Citations need not be
      in English when strict_language=True.)

  additional_instructions=<text>
    → The text is verbatim user guidance. Apply common-sense
      interpretation.

For each field where the response does NOT comply, output a Violation
describing it. If multiple intent fields are violated, list them all.

## Severity

  critical — response is broken as-is. Wrong language entirely;
             requested artifact not produced at all; fabricated facts
             that contradict the source document.
  major    — user-visible quality drop. Missing required section;
             depth not honoured; numerals in wrong script for strict
             mode; English content where strict-language demanded
             native.
  minor    — cosmetic; would-be-nice; small inconsistency that doesn't
             impair the response.

## Decision rule for `passes`

  passes = True  iff  there are no critical AND no major violations.
                 Minor violations may exist alongside passes=True.

## Drafting-specific categories (only when task_intent='draft' OR the
##                                  response is clearly a legal draft)

When the response is a court-filing-ready legal document (plaint,
petition, written statement, bail application, affidavit, legal notice,
agreement, contract, deed, MOU, will), ALSO audit for these MAJOR
violations specific to Indian drafting practice:

  forbidden_statute_pair — when the draft cites a statute that does not
    apply to the lane chosen. The most common traps:
       Section 38 Specific Relief Act, 1963 paired with "temporary
       injunction" / "ad-interim relief" / "interim relief" —
       Section 38 SRA grants permanent injunction only. Temporary
       injunction is under Order XXXIX Rules 1 & 2 CPC. MAJOR.
       Section 54 CPC paired with "partition of flat / apartment /
       residential" — Section 54 applies only to estates assessed
       to land revenue. Order XX Rule 18 CPC governs residential
       partition. MAJOR.
       Any other statute the user's typed intent / chat history
       indicates is the wrong authority for the relief sought.

  placeholder_marker — surviving `[CITE: ...]` brackets, "(citation
    needed)", "{section number}", "<insert party>", "TBD", "FILL IN".
    The drafting prompt forbids these; if they survive, MAJOR.

  orphan_citation_tail — sentences ending with "as held in.", "the
    Supreme Court in.", "the Hon'ble Court in." — the LLM started a
    case citation, never finished. MAJOR.

  trailing_preposition — paragraph ends with a bare "in.", "of.",
    "by.", "to.", "under." — a citation sentence was truncated. MAJOR.

  missing_procedural_section — when the draft is a civil suit / plaint
    / writ / appeal and one or more of these mandatory blocks is
    absent: Schedule of Properties, Valuation and Court Fee, List of
    Documents (Order VII Rule 14 / Order XI Rule 14 CPC), Verification
    (Order VI Rule 15 CPC), Affidavit in Support (Order XIX Rule 3 CPC),
    or a separate Interim Application under Order XXXIX Rules 1 & 2
    CPC when a temporary injunction is prayed for. MAJOR.

  raw_html — `<p>`, `<div>`, `<span>`, `<center>`, `align="center"`,
    `align="right"` attributes inside the body. The frontend renders
    markdown only; raw HTML shows up as literal text. MAJOR.

  cause_title_collapsed — the opening party block (court name + case
    number + plaintiff details + "vs" + defendant details) is one
    squashed paragraph instead of a structured layout. Markdown
    collapses single newlines, so each detail must be on its OWN
    paragraph (separated by blank lines). Pattern that's a MAJOR
    violation:
       "IN THE COURT OF CIVIL JUDGE ... C.S. No. ___/2026 Mrs. Anjali
        Deshmukh Age: 42 years Occupation: ... Address: ... .....Plaintiff"
    Should be (each line a paragraph, blank lines between):
       IN THE COURT OF CIVIL JUDGE SENIOR DIVISION, AT PUNE
       (blank line)
       C.S. No. ___/2026
       (blank line)
       Mrs. Anjali Deshmukh
       (blank line)
       Age: 42 years
       (blank line)
       Occupation: ...
       (blank line)
       Address: ...
       (blank line)
                                                       .....Plaintiff
    Flag MAJOR when the plaintiff or defendant block reads as a
    flowing sentence with name + age + occupation + address
    concatenated. Suggested_fix: rewrite the party block with
    blank lines between every detail.

  missing_cause_title_elements — the cause title block is
    formatted with blank lines (so `cause_title_collapsed` doesn't
    catch it) but is MISSING one or more of these mandatory
    elements (Sagar's bug #6, 2026-06-16):
       (a) "IN THE MATTER OF:" header (bolded) between the case
           number line and the plaintiff block.
       (b) Subject heading at the END of the cause title block
           (after the defendant designation), e.g. "SUIT FOR
           COMPENSATION FOR MEDICAL NEGLIGENCE", "WRIT PETITION
           UNDER ARTICLE 226 OF THE CONSTITUTION OF INDIA",
           "COMPLAINT UNDER SECTION 138 NI ACT". Bolded, its own
           paragraph.
       (c) Court name and Suit/Petition number NOT bolded.
           Both must be `**bolded markdown**` on their own
           paragraphs.
    Flag MAJOR for each missing element. Suggested_fix: "Insert
    `**IN THE MATTER OF:**` between the suit-number line and the
    plaintiff block" / "Append `**SUIT FOR <SUBJECT>**` after the
    defendant designation line" / "Bold the court name and suit
    number with `**` markers".

  vs_in_code_block — the "vs" separator between plaintiff and
    defendant blocks is wrapped in backticks (`` `vs` ``) or a
    fenced triple-backtick code block. The renderer treats backticks
    as inline code and shows a dark highlighted bar instead of
    plain text. Expected output is `**vs**` (bolded markdown,
    plain text) on its own paragraph with blank lines on either
    side. Any code-block / inline-code formatting of "vs" / "VERSUS"
    / "versus" is MAJOR. Suggested_fix: replace the code-fenced
    "vs" with `**vs**` between blank lines.

  fabricated_citation — a case name + citation that does not exist OR
    that is NOT in the doctrinal stance's `key_cases` whitelist. The
    drafting agent generates a stance JSON with vetted cases before
    section generation; sections should cite ONLY those, or omit the
    citation entirely. Any case appearing in the draft that is NOT in
    `stance.key_cases` is suspect — flag MAJOR and ask the refiner
    to either replace with a stance case or remove the citation lead-in.

  date_placeholder_inconsistency — the same draft mixes specific dates
    ("2026-06-13", "2023-01-15") AND placeholder forms ("[Date]",
    "[Date of Cheque]"). A real draft uses ONE convention throughout:
    placeholders when facts are unknown, specific dates when facts are
    given. Mixed = MAJOR. The refiner picks one convention and rewrites.

  paragraph_numbering_break — section heading numbers don't match
    paragraph numbers, OR substantive body sections (Brief Facts,
    Cause of Action, Issues, Grounds, Pleadings) restart numbering
    inside each section instead of continuing the global counter,
    OR section numbers skip ("## 1." → "## TRANSACTION DETAILS" →
    "## 3."). A real legal draft has continuous paragraph numbering
    THROUGH THE BODY. MAJOR.

  wrong_numbering_scheme_for_procedural_section — PROCEDURAL blocks
    must use their OWN local numbering scheme, NOT continue the
    global body counter. Specifically:
       PRAYER — uses (a)/(b)/(c) or (i)/(ii)/(iii) or (1)/(2)/(3)
       FROM A FRESH START. Continuing the body counter ("34. Direct
       the Defendants...", "35. Direct the Defendants...") is a
       MAJOR violation — Prayer should be "(a) Direct ..." or
       "(1) Direct ...".
       VERIFICATION — single declaratory paragraph, NO point number.
       The "I, [name], aged [age], do hereby verify..." statement
       is unnumbered. Numbering it as "37. I, [name], aged..." or
       similar continuation from body paragraphs is a MAJOR violation.
       COURT FEE STATEMENT — descriptive section, uses fresh local
       numbering (1, 2, 3 from start) or unnumbered prose. Continuing
       body counter ("39. The Plaintiff submits...", "40. In
       accordance with...") is a MAJOR violation.
       SCHEDULE OF PROPERTIES — uses Schedule A / Schedule B per
       asset, NOT body para numbers.
       LIST OF DOCUMENTS — uses fresh 1, 2, 3.
       AFFIDAVIT-IN-SUPPORT — separate document with its own
       deponent declaration; not numbered into the body counter.
    Suggested_fix: rewrite the affected section's paragraph numbers
    to use the correct local scheme (a/b/c or i/ii/iii or fresh
    1/2/3 or unnumbered for Verification).

  wrong_footer_for_artifact — a court-filing footer ("Place: / Date: /
    Signature of the Petitioner/Applicant / Through Counsel:") appears
    on a non-court-filing draft (legal notice, agreement, MOU, will,
    deed). Legal notices are signed by counsel directly; agreements
    have parties' signatures. The court-filing footer is wrong. MAJOR.

  missing_jurisdiction_clause — a court-filing draft (plaint, petition,
    suit, writ, complaint) lacks an explicit jurisdiction clause stating
    EITHER territorial jurisdiction (place of cause of action / where
    property is situated / where defendant resides — Sec 16-20 CPC) OR
    pecuniary jurisdiction (value of suit). Also flag MAJOR if the forum
    named in the cause title (e.g. "Civil Judge Senior Division Pune")
    does NOT match the jurisdiction clause (e.g. clause says "value Rs.
    25,00,000" but the cause title is a Junior Division court whose
    pecuniary limit excludes that valuation). MAJOR.

  wrong_court_fees_act — the draft cites the central "Court Fees Act,
    1870" for a State court in Maharashtra. Correct authority is
    "Maharashtra Court Fees Act, 1959" (formerly Bombay Court Fees Act,
    1959). Same principle for other States: prefer the State Court Fees
    Act over the central Act unless the user explicitly worked from the
    central Act. MAJOR.

  missing_limitation_clause — court-filing draft does not state the
    applicable Article of the Limitation Act, 1963 and assert that the
    claim is within time. Limitation is a live vulnerability in any
    plaint / suit / appeal; omitting the clause is a filing-level
    defect. MAJOR. Suggested_fix: "Add a paragraph: 'The suit is filed
    within the limitation period under Article ___ of the Limitation
    Act, 1963 as the cause of action arose on ___.'"

  prayer_relief_mismatch — the Prayer clause asks for relief NOT
    pleaded in the body, OR the body pleads relief that does NOT
    appear in the Prayer. Common patterns:
       Body alleges permanent injunction throughout, Prayer asks only
       for damages → MAJOR; add the injunction prayer.
       Body alleges three distinct reliefs, Prayer lists only two →
       MAJOR; add the missing relief.
       Prayer asks for "any other order this Hon'ble Court may deem
       fit" only, with no specific relief → MAJOR; specific reliefs
       must be enumerated before the residuary catchall.
    MAJOR.

For each, the suggested_fix should be concrete:
  - "Replace 'Section 38 SRA' with 'Order XXXIX Rules 1 & 2 CPC' in
     para 4.2 and re-state the three-fold injunction test."
  - "Append the Schedule of Properties block describing the suit
     property with CTS number, area, boundaries before the prayer."
  - "Complete the truncated citation 'as held by the Supreme Court in.'
     in para 3.1 — either with a real case name + citation or remove
     the citation lead-in entirely."

## What NOT to do

  - Do NOT propose new rules the intent doesn't specify.
  - Do NOT flag stylistic choices the LLM made if the intent didn't
    constrain them. (E.g., do not flag "could be more elegant" — the
    intent didn't ask for elegance.)
  - Do NOT flag the system-added Disclaimer block at the very end —
    that's appended by guardrail, not the model under audit.
  - Do NOT flag English text inside literal case names like
    "Kesavananda Bharati v. State of Kerala" even in strict-language
    mode. Case names are proper nouns.
  - Do NOT flag missing procedural sections on responses that are
    Q&A, explanations, or analyses (not legal drafts). The
    procedural-completeness rule applies ONLY to actual draft output.

## Inputs

User query:
{query}

User intent (JSON — this is your ground truth):
```json
{intent_json}
```

Response to audit:
{response}
"""


REFINE_PROMPT = INJECTION_GUARD_PREAMBLE + """You are a senior Indian legal practitioner refining a draft response.

A previous attempt was generated and an auditor found specific violations of
the user's intent. Your task is to produce a REVISED response that fixes
EACH violation — and changes nothing else.

## Rules

1. Apply EVERY suggested_fix in the violations list. Do not leave any
   critical or major violation unaddressed.
2. Do NOT rewrite the whole response from scratch. Keep the existing
   structure, headings, facts, and prose voice unless a violation
   explicitly demands a change.
3. Do NOT pad the response. The auditor did not ask for more content —
   it asked for the right content. Length should stay roughly similar
   unless a violation specifically says "too short" / "too long".
4. Do NOT introduce new facts, citations, or content not present in the
   prior response or the user's source material.
5. Output ONLY the refined response (no preamble like "Here is the
   revised version", no postscript like "Let me know if you need
   changes"). The response should drop in cleanly where the original
   was.
6. When a violation requires native-script numerals (strict_language
   mode), rewrite EVERY Latin digit in the response — paragraph
   numbers, date components, year, monetary amounts, section numbers
   inside running prose, list-item prefixes ("1.", "(2)", "3)" →
   "१.", "(२)", "३)" for Devanagari; analogous for other Indic
   scripts). Do a clean pass. Do not leave any "1.", "2.", "3.",
   "(4)", etc. anywhere in the body. The ONLY Latin digits that may
   remain are inside English-language verbatim case citations
   (e.g. "Kesavananda Bharati v. State of Kerala, AIR 1973 SC 1461").

## Inputs

Original user query (untrusted):
{query}

User intent (this is the ground truth — do NOT deviate):
```json
{intent_json}
```

Violations to fix (each has a suggested_fix the auditor wrote):
{violations_block}

Auditor's overall quality note (free text, contextual):
{quality_notes}

Previous response (the draft to revise):
{response}

Produce the revised response now.
"""


# ---------------------------------------------------------------------------
# Critic + refiner helpers
# ---------------------------------------------------------------------------

# Telemetry buckets — only count meaningful runs (not the "skipped" trivial
# cases) so the metrics actually mean something.
_TELEMETRY_KEYS = (
    "self_refine_invocations",
    "self_refine_critic_calls",
    "self_refine_refiner_calls",
    "self_refine_passes_first_try",
    "self_refine_passes_after_refine",
    "self_refine_max_iters_exhausted",
)


def _intent_has_directives(intent: Optional[UserIntent]) -> bool:
    """True iff the intent expresses at least one explicit directive worth
    auditing. Trivial intents skip the loop entirely.
    """
    if intent is None:
        return False
    if intent.confidence < 0.5:
        return False
    return (
        intent.format_explicit
        or intent.language_explicit
        or intent.strict_language
        or intent.response_depth != "standard"
        or intent.include_case_law
        or intent.include_examples
        or intent.arguments_for_party != "none"
        or intent.legal_artifact != LegalArtifact.NONE
        or bool(intent.additional_instructions.strip())
    )


def _source_language_mismatch(
    intent: Optional[UserIntent], source_languages: tuple[str, ...]
) -> bool:
    """True when the target response language differs from any detected
    source-content language — a strong signal that the response is at risk
    of code-switching. Used to force the critic loop even when the intent
    has no explicit directives (e.g. user typed English, uploaded a Marathi
    PDF; intent defaults to language="en"/language_explicit=False, but the
    Marathi source is real and worth auditing).
    """
    target = (getattr(intent, "language", "en") if intent else "en") or "en"
    for s in source_languages:
        if s and s in {
            "en", "hi", "bn", "te", "mr", "ta", "kn", "ml",
            "gu", "pa", "ur", "or", "as", "sa",
        } and s != target:
            return True
    return False


def _format_violations(violations: list[Violation]) -> str:
    """Render the violation list for the refiner prompt."""
    if not violations:
        return "(no violations — this branch should not be reached)"
    lines: list[str] = []
    for i, v in enumerate(violations, 1):
        lines.append(
            f"{i}. [{v.severity.upper()}] field={v.field}\n"
            f"   issue: {v.issue}\n"
            f"   suggested fix: {v.suggested_fix}"
        )
    return "\n\n".join(lines)


async def _critique(
    user_query: str,
    intent: UserIntent,
    response: str,
    critic_llm=None,
) -> Critique:
    """Run a single critique LLM call. Returns a Critique (passes + violations).

    On any error: returns a "passes=True, confidence=0" critique so the loop
    treats it as "nothing to do" and stops. We do not want a critic blip to
    break the user-visible request.
    """
    try:
        with log_time(log, "Self-refine critique"):
            llm = (critic_llm or get_gemini_flash_full(
                temperature=0.0, max_output_tokens=4096, thinking_budget=0,
            )).with_structured_output(Critique, include_raw=True)
            intent_json = intent.model_dump_json(indent=2)
            prompt = ChatPromptTemplate.from_template(CRITIQUE_PROMPT)
            chain = prompt | llm
            raw_and_parsed = await asyncio.to_thread(
                chain.invoke,
                {
                    "query":       wrap_untrusted(user_query),
                    "intent_json": intent_json,
                    "response":    wrap_untrusted(response),
                },
            )
        _record_tokens("SelfRefine", "critique", raw_and_parsed.get("raw"))
        result: Critique = raw_and_parsed["parsed"]
        log.info(
            "Critique result",
            passes=result.passes,
            confidence=round(result.confidence, 2),
            violation_count=len(result.violations),
            critical=sum(1 for v in result.violations if v.severity == "critical"),
            major=sum(1 for v in result.violations if v.severity == "major"),
            minor=sum(1 for v in result.violations if v.severity == "minor"),
        )
        return result
    except Exception as e:
        log.warning(
            "Critique LLM call failed; treating as pass to avoid blocking user",
            error=str(e).splitlines()[0][:200],
            exc_info=True,
        )
        return Critique(passes=True, confidence=0.0,
                        overall_quality_notes="critique failed")


async def _refine(
    user_query: str,
    intent: UserIntent,
    response: str,
    critique: Critique,
    refiner_llm=None,
) -> str:
    """Run a single refinement pass. Returns the revised response.

    On any error: returns the original response so the user gets SOMETHING
    rather than nothing.
    """
    try:
        with log_time(log, "Self-refine refinement"):
            llm = refiner_llm or get_gemini_pro(
                temperature=0.3, max_output_tokens=20000, thinking_budget=2048,
            )
            intent_json = intent.model_dump_json(indent=2)
            violations_block = _format_violations(critique.violations)
            prompt = ChatPromptTemplate.from_template(REFINE_PROMPT)
            chain = prompt | llm
            result = await asyncio.to_thread(
                chain.invoke,
                {
                    "query":           wrap_untrusted(user_query),
                    "intent_json":     intent_json,
                    "violations_block": violations_block,
                    "quality_notes":   critique.overall_quality_notes or "(none)",
                    "response":        response,
                },
            )
        _record_tokens("SelfRefine", "refine", result)
        text = getattr(result, "content", None)
        if text is None:
            text = str(result)
        log.info(
            "Refinement done",
            original_len=len(response),
            refined_len=len(text),
            len_diff=len(text) - len(response),
        )
        return text
    except Exception as e:
        log.warning(
            "Refinement LLM call failed; returning original response",
            error=str(e).splitlines()[0][:200],
            exc_info=True,
        )
        return response


# ---------------------------------------------------------------------------
# The main loop
# ---------------------------------------------------------------------------

async def self_refine(
    response: str,
    user_query: str,
    intent: Optional[UserIntent],
    *,
    max_iterations: int = 2,
    min_response_chars: int = 500,
    critic_llm=None,
    refiner_llm=None,
    source_languages: tuple[str, ...] = (),
) -> tuple[str, list[Critique]]:
    """Generate-critique-refine loop over an existing response.

    The caller has already produced the initial response. This function
    audits it against the typed intent and refines it once (or twice) when
    violations are found.

    Skip-when-trivial:
      - intent is None / has confidence < 0.5 / expresses no explicit
        directives → no critic call, return original.
      - response < min_response_chars → no critic call (not worth the
        cost; the response is too thin to refine usefully).

    Force-run on language mismatch:
      When `source_languages` includes any code that differs from the
      target response language (intent.language), the critic runs even
      if the intent expresses no other directives. This catches the
      "English-query + Marathi-PDF → mixed response" failure mode at
      the audit layer — localize_prompt's directive is the primary
      defence, but the critic is the safety net when Gemini ignores
      the directive.

    Returns:
      (final_response, list_of_Critiques) — the critique list is the
      per-iteration record for telemetry. Empty list when the loop was
      skipped.
    """
    has_directives = _intent_has_directives(intent)
    has_lang_mismatch = _source_language_mismatch(intent, source_languages)
    if not (has_directives or has_lang_mismatch):
        return response, []
    if len(response) < min_response_chars:
        log.info(
            "Self-refine skipped — response below min length",
            response_chars=len(response), min=min_response_chars,
        )
        return response, []

    # When the loop is force-run on language-mismatch but the caller passed
    # no intent, build a default one so the critic still has a typed
    # ground-truth to reason against (the language='en' branch of the
    # critic prompt is enough to catch script mixing).
    critic_intent = intent if intent is not None else default_intent()

    history: list[Critique] = []
    current = response
    for iteration in range(max_iterations + 1):  # +1 for the final critique
        critique = await _critique(user_query, critic_intent, current, critic_llm)
        history.append(critique)
        if critique.passes:
            log.info(
                "Self-refine passed",
                iteration=iteration,
                confidence=round(critique.confidence, 2),
                cumulative_violations=sum(len(c.violations) for c in history),
            )
            return current, history
        # If confidence is too low, the critic isn't trustworthy — stop
        # rather than refine on a shaky verdict (see 2026 research:
        # self-correction without grounding can degrade quality).
        if critique.confidence < 0.5:
            log.info(
                "Self-refine stopping — low-confidence critique",
                iteration=iteration,
                confidence=round(critique.confidence, 2),
                violation_count=len(critique.violations),
            )
            return current, history
        if iteration >= max_iterations:
            log.warning(
                "Self-refine max iterations exhausted",
                iterations=iteration,
                final_violations=len(critique.violations),
            )
            return current, history
        # Refine
        current = await _refine(user_query, critic_intent, current, critique, refiner_llm)

    return current, history
