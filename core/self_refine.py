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
    - When the user invents a new directive (e.g. "keep all numerals
      and section names in English"), the critic catches native-script
      digit leakage and translated Act names, and the refiner rewrites
      them — without me adding a regex.

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
from core.logger import get_logger, log_time, short_err
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
                    "Example: \"Paragraph numbers use Devanagari digits "
                    "('१.', '२.', '३.') but the fixed-English-anchor "
                    "policy requires Latin ('1.', '2.', '3.') even when "
                    "language=mr.\"",
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
                    "Example: 'Replace every Devanagari digit in paragraph "
                    "numbers, dates, and amounts with its Latin counterpart "
                    "(०→0, १→1, २→2, ...). Rewrite the translated statutory "
                    "reference \"कलम १३८ परक्राम्य लिखत अधिनियम, १८८१\" as "
                    "the standard English span \"Section 138 of the "
                    "Negotiable Instruments Act, 1881\"; keep the surrounding "
                    "Marathi clause in Marathi.'",
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
    → RESPONSE PROSE — must be in the target language's native script.
      Body prose, ceremonial blocks (party role labels, courtroom forms
      of address), placeholder brackets, and signature labels are in the
      target language.

      FIXED-ENGLISH ANCHORS (audit for VIOLATIONS of the anchor rules;
      these apply regardless of strict_language):

      (a) NUMERALS — every digit in the response body MUST be Latin
          (0-9). Any Devanagari (०-९), Bengali (০-৯), Tamil (௦-௯),
          Telugu (౦-౯), Kannada (೦-೯), Malayalam (൦-൯), Gujarati (૦-૯),
          Gurmukhi (੦-੯), Odia (୦-୯), or Eastern-Arabic (۰-۹) digit is
          a MAJOR violation. Concrete leak patterns to flag:
             "१. वादीचे कथन ..."       ← MAJOR. Must be "1. वादीचे कथन ..."
             "२०२३ मध्ये"              ← MAJOR. Must be "2023 मध्ये"
             "रु. ५,००,०००"           ← MAJOR. Must be "Rs. 5,00,000"
             "पृष्ठ क्र. १५"           ← MAJOR. Must be "पृष्ठ क्र. 15"
             "(१) प्रथम कारण"          ← MAJOR. Must be "(1) प्रथम कारण"
             "दिनांक १५ मे २०२४"        ← MAJOR. Must be "दिनांक 15 May 2024"
          Applies to paragraph numbers, list-item prefixes, dates, years,
          amounts, page numbers, cheque numbers, case numbers, ages,
          addresses, quantities — every digit.

      (b) STATUTORY REFERENCES — the FULL statutory reference (label +
          Act/Code name + year) stays English inline as one uninterrupted
          span. Any translation of the label OR the Act name OR the year
          is a MAJOR violation:
             "कलम १३८ परक्राम्य लिखत अधिनियम, १८८१"
                                        ← MAJOR. Must be:
                "Section 138 of the Negotiable Instruments Act, 1881"
             "भारतीय नागरिक सुरक्षा संहिता, २०२३ चे कलम ४८० नुसार"
                                        ← MAJOR. Must be:
                "Section 480 of the Bharatiya Nagarik Suraksha Sanhita, 2023 नुसार"
             "अनुच्छेद २२६ भारतीय संविधान"
                                        ← MAJOR. Must be:
                "Article 226 of the Constitution of India"
             "आदेश XXXIX नियम १ व २ सीपीसी"
                                        ← MAJOR. Must be:
                "Order XXXIX Rules 1 and 2 CPC"
          The native-language connector ("च्या तरतुदींनुसार", "के तहत",
          "के अनुसार", "अंतर्गत") wraps the English statutory span; the
          span itself stays English.

      (c) TRANSLATED SECTION / ARTICLE / RULE / ORDER LABELS — the words
          "कलम", "धारा", "अनुच्छेद", "अध्याय", "नियम", "आदेश" appearing
          as the label of a statutory reference are MAJOR violations
          (the label must be the English "Section" / "Article" / "Rule"
          / "Order"). EXCEPT when quoting a caption that the source
          document itself uses in native script (e.g. quoting the printed
          heading of a Marathi-language Act's own section headings — rare;
          the vast majority of statutory references in Indian legal
          drafting are to the primary English enactment).

      (d) TRANSLATED ACT / CODE NAMES — the full name of any Indian Act
          or Code translated to native (e.g. "भारतीय दंड संहिता",
          "दिवाणी प्रक्रिया संहिता", "परक्राम्य लिखत अधिनियम") in body
          prose is a MAJOR violation. Use the standard English rendering
          ("Indian Penal Code", "Code of Civil Procedure", "Negotiable
          Instruments Act").

      (e) CASE-LAW CITATIONS remain English (party names + reporter
          cite) as before. Surrounding clause stays in the target
          language.

      (f) ENGLISH-ANCHOR-IN-CODE-FENCE — the English anchors above
          (statutory references, act/code names, article numbers, order
          rules, case-law citations, monetary amounts, dates, and
          numerals) MUST be emitted as PLAIN TEXT. Wrapping ANY of them
          in Markdown inline-code backticks or triple-backtick code
          fences is a MAJOR violation because the frontend renders that
          span in a monospaced typewriter font, breaking visual
          uniformity with the surrounding proportional-font body prose.
          Concrete patterns to flag:
             "`Section 138 of the Negotiable Instruments Act, 1881`"
                                      ← MAJOR. Must be plain text:
                "Section 138 of the Negotiable Instruments Act, 1881"
             "``Indian Contract Act, 1872``"
                                      ← MAJOR. Must be plain text.
             "`Article 226 of the Constitution of India`"
                                      ← MAJOR.
             "`Code on Wages, 2019`"
                                      ← MAJOR.
             "`Section 17(2)`"        ← MAJOR (bare Section-N label).
             "`Rs. 5,00,000`"         ← MAJOR (monetary amount).
             "`2024`" / "`15 May 2024`" ← MAJOR (year/date wrapped).
             "```\nSection 138 ...\n```" ← MAJOR (triple-fenced anchor).
             "<code>Section 138</code>" ← MAJOR (HTML code tag).
          BOLD (**...**) and ITALIC (*...*) around headings / subject
          lines / party-role emphasis are FINE and are NOT the same
          thing as inline-code — do not flag them. The violation is
          strictly backtick-based inline code, triple-backtick code
          fences, and <code> HTML tags around any English anchor.
          Suggested_fix: "Remove the backticks (or code fence, or
          <code> tags) surrounding <specific anchor span> in <paragraph
          N> — emit that span as plain running text so it renders in
          the same proportional font as the surrounding {{lang}} prose;
          preserve any adjacent bold/italic markdown, only the code
          formatting is being stripped."

      strict_language=True additionally forbids stray English narrative
      clauses in body prose (see the strict_language section below), but
      the FIXED-ENGLISH ANCHOR rules above apply in BOTH strict and
      non-strict mode.

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
            "ग्रामसभेचे नाव", "राजूचा पत्ता", "Section 138 च्या
            तरतुदींनुसार" (the possessive suffix chained onto the
            English statutory span is the Marathi form; Hindi would
            write "Section 138 के प्रावधानों के अनुसार").
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
    → STRICT MODE for BODY PROSE. Audit the following:
       (a) Any English NARRATIVE clause / connector / stock phrase in
           body prose ("It is submitted that", "as per", "in accordance
           with", "under the provisions of", "kindly note", "may be
           pleased to", "the humble petitioner submits") — MAJOR. The
           body must be written in the target language's pleading
           register.
       (b) Ceremonial blocks in English (Plaintiff / Defendant / Versus /
           Prayer / Verification / Affidavit / Hon'ble Court) instead of
           the target-language translation (वादी / प्रतिवादी / विरुद्ध /
           विनंती / प्रमाणीकरण / शपथपत्र / मा. न्यायालय) — MAJOR.
       (c) Placeholder brackets in English ("[Place]", "[Date]",
           "[Advocate's Address]") instead of target-language equivalents
           ("[ठिकाण]", "[दिनांक]", "[वकिलाचा पत्ता]") — MAJOR.
       (d) An entire sentence written in English inside the body prose
           (as opposed to English statutory references or case citations,
           which are permitted anchors — see below) — MAJOR.

      NON-VIOLATIONS in strict mode (these are REQUIRED English anchors):
       (i)  Latin digits everywhere (dates, years, amounts, paragraph
            numbers, list-item prefixes, section numbers) — REQUIRED.
            Do NOT flag "1.", "(2)", "Rs. 5,00,000", "15 May 2024",
            "Section 138" as violations; those are the correct form.
       (ii) Full statutory references in English ("Section 138 of the
            Negotiable Instruments Act, 1881", "Article 226 of the
            Constitution of India", "Order XXXIX Rules 1 and 2 CPC") —
            REQUIRED. The label + Act name + year travel as one English
            span; the surrounding native-language clause wraps it. Do
            NOT flag this pattern; DO flag if the response translated
            the label / Act name / year to native script (that's what
            the (a)-(e) checks above cover).
       (iii) Case-law citation blocks in English ("Kesavananda Bharati
             v. State of Kerala, AIR 1973 SC 1461") — REQUIRED.
      In short: strict_language means no ENGLISH NARRATIVE inside
      target-language prose; but Latin digits and English statutory /
      case-law anchors ARE the correct form and MUST NOT be flagged.

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
    needed)", "{{section number}}", "<insert party>", "TBD", "FILL IN".
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

  duplicate_section_block — the draft contains two structurally-equivalent
    blocks that a real filing has only once. Common patterns to detect:
       (a) Two "Prayer" / "Prayer for Relief" / "PRAYER" blocks, each
           with its own (a)/(b)/(c) clauses. A single filing has ONE
           prayer.
       (b) Two "Verification" blocks, each opening with "I, <deponent>,
           do hereby verify that the contents of paragraphs...". A single
           filing has ONE verification.
       (c) Two full signature / place-date footer blocks ("Place: X /
           Date: Y / APPLICANT / THROUGH / [ADVOCATE FOR APPLICANT]"),
           each followed by more body text.
       (d) A second full document heading repeating after the first one
           has already concluded — e.g. "## AN APPLICATION FOR THE GRANT
           OF BAIL U/S 437 OF THE CRIMINAL PROCEDURE CODE, 1973" appearing
           15+ paragraphs AFTER the first bail-application caption and
           first Prayer / signature / Verification have already ended.
       (e) A second cause title block (court name + case number + party
           details + "vs" + party details) appearing mid-document after
           the opening cause title has already been established, typically
           introduced by a `---` separator or a fresh heading.
       (f) A second "IN THE MATTER OF:" header stranded before a body
           block that ALREADY sits under the initial cause title.
    These duplications arise when the section fan-out generator emits
    redundant section bodies that overlap with earlier sections. Flag
    MAJOR. Suggested_fix: KEEP the FIRST occurrence (embedded in the
    natural flow of the document) and REMOVE the second occurrence
    entirely, along with any transition / separator markup ("---",
    "Continued below", stranded "IN THE MATTER OF:") that introduces
    it. Do NOT merge the two blocks — they are duplicates, not distinct
    variants. Be lenient: if paragraphs 20+ genuinely continue argument
    that was cut short at paragraph 10 (e.g. numbered facts 1-9, then a
    procedural interlude, then facts 10-15), that's continuation, not
    duplication — do NOT flag. The signature is (a) same section-type
    header repeating, or (b) numbered-paragraph counter resetting to 1
    mid-document.

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

  unverified_web_citation — [PIPELINE-LEVEL — applies to every response
    that touched web-grounded search, i.e. Scenario, Legal_Concepts, and
    any domain agent that fell back to the web layer.] Flag as MAJOR
    whenever the response body surfaces ANY of the following:
      (i)  A bare web-domain name treated as a citation, source label,
           or bracketed marker. Concrete patterns:
             `[leg-vakilsearch.com-44320]`, `[web-812345]`,
             `[hc-<domain>-<digits>]`, `[leg-<domain>-<digits>]`,
             "Source: livelaw.in", "According to vakilsearch.com",
             "per chamberofmayank.com", "as noted by ipleaders.in",
             a trailing `[<domain>-<id>]` after a sentence, ANY bracket
             whose content is a domain-shaped string (e.g. `<word>.com`,
             `<word>.in`, `<word>.co.in`, `<word>.org`).
      (ii) An external hyperlink to a web page — any `[<label>](https://...)`
           whose URL is NOT (a) an `api.sci.gov.in/...` Supreme Court PDF
           URL from the whitelist below, (b) an official High Court PDF
           URL that appears verbatim in the whitelist, or (c) a Lawttorney
           S3 bucket link that appears verbatim in the whitelist. Blog
           posts, news articles, aggregator pages, forum posts, and
           content-mill URLs are all MAJOR violations regardless of the
           domain — even if the domain is on the ingestion allowlist
           (indiankanoon.org, barandbench.com, etc.), the URL itself
           must not be a visible link.
     (iii) Any internal retrieval identifier leaked verbatim into the
           body: `leg-*`, `hc-*`, `sci-*`, `web-*`, `newacts-*`,
           `const-*`, `maxim-*`, `gst-*` id-shaped tokens appearing as
           bracketed markers, footnotes, superscripts, or standalone
           strings. These are internal index handles, never user-visible.
     (iv) A "Sources" / "References" / "Web References" / "External
          Sources" appendix that enumerates web pages, domain names, or
          search-grounding results. The only permitted appendix is
          `## PDF Links` populated exclusively from VERIFIED entries
          (api.sci.gov.in PDFs, official High Court PDF URLs, Lawttorney
          S3 links) present verbatim in the whitelist below.
    Suggested_fix must be surgical: name the exact offending token /
    marker / URL and specify the replacement. Examples:
      - "Strip the trailing `[leg-vakilsearch.com-44320]` marker after
         paragraph 3 sentence 2; restate the sentence without any
         attribution."
      - "Remove the external hyperlink `[livelaw article](https://
         livelaw.in/...)` in paragraph 5; keep the underlying legal
         point but attribute it to Section <X> of the <Act> only,
         without any URL."
      - "Delete the '## References' section at the end that lists web
         URLs; leave only the `## PDF Links` block with verified
         api.sci.gov.in entries (if any)."
    This category is INDEPENDENT of `unretrieved_citation` — even when
    the underlying legal proposition is correct, surfacing a web
    attribution or retrieval id is still a MAJOR violation.

  unretrieved_citation — [PIPELINE-LEVEL — applies to scenario, multi-
    agent, and single-agent responses; the source-registry version of
    fabricated_citation.] Flag as MAJOR when the response cites any case
    name, quoted statutory provision, or PDF URL that is NOT present in
    the "## Retrieved Sources (allowed-citation whitelist)" block passed
    to you below. Also flag as MAJOR every bracketed placeholder like
    "[citation to be verified]", "[verify]", "[TBD]", "[citation
    needed]", "[to be confirmed]" or similar — even if the surrounding
    prose is otherwise fine. Also flag citation drift: same party names
    but different volume / page / court abbreviation / year from the
    retrieved-source record. If a case appears in the response but no
    entry in the retrieved-sources block contains those party names,
    it is a hallucination from training memory. Suggested_fix: "Replace
    the hallucinated cite of <invented case> with a case from the
    retrieved-sources block (e.g. <closest match>), or drop the citation
    and restate the point without an authority." Do NOT flag when the
    retrieved-sources block is literally empty (marker "(none — ...)"),
    because in that case the response layer has no whitelist to enforce
    against — flag only when at least one retrieved source is available
    AND the response cites something not in it.

    ### URL character-match sub-rule (catches fabricated PDF URLs)
    For EVERY URL emitted in the response (typically inside
    `[Judgment PDF](https://...)` markdown links or trailing
    `## PDF Links` blocks), check character-by-character whether the
    URL string appears verbatim in one of the `pdf_urls: [...]`
    entries in the whitelist. If a URL is NOT a verbatim character-
    for-character match to a whitelist URL, flag as MAJOR — even if
    the case name in the surrounding sentence IS in the whitelist,
    even if the URL "looks like" a plausible `api.sci.gov.in` /
    `indiankanoon.org` / `hcbombay.gov.in` / etc. path. LLMs will
    fabricate plausibly-shaped URLs (e.g. inventing
    `api.sci.gov.in/supremecourt/YYYY/MM/DD/judgment_YYYY-MM-DD.pdf`
    when the real retrieved URL is
    `api.sci.gov.in/supremecourt/2023/12064/12064_2023_1_1501_56228_Judgement_03-Oct-2024.pdf`).
    Path patterns, ID numbers, and file names must match exactly.
    Suggested_fix: "Replace the fabricated URL `<invented>` with
    the retrieved URL `<real url from whitelist>` for <case name>,
    or if the case has no PDF in the whitelist, drop the `[Judgment
    PDF](...)` markdown link and cite the case name only."

    ### Cross-source-type hallucination sub-rule (catches invented HC
    cases when only SCI was retrieved, etc.)
    Group the whitelist entries by their court/source type using the
    party-name and citation format as signal (Supreme Court cases end
    in `... - <YEAR>` / `... INSC ...` / `... SCC ...` and match the
    `sci-*` id prefix in the whitelist; High-Court cases carry court
    abbreviations `Del`, `Bom`, `Mad`, `Kar`, `All`, `Hyd`, `HP`, etc.
    or ITR/CTR reporter cites). If the whitelist contains ZERO entries
    of a particular court/source type (e.g. no High-Court records
    retrieved at all), and the response nonetheless cites a case
    attributed to that court/type (e.g. "Delhi High Court held in
    *Sonansh Creations Pvt. Ltd. v. ACIT, (2025) SCC OnLine Del ...*"
    when no `[hc-*]` or Delhi-HC record is in the whitelist), flag as
    MAJOR — it is a training-memory hallucination that survived the
    party-name check because it doesn't collide with any retrieved
    entry to compare against. Suggested_fix: "Drop the invented
    <court-type> citation of <case> because no <court-type> records
    were retrieved for this request; rely on the retrieved <other-
    court-type> authorities instead, or state the point without a
    case cite."

  canonical_example_substitution — the draft uses canonical Indian-legal
    example names / places / dates from LLM training data as if they were
    the user's real facts. Substitution set to flag: "Priyanka", "Sneha",
    "Bhausaheb", "Sakore", "Anjali Deshmukh", "Rakesh Sharma", "Ram Kumar",
    "Sita Devi", "Jyoti Narendra Amrutkar", "Nashik", "Sangamner",
    "Ahmednagar", "Pune Civil Court", "Sangamner Taluka", "29 May 2022",
    "1 June 2020", "16 May 2013". These are training-set artefacts, not
    case facts. When the user's query or an uploaded source document
    names DIFFERENT real parties/places/dates, appearance of any of these
    canonical values in the draft is CRITICAL — the model substituted
    its prior for a real fact. The refiner MUST replace each canonical
    value with the corresponding real value from the query or source.
    Do NOT flag when the user's own query/source ACTUALLY contains one
    of these names (e.g. a real client happens to be named "Priyanka" and
    the source PDF confirms it) — the check is context-sensitive:
    compare against the query + source, not against the substitution
    list alone. Suggested_fix: "Replace 'Priyanka' in paragraphs 3, 7,
    12 with the client name from the source ('<real name>'); replace
    'Nashik' with '<real place>'."

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

  reply_notice_correspondence_framing — when the draft is a REPLY to a
    legal notice (identifiable from the response's OWN text: (i) the
    subject line begins with "Reply to your Legal Notice" / "Reply to
    your notice dated ___" / "In reply to your legal notice dated ___",
    or (ii) the opening body paragraph establishes rejoinder to a prior
    notice — "your legal notice dated ___ served on my client",
    "hereby reply to the legal notice dated ___ issued by you on behalf
    of ___"), the sender letterhead, addressee block, and body voice
    must be internally consistent AND must respect Indian reply-notice
    conventions. The critic does not see the source PDF — cross-check
    the letterhead and "To" block against the response's OWN subject
    line and opening body paragraph, which name the real parties. Three
    sub-patterns to flag:

    (a) Sender-voice contradiction. The "From the desk of" / letterhead
        block names the REPLYING PARTY directly (party-as-sender), but
        the body speaks in an advocate's voice ("Under the instructions
        and on behalf of my client, Shri [Party Name]...", "my client
        instructs me to reply", "on the strength of my client's
        instructions"). A reply notice is EITHER (i) party-authored
        — letterhead names the party, body opens first-person "I,
        [Party Name], do hereby reply to the legal notice dated ___
        served on me by you..." — OR (ii) advocate-authored —
        letterhead names the replying advocate (name, enrolment number,
        office address), body opens "Under the instructions of my
        client, Shri [Party Name]...". Mixing the two (party name in
        the letterhead AND "my client Shri [same party name]" in the
        body) is MAJOR. Additional signal: the same party name appears
        BOTH in the "From the desk of" line AND immediately after "my
        client" in the opening body paragraph — that is the tell.
        Suggested_fix: pick ONE artefact. If the reply is intended to
        be advocate-issued (mirroring the source notice's advocate
        letterhead), rewrite the letterhead as "[Replying Advocate —
        Name, Enrolment No., Office Address, Contact]" — preserve as
        a bracketed placeholder if the replying advocate's identity was
        NOT supplied by the user or the source — and keep the "on
        behalf of my client" body voice. If the reply is genuinely
        party-authored, keep the party's name in the letterhead and
        rewrite the body opening to first-person "I, [Party Name], do
        hereby reply to the legal notice dated ___ served on me by
        you...".

    (b) Missing claimant in the "To" block. The source legal notice
        was issued BY an advocate ON BEHALF OF a named client (the
        CLAIMANT — typically identified in the reply's own subject
        line or opening body paragraph as "your client Shri [Claimant
        Name] of M/s [Company]"). A reply notice must address BOTH
        (i) the opposing advocate (for service) AND (ii) the claimant
        party (whose claims are being denied on the merits). If the
        "To" block names only the opposing advocate — with no
        "Advocate for [Claimant Name]" qualifier and no separate
        "Through his Advocate:" sub-block naming the claimant above —
        the substantive service chain breaks. Concrete failure
        pattern:
           "To, [Opposing Advocate Name], Advocate, Office: [Address]."
           (no claimant identified anywhere in the To block, even
           though the response's own subject line names "your client
           Shri [Claimant Name] of M/s [Company]")
                                        ← MAJOR.
        Suggested_fix: rewrite the "To" as EITHER the two-level form —
           "To,
            1. [Claimant Name, Designation, M/s Company],
               [Claimant Address as stated in the response's opening
                body paragraph or source description].
            Through his Advocate:
               [Opposing Advocate Name, Office Address]."
        OR the compact "Advocate for" form —
           "To, Shri [Opposing Advocate Name], Advocate for
            Shri [Claimant Name] ([Designation] of M/s [Company]),
            Office: [Opposing Advocate Office Address]."
        Populate the [Claimant ...] and [Address] slots from the
        claimant identification that the response's own subject/body
        already surfaces. Never leave the claimant anonymous, never
        invent a claimant not identifiable from the response itself.

    (c) Recipient identity hallucination in the To block. The "To"
        block names an opposing-advocate identity or office address
        that does NOT appear anywhere else in the response (subject
        line, opening body paragraph, reference to the source notice).
        Reply notices must be served on the SAME advocate at the SAME
        office address that issued the source notice; any fresh
        advocate name or fresh office address that the response's own
        body does not corroborate is a service defect. Flag MAJOR.
        Distinct from `canonical_example_substitution` (which covers
        substantive party/place substitution in the body) — this rule
        specifically covers the address block of a reply notice.
        Suggested_fix: "Replace the unsupported opposing-advocate
        [Name / Address] in the To block with the placeholder
        `[Opposing Advocate — Name and Office Address]` and leave the
        population to fact-injection at service time."

    These three sub-flags fire in ADDITION TO (not instead of) the
    generic `legal_artifact='legal_notice'` intent check, which
    enforces format-level completeness for any notice. Reply-notice
    framing is reply-specific and needs its own audit.

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

  reliefs_section_duplication — the draft contains TWO adjacent
    sections that both function as the enumerated reliefs list, e.g.
    "## Reliefs Sought" (with a-d listing damages, interest, costs,
    catch-all) IMMEDIATELY FOLLOWED BY "## Prayer" (with a-d listing
    the same damages, interest, costs, catch-all). Any Indian
    pleading has ONE reliefs enumeration — the Prayer clause. A
    separate "Reliefs Sought" section that repeats the same substance
    is structural redundancy, not two distinct pleading blocks.
    Signals:
       Both sections open with the same substantive relief items in
       the same order.
       "Reliefs Sought" restates the reliefs in a-d form and "Prayer"
       restates the same reliefs in a-d form with a "WHEREFORE ..."
       preamble.
       A short forwarding stub "Reliefs Sought" ("The Plaintiff claims
       the reliefs detailed in the prayer clause hereunder") followed
       by a "Prayer" is ALSO redundant — the stub adds no substance;
       remove the stub and keep only the Prayer.
    MAJOR. Suggested_fix: "Merge the two sections into a single
    canonical `## Prayer` section that opens with 'WHEREFORE ...' and
    enumerates the reliefs a/b/c/... once. Delete the duplicate
    `Reliefs Sought` section (or its forwarding stub). Renumber any
    following section (Verification, List of Documents) so paragraph
    counts remain consistent."

  prayer_generic_boilerplate — the Prayer clause exists but its
    enumerated reliefs are MATERIALLY THINNER OR MORE GENERIC than
    what the pleaded Facts and Legal Grounds justify. The Prayer must
    reflect THIS matter's specific reliefs — not the reference
    template's boilerplate. Concrete gap patterns:
       Facts plead REJECTION of specific goods delivered in defective
       / damaged condition, or Facts plead an advance already PAID
       against the contract → Prayer MUST include a specific relief
       directing the Defendant to take back the rejected goods and
       refund the price paid (or a proportionate refund), OR to
       replace the defective goods with conforming goods. A Prayer
       that asks only for undifferentiated "damages for breach of
       contract" misses the concrete replace/refund/take-back relief
       the pleaded facts already justify → MAJOR.
       Facts + Legal Grounds identify DISTINCT DAMAGES HEADS (cost of
       goods, loss of profit on intended resale, surveyor / inspection
       cost, storage / transportation of rejected consignment, cost of
       legal notice, reputational loss) → the Prayer MUST list those
       heads separately with quantum placeholders, not collapse them
       into a single "Rs. [Amount of Damages Claimed]" line → MAJOR.
       Legal Grounds section builds up specific statutory authority
       for the reliefs (e.g. Section 73 of the Indian Contract Act,
       1872; Sections 41 / 59 / 60 of the Sale of Goods Act, 1930;
       Section 34 of the Code of Civil Procedure, 1908 for interest)
       → the Prayer MUST anchor each substantive relief to the
       operative statute cited above ("Pass a decree ... under Section
       73 of the Indian Contract Act, 1872 read with Section 59 of
       the Sale of Goods Act, 1930", "pendente lite and future
       interest under Section 34 of the Code of Civil Procedure,
       1908"). A Prayer that asks bare "damages" and "interest"
       without tethering to the statutes the Legal Grounds section
       cites is generic boilerplate inherited from the reference
       template → MAJOR.
       Facts describe SPECIFIC PROPERTY / GOODS with identifying
       detail (1000 refrigerators of a stated brand, an immovable
       property with CTS/survey number, a specific consignment) →
       the Prayer's substantive reliefs MUST reference the same
       identified subject-matter, not talk about "the goods" or "the
       property" in the abstract → MAJOR.
       Facts plead RESCISSION / REPUDIATION of the contract, OR
       exercise a statutory right of rejection (Section 41 of the
       Sale of Goods Act, 1930; Section 39 of the Indian Contract
       Act, 1872) → the Prayer MUST include a DECLARATORY relief
       ("Pass a decree declaring that the contract dated ___ stands
       rescinded / that the Plaintiff's rejection of the consignment
       is valid and binding on the Defendant"). Restitution and
       damages flow from a rescinded contract; a Prayer that seeks
       only money without asking the Court to declare the rescission
       leaves the primary status question unadjudicated → MAJOR.
       Facts plead a LEGAL NOTICE that demanded reliefs IN THE
       ALTERNATIVE (typical patterns: "replace the defective goods
       OR refund the price", "perform the contract OR pay damages",
       "vacate the premises OR pay mesne profits") → BOTH alternative
       reliefs MUST appear in the Prayer, either as alternative
       prayers ("(a) direct the Defendant to replace ... ; (b) in the
       alternative, refund ...") or as compounded prayers. Silently
       dropping one alternative to keep only the money claim exposes
       the Plaintiff to Order II Rule 2 CPC bar in any subsequent
       suit for the abandoned relief → MAJOR.
       Facts plead a PRE-SUIT LEGAL NOTICE with a monetary demand
       that went unpaid → the Prayer MUST include PRE-SUIT interest
       from the date of the legal notice (or date of breach), not
       merely pendente lite and future interest from the date of
       filing. Statutory anchors: Section 34 of the Code of Civil
       Procedure, 1908 (pre-suit + pendente lite + future); Section
       61 of the Sale of Goods Act, 1930 (seller's / buyer's right
       to interest on the price); Section 3 of the Interest Act,
       1978 (interest on debts and damages). A Prayer that skips
       the pre-suit head silently truncates the claim → MAJOR.
       Facts plead BOTH (a) restitution of a sum already paid
       (advance / consideration / earnest money to be returned upon
       rescission — Section 65 of the Indian Contract Act, 1872 or
       Section 62 of the Sale of Goods Act, 1930) AND (b)
       compensation for loss caused by the breach (damages under
       Section 73 of the Indian Contract Act, 1872) → the Prayer
       MUST separate the restitutionary head from the damages head
       ("(a) direct restitution of Rs. ___ under Section 65 ICA;
       (b) award damages of Rs. ___ under Section 73 ICA"). Rolling
       both into a single lump-sum decree ("Pass a decree for Rs.
       ___ comprising refund of advance + loss of profit + ...") is
       legally sloppy: the court can grant one head and refuse the
       other, and the two heads rest on different burdens of proof
       and different limitation clocks → MAJOR.
    MAJOR. Suggested_fix must be CONCRETE and derived from THIS
    draft's Facts + Legal Grounds — never a generic "improve the
    prayer" instruction. Example: "Rewrite Prayer clause (a) as: 'Pass
    a decree directing the Defendant to take back the [Number of
    Damaged Units] rejected refrigerators from the Plaintiff's
    warehouse at [Plaintiff's Warehouse Address] and refund the sum
    of Rs. [Amount Paid] paid by way of advance, together with damages
    of Rs. [Damages Claimed] under Section 73 of the Indian Contract
    Act, 1872 read with Section 59 of the Sale of Goods Act, 1930.'
    Add clauses for (i) loss of profit on intended resale, (ii)
    surveyor's fee, (iii) cost of legal notice, before the interest
    and costs prayers. Anchor the interest prayer to Section 34 of
    the Code of Civil Procedure, 1908, and claim pre-suit interest
    from the date of the legal notice."

  under_detailed_draft — [FIRES ONLY when `intent.response_depth == "detailed"`
    AND the response is a legal draft (task_intent='draft' or the response is
    clearly a draft).] The user explicitly asked for a DETAILED draft but the
    output is skeletal — a competent short draft, not a comprehensive one.
    Fires when ANY of the following holds on a document type that carries the
    relevant structural feature:
       GROUNDS section present but fewer than 15 numbered grounds, OR each
       ground is a single sentence rather than a 3-5 sentence paragraph of
       substantive argument. (Applies to writ petitions, plaints, bail /
       anticipatory bail / regular bail applications, revisions, appeals,
       counter-affidavits, review petitions.)
       ZERO landmark Supreme Court precedents woven inline into the grounds
       on a doc type where doctrinal anchors are expected. Anticipatory
       bail without Gurbaksh Singh Sibbia / Siddharam Mhetre / Arnesh Kumar;
       regular bail without Sanjay Chandra / Dataram Singh; writ quashing
       without Bhajan Lal / R.P. Kapur — the omission is a MAJOR gap when
       the user asked for detail.
       FACTS section reduced to a single summary paragraph when the uploaded
       source has multiple paragraph-level events, dates, complainants,
       witnesses, recovered articles — the Facts should walk the source
       chronologically, not compress.
       Bail-type draft (anticipatory / regular / interim) MISSING character /
       conduct paragraphs (applicant's roots in society, family ties,
       occupation, clean record, willingness to cooperate) OR missing
       custodial-interrogation submissions (why custodial is not required,
       nothing to recover, cooperation offered, parity with released
       co-accused). One or the other omission → MAJOR. Both missing →
       CRITICAL.
       PRAYER with only a single relief when the Facts + Grounds justify
       main relief + interim relief + costs + omnibus. Detailed drafts
       enumerate multiple sub-lettered reliefs.
       STATUTORY-FRAMEWORK section absent or reduced to a single sentence
       naming the statute — a detailed draft REPRODUCES the text of each
       relied-upon provision and applies it to the matter.
       ASSEMBLED LENGTH — the assembled draft body is under ~12,000
       characters for a document type that carries multiple substantive
       sections (writ, plaint, detailed bail, appeal, revision). Short
       assembled length is a strong SIGNAL of under-detail; combine with
       one or more of the above to flag under MAJOR (do not flag length
       alone).
    Severity: individual omissions are MAJOR; two or more concurrent
    omissions on the same draft → CRITICAL. Suggested_fix must be
    CONCRETE and reference the specific missing section / grounds /
    citations by the doc type. Example: "Expand the Grounds section from
    9 numbered grounds to 15+. Add a dedicated ground on custodial
    interrogation not being required (nothing to recover, cooperation
    offered, disclosure-statement-only case). Add character/conduct
    paragraphs covering the Applicant's roots in Sirsa, occupation,
    family ties, clean past record. Weave in Gurbaksh Singh Sibbia v.
    State of Punjab (1980) 2 SCC 565 (the seminal test for anticipatory
    bail), Siddharam Satlingappa Mhetre v. State of Maharashtra (2011)
    1 SCC 694 (life and personal liberty under Article 21), and Arnesh
    Kumar v. State of Bihar (2014) 8 SCC 273 (safeguards against
    routine arrest). Add a Prayer sub-clause (b) for interim
    anticipatory bail during pendency and sub-clause (c) omnibus
    relief."

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

## Retrieved Sources (allowed-citation whitelist for the `unretrieved_citation` category)
The following are the ONLY case names, statutory quotes, and PDF URLs the
response is permitted to cite. Anything cited in the response that is not
here is a hallucination — flag under `unretrieved_citation`.

{retrieved_sources_whitelist}

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
6. FIXED-ENGLISH ANCHOR ENFORCEMENT (applies whenever `language` is a
   non-English target — Hindi, Marathi, Bengali, Tamil, etc., in both
   strict and non-strict mode):

   The correct form is:
     * Latin digits (0-9) for ALL numerals — paragraph numbers, list-
       item prefixes, dates, years, amounts, page/case/cheque numbers,
       ages, quantities.
     * Full statutory references in English inline as one span —
       "Section 138 of the Negotiable Instruments Act, 1881",
       "Article 226 of the Constitution of India", "Order XXXIX
       Rules 1 and 2 CPC" — with the surrounding native clause
       wrapping the English span.
     * Case-law citations in English (party names + reporter cite).

   When the prior response violates these anchors (native-script digits
   like "१. वादीचे कथन", translated Act names like "परक्राम्य लिखत
   अधिनियम, १८८१", translated section labels like "कलम १३८"), do a
   CLEAN PASS:
     - Replace every native-script digit with its Latin counterpart
       (०→0, १→1, २→2, ३→3, ४→4, ५→5, ६→6, ७→7, ८→8, ९→9; analogous
       for Bengali ০-৯, Tamil ௦-௯, Telugu ౦-౯, Kannada ೦-೯, Malayalam
       ൦-൯, Gujarati ૦-૯, Gurmukhi ੦-੯, Odia ୦-୯, Eastern-Arabic ۰-۹).
     - Replace the translated label + Act name + year with the standard
       English statutory reference: "कलम १३८ परक्राम्य लिखत अधिनियम,
       १८८१" → "Section 138 of the Negotiable Instruments Act, 1881".
     - Keep the surrounding native-language clause in the target
       language: rewrite "परक्राम्य लिखत अधिनियम, १८८१ च्या कलम १३८
       च्या तरतुदींनुसार" as "Section 138 of the Negotiable Instruments
       Act, 1881 च्या तरतुदींनुसार".
     - Do NOT leave any native-script digit anywhere in the response.
     - Do NOT leave any translated Act name / section label in body
       prose.

   When the violation is the opposite direction (English narrative
   clauses inside a strict-language response — "It is submitted that",
   "as per", "in accordance with"), translate the narrative clause into
   the target language while KEEPING the statutory reference and any
   digits in their English/Latin form.

7. ENGLISH-ANCHOR-IN-CODE-FENCE STRIPPING. If the auditor flagged an
   `english_anchor_in_code_fence` violation (or the prior response wraps
   any English anchor — statutory reference, act/code name, article
   number, order rule, case-law citation, monetary amount, date, or bare
   numeral — in Markdown inline-code backticks, triple-backtick fences,
   or HTML `<code>` tags), STRIP the wrapping markup while leaving the
   anchor text and every character of the surrounding native-language
   clause unchanged. Concrete rewrites:
     - "`Section 138 of the Negotiable Instruments Act, 1881`" →
       "Section 138 of the Negotiable Instruments Act, 1881"
     - "``Indian Contract Act, 1872``" →
       "Indian Contract Act, 1872"
     - "`Code on Wages, 2019`" →
       "Code on Wages, 2019"
     - "`Section 17(2)`" →
       "Section 17(2)"
     - "```\nSection 138 of the Negotiable Instruments Act, 1881\n```" →
       "Section 138 of the Negotiable Instruments Act, 1881" (inlined
       back into the surrounding sentence — do not leave a stranded
       standalone paragraph where a code-fenced block used to be).
     - "<code>Section 138 of the NI Act</code>" →
       "Section 138 of the NI Act"
   PRESERVE any adjacent bold (**...**) or italic (*...*) markdown —
   only the backticks / code fences / <code> tags are removed. Do NOT
   translate the anchor into the target language while stripping the
   fence; the anchor stays English by policy (Rule 6). Do NOT introduce
   new anchor content — only unwrap what was already there.

8. PLACEHOLDER PRESERVATION. Never substitute concrete values (party
   names, addresses, dates, monetary amounts, cheque numbers, enrolment
   numbers, PAN / GSTIN / registration numbers, phone / email, section
   numbers filled in when the source used `[Section]`) for bracketed
   `[Placeholder]` fields unless the user query OR the prior response's
   own factual context (its subject line, its opening body paragraph,
   its cited source material) already contains that value verbatim.
   Preserve every `[…]` marker exactly — the bracket syntax, the label
   inside, the position in the sentence.

   When a violation's suggested_fix directs you to INSERT a placeholder
   (e.g. "replace the letterhead with `[Replying Advocate — Name,
   Enrolment No., Office Address]`", "replace 'Rs. 1,50,00,000/-' with
   `Rs. [Total Contract Value]/-`"), emit that placeholder LITERALLY —
   do NOT populate it with any name, address, amount, or date pulled
   from elsewhere in the document to "make the draft look complete."
   An honestly-bracketed placeholder is the correct form; a plausibly-
   invented concrete value is a fact hallucination.

   Concrete failure patterns this rule prevents:
     - Reply-notice letterhead: the prior response has the replying
       party's name in "From the desk of" (a Sender-voice contradiction
       flagged by `reply_notice_correspondence_framing (a)`). The
       correct fix is to replace that letterhead with `[Replying
       Advocate — Name, Enrolment No., Office Address, Contact]`, NOT
       to grab a different party's name from the source PDF and drop
       it in.
     - Prayer clauses: Facts para reads `Rs. [Total Contract Value]/-`
       and the critic flags `prayer_relief_mismatch`. The correct fix
       is to add the missing relief while KEEPING the `[Total Contract
       Value]` placeholder verbatim in both the Facts and the Prayer,
       NOT to invent `Rs. 1,50,00,000/-` and back-propagate it.
     - Party details: cause title reads `[Plaintiff Name], aged
       [Age] years, resident of [Address]`. The correct fix for any
       violation touching the cause title is to leave those placeholders
       intact, NOT to substitute "Anjali Deshmukh, aged 42, resident of
       Pune" from the LLM's canonical-example bank.

   Cross-checks:
     - Count bracketed `[...]` markers in the prior response. The
       refined response must have AT LEAST that many bracketed markers
       (may have more if a violation's suggested_fix explicitly adds
       one). Fewer bracketed markers = you substituted a concrete
       value. Undo it.
     - If a suggested_fix's example text contains a bracketed
       placeholder, that placeholder MUST appear verbatim in your
       refined output.

## Inputs

Original user query (untrusted):
{query}

User intent (this is the ground truth — do NOT deviate):
```json
{intent_json}
```

## Retrieved Sources (allowed-citation whitelist)
When fixing `unretrieved_citation` violations, replace the hallucinated
citation with the CLOSEST-topic case, statute, or URL from this whitelist
— never restore the invented citation, and never invent a new one.
If nothing in this whitelist supports the point, state the legal
principle without any case citation rather than emitting a placeholder.

{retrieved_sources_whitelist}

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
    retrieved_sources_whitelist: str = "",
) -> Critique:
    """Run a single critique LLM call. Returns a Critique (passes + violations).

    On any error: returns a "passes=True, confidence=0" critique so the loop
    treats it as "nothing to do" and stops. We do not want a critic blip to
    break the user-visible request.

    `retrieved_sources_whitelist` is the SourceRegistry-derived allowed-
    citation pool for the `unretrieved_citation` category. Empty string
    (the default) means "no whitelist available" — the critic will skip
    that category for this call (see the category description in
    CRITIQUE_PROMPT). Populated by the orchestrator when it calls
    self_refine after multi-agent synthesis.
    """
    # Circuit breaker: skip the critic entirely when Gemini Flash is
    # unhealthy. Better to ship an un-audited draft (same as the existing
    # "critique failed → passes=True" fallback) than to sit for 45s
    # waiting on a call that we already know is failing.
    from core.clients import (
        is_gemini_flash_available, record_gemini_flash_failure,
        record_gemini_flash_success,
    )
    if not is_gemini_flash_available():
        log.warning("Gemini Flash circuit open — skipping critique",
                    fast_fail=True)
        return Critique(passes=True, confidence=0.0,
                        overall_quality_notes="critique skipped (Flash circuit open)")

    try:
        with log_time(log, "Self-refine critique"):
            llm = (critic_llm or get_gemini_flash_full(
                temperature=0.0, max_output_tokens=4096, thinking_budget=0,
            )).with_structured_output(Critique, include_raw=True)
            intent_json = intent.model_dump_json(indent=2)
            prompt = ChatPromptTemplate.from_template(CRITIQUE_PROMPT)
            chain = prompt | llm
            # Bound the critic call. Without this, a hung Gemini Flash
            # blocks the caller's drafting semaphore slot until gunicorn
            # kills the worker at 300s — cascading failure across every
            # in-flight request in that worker. `bounded_wait_for`
            # additionally clamps to the request-scoped deadline so a
            # 45s critic on a 10s remaining budget fails fast instead of
            # blowing the outer envelope.
            from core.deadline import bounded_wait_for
            raw_and_parsed = await bounded_wait_for(
                asyncio.to_thread(
                    chain.invoke,
                    {
                        "query":       wrap_untrusted(user_query),
                        "intent_json": intent_json,
                        "response":    wrap_untrusted(response),
                        "retrieved_sources_whitelist": (
                            retrieved_sources_whitelist
                            or "(none — the caller passed no source registry; skip the "
                               "`unretrieved_citation` category for this call)"
                        ),
                    },
                ),
                local_timeout=45,
            )
        _record_tokens("SelfRefine", "critique", raw_and_parsed.get("raw"))
        result: Critique = raw_and_parsed["parsed"]
        record_gemini_flash_success()
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
        record_gemini_flash_failure()
        log.warning(
            "Critique LLM call failed; treating as pass to avoid blocking user",
            error=short_err(e),
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
    retrieved_sources_whitelist: str = "",
) -> str:
    """Run a single refinement pass. Returns the revised response.

    On any error: returns the original response so the user gets SOMETHING
    rather than nothing.

    `retrieved_sources_whitelist` is threaded through so the refiner can
    substitute a real retrieved citation for a hallucinated one flagged
    under `unretrieved_citation`, instead of restoring the invention.
    """
    # Circuit breaker: skip the refiner when Gemini Pro is unhealthy.
    # Returning the original response is the existing fallback anyway.
    from core.clients import (
        is_gemini_pro_available, record_gemini_pro_failure,
        record_gemini_pro_success,
    )
    if not is_gemini_pro_available():
        log.warning("Gemini Pro circuit open — skipping refinement",
                    fast_fail=True)
        return response

    try:
        with log_time(log, "Self-refine refinement"):
            llm = refiner_llm or get_gemini_pro(
                temperature=0.3, max_output_tokens=20000, thinking_budget=2048,
            )
            intent_json = intent.model_dump_json(indent=2)
            violations_block = _format_violations(critique.violations)
            prompt = ChatPromptTemplate.from_template(REFINE_PROMPT)
            chain = prompt | llm
            # Bound the refiner too — same reasoning as the critic above,
            # with deadline-aware clamping.
            from core.deadline import bounded_wait_for
            result = await bounded_wait_for(
                asyncio.to_thread(
                    chain.invoke,
                    {
                        "query":           wrap_untrusted(user_query),
                        "intent_json":     intent_json,
                        "violations_block": violations_block,
                        "quality_notes":   critique.overall_quality_notes or "(none)",
                        "response":        response,
                        "retrieved_sources_whitelist": (
                            retrieved_sources_whitelist
                            or "(none — restrict yourself to fixing non-citation "
                               "violations; do not touch existing citations for this call)"
                        ),
                    },
                ),
                local_timeout=60,
            )
        _record_tokens("SelfRefine", "refine", result)
        record_gemini_pro_success()
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
        record_gemini_pro_failure()
        log.warning(
            "Refinement LLM call failed; returning original response",
            error=short_err(e),
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
    source_registry: object = None,
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
    # Phase 5 (citation-grounding pipeline): also force the loop when a
    # populated source_registry is supplied. Even with no explicit intent
    # directives, if the caller retrieved N real sources we want the critic
    # to verify the response actually cites from that pool rather than
    # hallucinating. Skip force-run when registry is empty.
    has_registry = bool(source_registry) and getattr(source_registry, "__len__", lambda: 0)() > 0
    if not (has_directives or has_lang_mismatch or has_registry):
        return response, []

    # Compute the allowed-citation whitelist ONCE, reuse across iterations.
    _whitelist = ""
    if has_registry and hasattr(source_registry, "serialize_for_critic"):
        _whitelist = source_registry.serialize_for_critic()
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
    original_response_len = len(response)  # for cumulative-shrink guard
    for iteration in range(max_iterations + 1):  # +1 for the final critique
        critique = await _critique(
            user_query, critic_intent, current, critic_llm,
            retrieved_sources_whitelist=_whitelist,
        )
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
        refined = await _refine(
            user_query, critic_intent, current, critique, refiner_llm,
            retrieved_sources_whitelist=_whitelist,
        )
        # Destructive-refinement guard #1 (PER-ITERATION): reject a
        # refinement that drops the response length by more than 30%
        # in a single step. Observed 2026-07-30 on a Hindi anticipatory-
        # bail draft — critic flagged 14 violations against a well-formed
        # 7,643-char draft and the refiner rewrote it into 3,233 chars
        # trying to fix all of them at once. Long drafts should get
        # LONGER (or roughly stay the same) after refinement — dropping
        # to under 70% of the input signals a runaway rewrite, not a fix.
        # Guard applies to responses >2K chars (short responses can
        # legitimately shrink after e.g. a table format fix).
        if len(current) > 2000 and len(refined) < len(current) * 0.7:
            log.warning(
                "Refinement destructively shortened response; keeping original",
                original_len=len(current),
                refined_len=len(refined),
                drop_pct=round(100 * (1 - len(refined) / max(len(current), 1)), 1),
                iteration=iteration,
                violation_count=len(critique.violations),
            )
            # Skip the refinement, break the loop — retrying would just
            # feed the shorter draft back to the critic and produce further
            # destructive shortening.
            return current, history
        # Destructive-refinement guard #2 (CUMULATIVE): the per-iteration
        # guard misses the "three small shrinks that add up" case
        # observed 2026-08-02 on a bail-app draft — iter1 shrunk 9484→
        # 7954 (-16%), iter2 shrunk 7954→6141 (-23%), cumulative -35%.
        # Each step under the per-iter threshold but the aggregate is
        # user-visible damage. Revert to the ORIGINAL response (not the
        # partially-shrunk `current`) because both mid-states were also
        # unwanted shrinks.
        if (original_response_len > 2000
                and len(refined) < original_response_len * 0.65):
            log.warning(
                "Refinement cumulative-shrink exceeded budget; reverting to original",
                original_response_len=original_response_len,
                refined_len=len(refined),
                drop_pct=round(100 * (1 - len(refined) / max(original_response_len, 1)), 1),
                iteration=iteration,
            )
            return response, history
        current = refined

    return current, history
