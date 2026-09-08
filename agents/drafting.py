"""Drafting Agent — raw-source pipeline (2026-07-02).

Flow:
  1. Build `user_facts` blob from attachments / pasted context / integrations.
     This is the RAW text of every uploaded document, passed straight
     through to generation. No extraction / bullet-summary middleman.
  2. Acquire a reference draft:
       a. ES `match` on the `drafting` index for candidate file names.
       b. Single LLM picker call — returns the best path OR 'none'.
       c. If 'none' or empty corpus → web fallback (Gemini 2.5 Flash + Google
          Search grounding) synthesises a reference draft using
          `DRAFTING_WEB_FALLBACK_PROMPT`.
  3. Generation: Gemini 2.5 Pro given
     (user_query + reference_draft + UPLOADED SOURCE DOCUMENTS + user_intent).
     Dispatcher picks single-pass or per-section fan-out via `_judge_fanout`.
     Raw source documents are threaded into EVERY section-pair call so any
     section that needs to walk the source paragraph-by-paragraph
     (para-wise reply, rejoinder para-wise denials, counter-affidavit)
     has direct access.
  4. Mechanical cleanup: `validate_draft` strips mojibake / HTML tags /
     leftover `[CITE: ...]` placeholders / empty numbered paragraphs.
  5. `core.self_refine.self_refine` audits the draft against typed UserIntent
     and refines on violations. Includes `canonical_example_substitution`
     category to catch training-set-artefact names (Sneha / Priyanka /
     Nashik / Sangamner / etc.) leaking into a draft whose sources named
     different real parties.
  6. Return `AgentResult` with source attribution (`reference_kind`: 'es' | 'web').

No doc-type classifier, no synthetic skeletons, no doctrinal-stance JSON,
no mandatory-section injection, no footer template, no case-fact bullet
extraction. The reference draft is the structural anchor; the user's query
shapes scope; the uploaded source documents provide para structure and
authoritative facts.

See `docs/drafting_simplification_plan.md` for the full design and rationale.
"""

from __future__ import annotations

from datetime import date

import asyncio
import os
import re
import unicodedata

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from core.state import (
    LegalAgentState, AgentResult, SourceMetadata,
    IntegrationContextData, FileContextData,
)
from core.clients import (
    get_es_client, get_gemini_flash_lite, cacheable_system,
    get_gemini_flash_full, get_gemini_flash_planning, get_drafting_llm,
    # Circuit-breaker helpers referenced from `try/except` blocks in
    # `_pick_relevant_chunk_indices` and `_judge_fanout`. Must be imported
    # at module scope so the `except:` handler can still call
    # `record_gemini_flash_failure` when the preceding `init_chat_model(...)`
    # call raises BEFORE any function-scope import would have executed. See
    # tests/test_drafting_simplification.py::TestJudgeFanout::
    # test_llm_failure_defaults_to_single_pass.
    is_gemini_flash_available, record_gemini_flash_failure,
    record_gemini_flash_success,
)
from core.settings import GEMINI_MODELS, MODELS, ES_INDICES
from core.language import (
    localize_prompt, detect_source_languages, language_name,
    is_off_target_language, output_script_ratio,
)
from core.logger import get_logger, log_time, short_err
from core.progress import progress
from core.self_refine import self_refine

log = get_logger("Drafting")


# Limit concurrent Drafting executions per worker process (avoids Gemini
# rate limits and gives the orchestrator a back-pressure signal).
_AGENT_SEMAPHORE = asyncio.Semaphore(3)


# ---------------------------------------------------------------------------
# Review-and-Redraft short-circuit
#
# When the user uploaded a document AND their query has review/redraft/revise
# verbs, the uploaded document IS the reference draft the user wants preserved.
# The ES picker rejects this case because it looks for a template that matches
# the QUERY, not the attachment; the web fallback then returns a generic
# template unrelated to the actual uploaded doc (observed on smoke: contract-
# analysis / money-recovery-plaint templates returned for a Section 290 BNSS
# plea-bargaining application). Both outcomes discard the format the user
# already showed us — the exact "not specific format" client complaint.
#
# Detection is verb-based (surface signal). The typed UserIntent does not yet
# carry a `document_analysis_mode` field; when it does, this regex should be
# replaced by an intent-driven check. Both conditions must hold — a fresh
# draft without a file uses the picker+web path unchanged; an uploaded file
# WITHOUT review verbs (e.g. "write a rejoinder to this notice") also uses
# the unchanged path so a proper rejoinder template is fetched.
# ---------------------------------------------------------------------------
_REVIEW_REDRAFT_VERBS_RE = re.compile(
    r"\b("
    r"review|redraft|revise|revised|revising|revision|"
    r"correct|corrected|correcting|"
    r"fix|fixing|"
    r"audit|auditing|"
    r"rectif|"          # rectify / rectifying / rectification
    r"amend|amending|amendment|"
    r"error|errors|mistake|mistakes"
    r")\b",
    re.IGNORECASE,
)


def _is_review_and_redraft_of_upload(query: str, user_facts: str) -> bool:
    """True when the user uploaded a document AND asked to review/redraft it.

    Both conditions must hold. Detection is verb-based (see
    `_REVIEW_REDRAFT_VERBS_RE` above for the trigger set and rationale).
    A short user_facts blob (<200 chars) is treated as "no meaningful upload"
    to avoid triggering on placeholder / metadata-only extractions.
    """
    if not user_facts or len(user_facts.strip()) < 200:
        return False
    if not query:
        return False
    return bool(_REVIEW_REDRAFT_VERBS_RE.search(query))


# System-prompt-level override prepended to DRAFTING_SYSTEM_PROMPT and
# DRAFTING_SECTION_PAIR_PROMPT when review_and_redraft_mode=True. Without
# this, both default system prompts authoritatively frame REFERENCE DRAFT
# as "from a DIFFERENT matter — MUST NOT appear in your output" (see
# config/prompts.py DRAFTING_SYSTEM_PROMPT and DRAFTING_SECTION_PAIR_PROMPT
# rule #1), which causes the section-writer LLM to discard the uploaded
# document's party names, court, case number, and statutory citations —
# defaulting to a generic template (observed on smoke: Master Services
# Agreement / Contract-Analysis Memorandum output for a Section 290 BNSS
# plea-bargaining application). Prepending the override at the system
# prompt level flips the framing before the default rules are read.
_REVIEW_AND_REDRAFT_MODE_OVERRIDE = """## MODE OVERRIDE — REVIEW-AND-REDRAFT OF THE USER'S OWN UPLOADED DOCUMENT

The user uploaded a legal document AND explicitly asked you to REVIEW it for legal errors + REDRAFT the corrected version. This is NOT a fresh drafting task. The uploaded document is the FORMAT ANCHOR, the FACT ANCHOR, and the IDENTITY ANCHOR of your output.

The rules below (in the default drafting system prompt) frame REFERENCE DRAFT as "from a DIFFERENT matter — MUST NOT appear in your output". In THIS mode that framing is INVERTED:

  - The REFERENCE DRAFT and the UPLOADED SOURCE DOCUMENTS are the SAME document — the user's own file.
  - PRESERVE every party name, court name, case number, forum, statutory citation, Act name, section number, address, date, and monetary amount from that document VERBATIM in your output.
  - Your job is to CORRECT the substantive legal errors and formatting in that document — nothing more.
  - Common corrections in scope: wrong statute cited for the relief, wrong Act name / wrong section number, misidentified chapter / part, missing procedural block (verification / prayer / cause title), misstated law, missing landmark-precedent citation.
  - OUT OF SCOPE: changing the document type. If the user uploaded a plea-bargaining application, produce a corrected plea-bargaining application. If they uploaded a bail application, produce a corrected bail application. If they uploaded a rejoinder, produce a corrected rejoinder. NEVER convert their document to a Master Services Agreement, a Contract-Analysis Memorandum, an MOU, a lease deed, or any other template.
  - Do NOT replace real values from the uploaded document with `[Placeholder]` / `[Date]` / `[Full Legal Name of Party A]` fields.
  - Do NOT introduce boilerplate WHEREAS / NOW THEREFORE / IN WITNESS WHEREOF blocks unless the uploaded document itself uses them.
  - When adding landmark Supreme Court case-law citations, add them inline where they support the legal argument, NOT as a bibliography appendix at the end.

If any rule in the default drafting system prompt below CONTRADICTS this MODE OVERRIDE, this MODE OVERRIDE wins."""


# ---------------------------------------------------------------------------
# Follow-up modification fast-path (Level 2 of the follow-up simplification —
# docs/followup_pipeline_simplification_plan.md).
#
# When Turn N is a SHORT DIRECTIVE follow-up on a PRIOR DRAFTING TURN, skip
# the ~9-call reference-picker + context-gather + fan-out judge + sectionwise
# generator + self-refine pipeline and route to a single Gemini Pro call that
# modifies the prior draft in place. Latency drops ~90s → ~8s; the modified
# document preserves every party name, date, statute reference from Turn 1
# instead of rebuilding from a fresh ES template.
#
# Detection is verb-based (surface signal) plus a length ceiling. Gated on
# `previous_artifact_kind == "draft"` and a non-trivial `previous_artifact_content`
# so it can NEVER fire on Turn 1 or on threads whose previous turn was not
# drafting. Behind `DRAFTING_FOLLOWUP_FAST_PATH=1` env flag — off by default
# during initial rollout so the first prod week only validates the WRITE side
# of Level 1.
# ---------------------------------------------------------------------------

# Directive verbs that indicate "modify the prior draft" intent. Deliberately
# lenient — a false positive costs ~8s on the fast path (vs ~90s on the slow
# path); a false negative degrades to the slow path (no regression vs today).
_DIRECTIVE_VERBS_RE = re.compile(
    r"\b("
    # Language switches — the `in|into` alternation catches both "in Marathi"
    # and "into Marathi" (Break #3, tests/multilingual_test_2026_08_17/
    # pipeline_investigation.md — the prior `\bin\s+` missed "into marathi"
    # because "into" has no word boundary between "in" and "to").
    r"(in|into)\s+(marathi|hindi|english|tamil|telugu|kannada|malayalam|bengali|"
    r"punjabi|gujarati|urdu|odia|assamese|sanskrit)|"
    # Verb-form directives — the prior list had only "translate to/into"
    # (loose match, would fire on "translate to json"). Adding "convert /
    # render / rewrite / give me" scoped to a language target so common
    # phrasings ("convert above text into marathi", "render this in Hindi",
    # "rewrite in Tamil", "give me this in Bengali") all trip the fast-path
    # WITHOUT firing on non-language conversions ("convert to json").
    r"(translate|convert|render|rewrite)\s+(to|into|in\s+)?\s*(marathi|hindi|"
    r"english|tamil|telugu|kannada|malayalam|bengali|punjabi|gujarati|urdu|"
    r"odia|assamese|sanskrit)|"
    r"give\s+(me|us)\s+(this|it|the\s+response)\s+in\s+(marathi|hindi|english|"
    r"tamil|telugu|kannada|malayalam|bengali|punjabi|gujarati|urdu|odia|"
    r"assamese|sanskrit)|"
    r"in\s+english\s+please|in\s+hindi\s+please|"
    r"मराठीत|हिंदी\s*में|मराठी\s*मध्ये|मराठीमधे|"
    r"marathi\s+madhe|hindi\s+mein|"
    # Length adjustments
    r"shorten|make\s+it\s+shorter|make\s+shorter|"
    r"expand|elaborate|make\s+it\s+longer|make\s+longer|"
    r"more\s+concise|less\s+verbose|"
    r"summari[sz]e\s+it|condense|trim(?:\s+it)?|"
    # Content additions
    r"add\s+(?:a|an|the|another)?\s*(prayer|verification|clause|paragraph|"
    r"section|ground|footer|salutation|cause\s+title)|"
    r"insert\s+(?:a|an)\s*(prayer|clause|paragraph|section|ground)|"
    r"include\s+(?:a|an|the)\s*(prayer|verification|clause|paragraph|"
    r"ground|citation|reference)|"
    # Format changes
    r"as\s+a\s+table|in\s+a\s+table|as\s+bullet\s+points|"
    r"in\s+bullet\s+points|as\s+a\s+numbered\s+list|"
    r"reformat|format\s+as|change\s+the\s+format|"
    # Polish
    r"polish|refine|improve|clean\s+up|make\s+(?:it|this)\s+(?:more\s+)?formal|"
    r"tighten|proofread|"
    # Party / court / forum changes
    r"change\s+the\s+(court|forum|respondent|petitioner|complainant|accused|"
    r"defendant|plaintiff|address|date|amount)|"
    r"correct\s+the\s+(court|forum|party|address|date|amount|section|statute)"
    r")\b",
    re.IGNORECASE,
)

# Query and prior-draft length gates. Chosen so that:
#   - a very long Turn 2 (> 300 chars) is treated as a fresh drafting task,
#     not a directive-follow-up, and takes the full pipeline;
#   - a very short "prior draft" (< 500 chars) is treated as "no meaningful
#     document to modify" — likely an error banner, refusal, or placeholder.
_FOLLOWUP_DIRECTIVE_MAX_QUERY_CHARS = 300
_FOLLOWUP_DIRECTIVE_MIN_PRIOR_CHARS = 500

# Marker the sectionwise generator prepends when at least one section pair
# fails. When the prior draft carries this banner the fast-path refuses to
# fire — modifying a known-degraded document would propagate the degradation
# through the whole follow-up chain.
_DRAFT_INCOMPLETE_BANNER_MARKER = "**Draft incomplete**"

# Marker `agents/memory.py` prepends (Move 2 deterministic rewrite) when it
# inlines the complete prior draft into the query for a directive follow-up
# ("convert the above text into Marathi", "shorten the above draft"). Its
# presence proves the query ALREADY carries the source document, so hunting
# for an additional ES/web reference template is both wasted work (measured
# 24-37s of web synthesis per turn) and actively harmful — the picker
# correctly rejects a translation task, the web fallback then synthesises an
# unrelated template, and that template competes with the real prior draft
# as a structural anchor. Kept as a prefix of the emitted sentence so a
# reword of its tail cannot silently break the match.
_INLINED_PRIOR_DRAFT_MARKER = "PRIOR DRAFT (the text the user wants to modify"


def _fast_path_enabled() -> bool:
    """Env-flag gate. Off by default; ops flips DRAFTING_FOLLOWUP_FAST_PATH=1
    once Level 1's write path has been validated on real prod data.
    """
    return os.getenv("DRAFTING_FOLLOWUP_FAST_PATH", "0") == "1"


def _is_drafting_followup_directive(
    query: str,
    previous_artifact_kind: str,
    previous_artifact_content: str,
    new_upload_this_turn: bool,
) -> bool:
    """True when this turn should take the drafting fast-path.

    Every rejection is intentional — see docstring block above for the
    per-condition rationale.
    """
    # Rejection 1: no prior draft to modify — fast path cannot apply.
    if previous_artifact_kind != "draft":
        return False
    # Rejection 2: prior draft is trivially short (error banner, refusal,
    # placeholder). Modifying it would produce garbage.
    if not previous_artifact_content or len(previous_artifact_content) < _FOLLOWUP_DIRECTIVE_MIN_PRIOR_CHARS:
        return False
    # Rejection 3: prior draft is known-degraded (sectionwise banner). Fast
    # path would propagate the degradation.
    if _DRAFT_INCOMPLETE_BANNER_MARKER in previous_artifact_content[:400]:
        return False
    # Rejection 4: empty query.
    if not query or not query.strip():
        return False
    # Rejection 5: query is too long — treat as a fresh drafting task.
    if len(query) > _FOLLOWUP_DIRECTIVE_MAX_QUERY_CHARS:
        return False
    # Rejection 6: user uploaded a new document THIS turn — likely asking
    # for a different draft based on the new file, not a modification of
    # the prior one.
    if new_upload_this_turn:
        return False
    # Accept condition: query matches a directive verb.
    return bool(_DIRECTIVE_VERBS_RE.search(query))


# ---------------------------------------------------------------------------
# Input sanitisation (used by the ES `match` query in `_acquire_reference_draft`)
# ---------------------------------------------------------------------------

_LUCENE_SPECIAL = re.compile(r'([+\-=&|!(){}\[\]^"~*?:\\/])')


def _sanitize_es_input(text: str, max_length: int = 500) -> str:
    """Sanitize user input before embedding in an ES query."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = text[:max_length]
    text = _LUCENE_SPECIAL.sub(r"\\\1", text)
    return text


# ---------------------------------------------------------------------------
# Mechanical draft cleanup — runs after generation, before self_refine.
# Pure bug fixes only (mojibake / HTML / [CITE:] / empty paragraphs).
# Substantive critique (orphan citation tails, forbidden statute pairings,
# paragraph numbering, prayer-relief mismatch, missing sections, etc.) lives
# in `core/self_refine.self_refine` against the typed UserIntent.
# ---------------------------------------------------------------------------

# Common UTF-8 → cp1252 → UTF-8 round-trip artefacts in Indian legal text.
_MOJIBAKE_REPLACEMENTS = [
    ("â€“", "–"),   # â€" -> en dash
    ("â€”", "—"),   # â€" -> em dash
    ("â€˜", "‘"),   # â€˜ -> left single quote
    ("â€™", "’"),   # â€™ -> right single quote
    ("â€œ", "“"),   # â€œ -> left double quote
    ("â€",  "”"),   # â€  -> right double quote
    ("â€¦", "…"),   # â€¦ -> ellipsis
    ("Â ",  " "),   # Â   -> nbsp
    ("ï¿½", "?"),   # replacement char (no-info fallback)
    (" â ", " – "), # bare â between spaces -> en-dash (surviving fragment
                    # of truncated 3-byte UTF-8 dash E2 80 93/94)
]

_CITE_PLACEHOLDER_RE = re.compile(r"\[CITE:[^\]]*\]", flags=re.IGNORECASE)
_EMPTY_NUMBERED_PARA_RE = re.compile(r"(?m)^\s*\d+\.\s*$\n?")

_HTML_BR_RE = re.compile(r"<br\s*/?>", flags=re.IGNORECASE)
_HTML_HR_RE = re.compile(r"<hr\s*/?>", flags=re.IGNORECASE)
_HTML_ANY_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?/?>")


def validate_draft(
    full_draft: str,
    stance=None,  # kept for signature compatibility; unused
) -> tuple[str, list[str]]:
    """Cheap mechanical repairs on the generated draft.

    Returns (possibly-cleaned draft, list of warning messages). Never raises.

    Repairs:
      - cp1252 / latin-1 mojibake roundtrip + substring fallback
      - HTML tag strip (<br>, <hr> first, then any remaining tags)
      - leftover `[CITE: ...]` placeholder strip
      - empty numbered paragraphs (`N.` with no body)

    Substantive critique (orphan citation tails, forbidden statute pairs,
    paragraph numbering, prayer-relief mismatch, missing sections) is the
    responsibility of `core.self_refine.self_refine`. Do NOT add per-rule
    checks here — extend CRITIQUE_PROMPT instead.
    """
    warnings: list[str] = []
    cleaned = full_draft

    # Mojibake: codec roundtrip first, then substring fallback for mixed
    # cases where the roundtrip aborts (e.g. clean "café" — bytes E9 alone
    # is invalid UTF-8 lead, so the roundtrip raises and leaves the
    # string alone).
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

    # HTML strip — convert <br> / <hr> to markdown equivalents first so
    # newlines survive, then drop any remaining tag scaffolding. Inner
    # text is preserved.
    cleaned = _HTML_BR_RE.sub("\n", cleaned)
    cleaned = _HTML_HR_RE.sub("\n---\n", cleaned)
    cleaned, html_strip_count = _HTML_ANY_TAG_RE.subn("", cleaned)
    if html_strip_count:
        warnings.append(
            f"Stripped {html_strip_count} HTML tag(s) from draft."
        )

    # [CITE: ...] survivors — the generation prompt forbids them, this is
    # the defensive fallback.
    cite_hits = _CITE_PLACEHOLDER_RE.findall(cleaned)
    if cite_hits:
        cleaned = _CITE_PLACEHOLDER_RE.sub("", cleaned)
        warnings.append(
            f"Stripped {len(cite_hits)} leftover [CITE: ...] placeholder(s)."
        )

    # Accidental repeated blocks. Advocates 2026-09-08 reported duplicated
    # content. Reproduced on BOTH models, so it is a pipeline defect, not a
    # model one: each section-pair call receives `prior_text` (the document so
    # far) and DRAFTING_SECTION_PAIR_PROMPT rule 5 tells it to avoid
    # "re-emission of content already pled earlier". That rule is routinely
    # ignored, so the guarantee moves into code (CLAUDE.md invariant 2: prompt
    # rules that do not hold become deterministic checks).
    #
    # CRITICAL EXEMPTION - an affidavit legitimately repeats the cause title.
    # A bail application ends with a supporting affidavit carrying its OWN
    # court name, case number and party block, then "AFFIDAVIT IN SUPPORT OF..."
    # and the deponent verification. Measured on a real draft: all four
    # "duplicate" blocks were exactly that, and stripping them would leave an
    # unfilable affidavit. So a repeat is preserved when an affidavit or
    # verification title appears nearby; anything else is a genuine repeat.
    _AFFIDAVIT_NEAR = re.compile(
        r"(AFFIDAVIT|VERIFICATION|SOLEMNLY AFFIRM|ON SOLEMN AFFIRMATION|DEPONENT)",
        re.IGNORECASE,
    )
    _blocks = re.split(r"(\n\s*\n)", cleaned)
    _seen: dict[str, int] = {}
    _dropped = 0
    _out: list[str] = []
    for _i, _blk in enumerate(_blocks):
        _norm = re.sub(r"\s+", " ", _blk).strip().lower()
        # Only consider substantial prose blocks; short lines (VERSUS, dates,
        # numbering) repeat legitimately all over a filing.
        if len(_norm) < 80 or _blk.strip().startswith("#"):
            _out.append(_blk); continue
        if _norm in _seen:
            _ctx = "".join(_blocks[max(0, _i - 8):_i + 9])
            if _AFFIDAVIT_NEAR.search(_ctx):
                _out.append(_blk)          # legitimate affidavit caption
            else:
                _dropped += 1              # genuine duplicate - drop it
            continue
        _seen[_norm] = _i
        _out.append(_blk)
    if _dropped:
        cleaned = "".join(_out)
        cleaned = re.sub(r"(\n\s*){3,}", "\n\n", cleaned)
        warnings.append(
            f"Removed {_dropped} duplicated block(s) (affidavit captions preserved)."
        )
    # Em / en dashes — the "ChatGPT dash". Flagged by advocates 2026-09-08 as
    # reading machine-written. Indian legal drafting sets off a parenthetical
    # with commas, semicolons or parentheses; an em-dash in a statutory notice
    # marks the document as generated. No prompt rule forbade it and nothing
    # normalised it, so it survived to the client.
    #
    # Mechanical and unambiguous, so it lives here rather than in
    # CRITIQUE_PROMPT (invariant 4). Three shapes, most specific first:
    #   "word — word"  spaced, parenthetical  -> ", "
    #   "word—word"    unspaced, range/compound -> "-"
    #   leading "— "   list/aside marker      -> ""
    # Measured on real drafts: claude-sonnet-5 emitted 1-3 per notice,
    # gemini-3.8-flash 0-2. This fixes both.
    _dash_before = cleaned.count("—") + cleaned.count("–")
    if _dash_before:
        cleaned = re.sub(r"(?m)^\s*[—–]\s+", "", cleaned)
        cleaned = re.sub(r"\s+[—–]\s+", ", ", cleaned)
        cleaned = re.sub(r"(?<=[A-Za-z0-9])[—–](?=[A-Za-z0-9])", "-", cleaned)
        cleaned = cleaned.replace("—", "-").replace("–", "-")
        cleaned = re.sub(r",\s*,", ",", cleaned)
        warnings.append(
            f"Normalised {_dash_before} em/en dash(es) to legal-register punctuation."
        )

    # Empty numbered paragraphs — a bare `N.` line with no body.
    empty_para_hits = _EMPTY_NUMBERED_PARA_RE.findall(cleaned)
    if empty_para_hits:
        cleaned = _EMPTY_NUMBERED_PARA_RE.sub("", cleaned)
        warnings.append(
            f"Removed {len(empty_para_hits)} empty numbered paragraph(s)."
        )

    # Fabricated citation provenance. Mechanical and unambiguous — a `DB ID`
    # is an internal row identifier, a placeholder marker is unresolved work,
    # and a filing cites the reporter or the court rather than a blog. None
    # has a legitimate place in a document that goes before a judge, so this
    # belongs with the other mechanical repairs rather than in CRITIQUE_PROMPT
    # (invariant 4: bug-fixes here, substantive critique there).
    #
    # Measured need: a draft asking for supporting case law produced 7 case
    # citations, 0 of them present in anything the pipeline retrieved, 2
    # carrying invented "DB ID" identifiers and a judgment dated in the
    # future. Whether a given case is REAL still needs the retrieved-source
    # whitelist and is tracked separately — this only removes provenance that
    # was manufactured to make a citation look verified.
    from core.fabricated_provenance import strip_fabricated_provenance
    cleaned, provenance_warnings = strip_fabricated_provenance(cleaned)
    if provenance_warnings:
        warnings.extend(provenance_warnings)
        log.warning("Validator stripped fabricated citation provenance",
                    findings=len(provenance_warnings),
                    sample=provenance_warnings[0][:140])

    if warnings:
        log.warning("Validator surfaced issues",
                    count=len(warnings),
                    sample=warnings[0][:120])

    return cleaned, warnings


# ---------------------------------------------------------------------------
# Stage 1 — Reference draft acquisition
# ---------------------------------------------------------------------------

class _PickerChoice(BaseModel):
    """Structured output for `_pick_reference_source`.

    'none' is a valid `selected_file` value so the picker can reject the
    entire candidate list and trigger the web fallback.
    """
    selected_file: str = Field(
        ...,
        description=(
            "Exact file name from the candidate list, OR the literal string "
            "'none' if no candidate fits the user's drafting query."
        ),
    )
    reasoning: str = Field(
        "",
        description="One short sentence explaining the choice.",
    )


# ---------------------------------------------------------------------------
# Regional-language → English translation for ES corpus lookup.
#
# The `drafting` ES index carries English file names and English template
# text. When the user's query is in a regional Indian language (Hindi /
# Marathi / Gujarati / Kannada / Tamil / Telugu / Bengali / Punjabi / Odia /
# Urdu / Assamese / Sanskrit / Malayalam), the raw ES `match` on the
# regional-script tokens returns 0 candidates → picker gets an empty list →
# fallback to web synthesis → thinner reference draft → much shorter final
# output than the English-equivalent query would produce.
#
# The fix is a single Gemini Flash Lite call that translates the user's
# regional-language query into a compact English drafting request. That
# translation drives BOTH the ES `match` and the picker's LLM reasoning
# (file names are English). Output-language stays the user's original
# choice — the reference draft is a STRUCTURAL anchor only; the
# section-writer produces body text in the user's target language.
#
# Fires only when `user_language != "en"`. Falls back to the original
# query on any translation failure so English-language traffic and
# regional-language traffic on translator errors both keep current
# behaviour.
# ---------------------------------------------------------------------------


async def _translate_query_for_es_match(
    query: str, user_language: str,
) -> str:
    """Translate a regional-language drafting query to English for ES lookup.

    Returns empty string on any failure — caller uses the original query
    in that case. Language codes match `core.language.SUPPORTED_LANGUAGES`.
    """
    if not query or not query.strip():
        return ""
    if not user_language or user_language == "en":
        return ""
    try:
        from core.language import language_name

        llm = get_gemini_flash_lite(temperature=0.0)
        source_lang = language_name(user_language)
        prompt = (
            f"Translate the following legal-drafting query from {source_lang} "
            f"to English. Return ONLY the translation — no preamble, no "
            f"quotes, no explanation. Preserve legal terms of art in their "
            f"standard English equivalents (e.g. 'वकालतनामा' → 'Vakalatnama', "
            f"'अभियुक्त' → 'accused', 'आवेदन' → 'application', 'याचिका' → "
            f"'petition', 'शपथपत्र' → 'affidavit'). Keep the translation "
            f"concise — one to three lines of English.\n\n"
            f"Query ({source_lang}):\n{query}"
        )
        with log_time(log, "Translate query for ES match"):
            response = await asyncio.wait_for(
                asyncio.to_thread(llm.invoke, prompt),
                timeout=10,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "translate_query", response)

        text = (getattr(response, "text", "") or getattr(response, "content", "") or "").strip()
        # Guard against the model echoing the original when it can't
        # translate — a translated string should have some Latin content.
        latin_ratio = sum(1 for c in text if c.isascii() and c.isalpha()) / max(len(text), 1)
        if not text or latin_ratio < 0.3:
            log.warning(
                "Translator returned insufficient Latin content; ignoring",
                user_language=user_language, latin_ratio=round(latin_ratio, 2),
                text_preview=text[:100],
            )
            return ""
        log.info(
            "Translated query for ES match",
            user_language=user_language,
            original=query[:80], translated=text[:80],
        )
        return text
    except Exception as e:
        log.warning(
            "Query translation failed; caller will use original",
            user_language=user_language,
            error=short_err(e),
        )
        return ""


async def _pick_reference_source(
    query: str, file_paths: list[str],
) -> str | None:
    """Single Gemini Flash Lite call. Picks the best-fitting file name from
    the candidate list, or returns None when no candidate fits (caller falls
    back to web search).

    File paths alone (no previews) — the 2026-06-28 index census confirmed
    corpus filenames are richly descriptive (avg 66.9 chars, 0.8% opaque).

    Falls back to None on any error so traffic never breaks — the worst
    case is a web fallback fire instead of a silent bad pick.
    """
    if not file_paths:
        return None
    try:
        from config.prompts import DRAFTING_PICKER_PROMPT

        llm = get_gemini_flash_lite(temperature=0.0).with_structured_output(_PickerChoice, include_raw=True)

        candidates_block = "\n".join(f"- {p}" for p in file_paths)
        prompt = ChatPromptTemplate.from_template(DRAFTING_PICKER_PROMPT)
        chain = prompt | llm

        with log_time(log, "Reference picker"):
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({"query": query, "file_paths": candidates_block}),
                timeout=15,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "pick_reference", raw_and_parsed.get("raw"))

        choice = raw_and_parsed["parsed"]
        picked = (choice.selected_file or "").strip()
        reasoning = (choice.reasoning or "")[:160]

        if picked.lower() == "none" or not picked:
            log.info("Picker returned 'none'", reasoning=reasoning)
            return None

        # Exact match preferred
        if picked in file_paths:
            log.info("Picker chose", picked=picked, reasoning=reasoning)
            return picked

        # Fuzzy match — LLM sometimes trims quotes / massages whitespace
        for p in file_paths:
            if p.lower() == picked.lower() or \
               os.path.basename(p).lower() == os.path.basename(picked).lower():
                log.info("Picker matched fuzzy",
                         picked=picked, matched=p, reasoning=reasoning)
                return p

        log.warning("Picker returned a path not in candidate list",
                    picked=picked, reasoning=reasoning)
        return None
    except Exception as e:
        log.warning(
            "Reference picker failed; treating as no reference",
            error=short_err(e),
        )
        return None


async def _acquire_reference_draft(
    query: str,
    progress_emit,
    *,
    user_language: str = "en",
    intent=None,
    original_query: str = "",
) -> tuple[str, str, str, str]:
    """Stage 1 of the simplified drafting pipeline (v1 DraftRetriever pattern).

    Flow:
      1. ES `match` on `page_content` (size=100) → distinct `source` file paths
      2. `_pick_reference_source` returns the best path OR None
      3. If picked: ES term query on `source.keyword` → fetch full page_content
      4. If None / empty corpus / fetch miss: synthesize via web_search_fallback
         using DRAFTING_WEB_FALLBACK_PROMPT (instructs the web model to emit
         a draft, not an essay — v1's gap)

    Returns:
      (reference_text, source_attribution, source_kind, search_query)
        source_kind is 'es' (from drafting corpus) or 'web' (synthesized).
        search_query is the ENGLISH form of the user's request — the
        translation we already computed for the corpus lookup, or the
        original query when the user wrote in English. Returned so the
        fan-out judge can plan against English instead of regional script;
        see the depth-collapse note on `_judge_fanout`.
    """
    es = get_es_client()
    index = ES_INDICES["drafting"]

    # Regional-language queries need to be translated to English for the ES
    # match — the drafting corpus is English-only, and raw regional-script
    # tokens don't match English template names. Falls back to the original
    # query on any translation failure. English queries skip translation.
    search_query = query
    if user_language and user_language != "en":
        translated = await _translate_query_for_es_match(query, user_language)
        if translated:
            search_query = translated

    sanitized = _sanitize_es_input(search_query)
    bm25_body = {
        "size": 100,
        "query": {"match": {"page_content": sanitized}},
        "_source": ["source"],
    }

    try:
        with log_time(log, "ES match for reference candidates"):
            response = await asyncio.to_thread(es.search, index=index, body=bm25_body)
        hits = response["hits"]["hits"]
    except Exception as e:
        log.warning("ES candidate search failed; going to web",
                    error=short_err(e))
        hits = []

    # Distinct file paths preserving order (best-scoring first)
    seen: set[str] = set()
    file_paths: list[str] = []
    for hit in hits:
        src = hit.get("_source", {}).get("source", "")
        if src and src not in seen:
            seen.add(src)
            file_paths.append(src)

    progress_emit(
        "drafting",
        f"Found {len(file_paths)} candidate templates",
        substep=True, step="reference",
        found=len(file_paths),
    )

    if not file_paths:
        log.info("No corpus candidates; synthesizing reference via web")
        progress_emit(
            "drafting",
            "No matching template in corpus, searching the web...",
            substep=True, step="reference",
        )
        web_text = await _acquire_reference_via_web(
            query, user_language=user_language, intent=intent,
            original_query=original_query,
        )
        return web_text, "<web:no-corpus-hit>", "web", search_query

    # Use the (possibly translated) English `search_query` for the picker so
    # the LLM reasons about English file names against English intent —
    # regional-script tokens against English paths produced picker-rejections
    # even when a good template existed in the corpus.
    picked = await _pick_reference_source(search_query, file_paths)

    if picked is None:
        log.info("Picker rejected all candidates; synthesizing via web")
        progress_emit(
            "drafting",
            "No close template in corpus, searching the web...",
            substep=True, step="reference",
        )
        web_text = await _acquire_reference_via_web(
            query, user_language=user_language, intent=intent,
            original_query=original_query,
        )
        return web_text, "<web:picker-rejected>", "web", search_query

    fetch_body = {
        "size": 1,
        "query": {"term": {"source.keyword": picked}},
        "_source": ["source", "page_content"],
    }
    try:
        with log_time(log, "ES fetch picked template"):
            fetch_resp = await asyncio.to_thread(
                es.search, index=index, body=fetch_body,
            )
        fetch_hits = fetch_resp["hits"]["hits"]
    except Exception as e:
        log.warning("ES fetch for picked template failed; going to web",
                    picked=picked, error=short_err(e))
        fetch_hits = []

    if not fetch_hits:
        log.warning("Picker chose path but ES returned 0 hits; going to web",
                    picked=picked)
        progress_emit(
            "drafting",
            "Selected template missing in corpus, searching the web...",
            substep=True, step="reference",
        )
        web_text = await _acquire_reference_via_web(
            query, user_language=user_language, intent=intent,
            original_query=original_query,
        )
        return web_text, "<web:fetch-failed>", "web", search_query

    reference_text = fetch_hits[0]["_source"].get("page_content", "") or ""
    display_name = os.path.basename(picked)
    progress_emit(
        "drafting",
        f"Using template: {display_name[:60]}",
        substep=True, step="reference",
    )
    log.info("Reference draft acquired from corpus",
             source=picked, length=len(reference_text))
    return reference_text, picked, "es", search_query


async def _acquire_reference_via_web(
    query: str,
    *,
    user_language: str = "en",
    intent=None,
    original_query: str = "",
) -> str:
    """Synthesize a reference draft from the open web.

    Uses `core.agent_fallback.web_search_fallback` (Gemini 2.5 Flash + Google
    Search grounding) with `DRAFTING_WEB_FALLBACK_PROMPT`, which instructs the
    model to produce a SAMPLE DRAFT rather than an essay. This fixes v1's gap:
    v1's fallback (Scenario_qa) returned a legal-QA answer for niche formats,
    not a usable reference draft.
    """
    from core.agent_fallback import web_search_fallback
    from config.prompts import DRAFTING_WEB_FALLBACK_PROMPT

    try:
        result = await web_search_fallback(
            query=query,
            agent_name="Drafting",
            system_prompt=DRAFTING_WEB_FALLBACK_PROMPT,
            original_query=original_query,
            user_language=user_language,
            intent=intent,
        )
        text = (getattr(result, "content", "") or "").strip()
        if not text:
            log.warning("Web fallback returned empty content; no reference available")
        return text
    except Exception as e:
        log.warning(
            "Web fallback synthesis failed; returning empty reference",
            error=short_err(e),
        )
        return ""


# ---------------------------------------------------------------------------
# Stage 1.5 — Gather relevant legal context (Newacts / Legislation /
# Judgments / SCI). Runs the same ES retrievers the domain agents use, in
# parallel, and packages the top hits into a labelled context bundle the
# drafting LLM can anchor on. Replaces the "drafting reads a single
# template" approach with "drafting reads template + relevant statutes +
# relevant precedents." Heavy on context, light on prompt engineering —
# Gemini 2.5 Pro has a 1M-token window; we use a few thousand tokens of
# real grounding instead of begging the model not to hallucinate.
# ---------------------------------------------------------------------------

# Drafting instructions are imperative and long ("Draft a detailed regular
# bail application for my client charged under Sections … Include relevant
# Supreme Court case law with citations supporting the grounds for bail").
# The instruction words carry no retrieval signal and push the clause count
# past Elasticsearch's 1024 cap. Keep the legal content, drop the command.
_DRAFTING_STOPWORDS = frozenset("""
draft drafting prepare create write generate compose make give me my client
please kindly detailed detail include including relevant with supporting
supporting grounds application for the a an and or of in on under as per
that this these those is are was were be been being to from into it its
""".split())

_SCI_QUERY_MAX_WORDS = 14


def _topical_query(query: str) -> str:
    """Shorten a drafting instruction into a topical query for SCI search.

    Measured: 41 words -> "maxClauseCount is set to 1024" error; 8-10 words ->
    hits or a clean empty. Keeps ordering so the phrase still reads naturally
    to a semantic search, and falls back to a plain truncation when filtering
    removes too much.
    """
    if not query:
        return query
    words = re.findall(r"[\w()\-/.]+", query)
    kept = [w for w in words if w.lower() not in _DRAFTING_STOPWORDS]
    if len(kept) < 4:
        kept = words
    return " ".join(kept[:_SCI_QUERY_MAX_WORDS])


def _parse_statute_refs(query: str) -> tuple[list[str] | None, str | None]:
    """(section_numbers, act_name) parsed out of a drafting query.

    Delegates to the Newacts agent's own parser so the two paths cannot
    drift apart — it already handles "Sections 316(2) and 318(4) of the
    Bharatiya Nyaya Sanhita, 2023" and the abbreviation map (IPC, BNS,
    CrPC, BNSS, IEA, BSA).

    Returns (None, None) on any failure. Retrieval then behaves exactly as
    it did before: a plain BM25 query. Degraded, never broken.
    """
    if not query:
        return None, None
    try:
        from agents.newacts import _regex_fallback_metadata
        md = _regex_fallback_metadata(query)
        secs = getattr(md, "section_number", None) or None
        act = getattr(md, "act_name", None) or None
        return (list(secs) if secs else None), act
    except Exception as e:
        log.warning("Statute-ref parse failed; falling back to plain query",
                    error=short_err(e))
        return None, None


class _ContextBlocks(dict):
    """Prompt blocks, carrying the SourceRegistry built from the same hits.

    A dict subclass rather than a tuple return so every existing caller and
    test that treats this as a plain `dict[str, str]` keeps working; consumers
    that want the registry read `getattr(ctx, "registry", None)`.
    """
    registry = None


def _registry_from_hits(newacts_t, legis_t, judg_t):
    """Build a SourceRegistry from the raw ES hits behind the context blocks.

    Only statutes and High Court judgments are lifted. The SCI tool returns a
    pre-formatted string rather than structured hits, so there is nothing
    reliable to key on — better an incomplete whitelist than one padded with
    guessed citations, since the critic treats the whitelist as the set of
    things the draft is ALLOWED to cite.
    """
    try:
        from core.source_registry import RetrievedSource, SourceRegistry
    except Exception:
        return None
    registry = SourceRegistry()

    def _act_from_source(src: str) -> str:
        """Recover an Act name from the corpus filename.

        These indices carry no act_name field — the Act is only in the path,
        e.g. ".../The Bharatiya Nyaya Sanhita,2023.csv". A whitelist of raw
        paths would be worse than no whitelist: the critic treats it as the
        set of citations the draft is ALLOWED to use, so every properly
        formatted citation would be flagged as unretrieved.
        """
        if not src:
            return ""
        name = os.path.basename(src).rsplit(".", 1)[0]
        name = re.sub(r"^\d{6,}_", "", name)          # judgment date prefixes
        name = re.sub(r"_\d+$", "", name)             # trailing doc ids
        name = re.sub(r",(?=\d)", ", ", name)         # "Sanhita,2023" -> "Sanhita, 2023"
        return name.replace("_", " ").strip()

    def _lift_statute(result, source_type: str, agent: str, prefix: str) -> None:
        """Statutes enter the whitelist at ACT level, never section level.

        The `section_number` on a hit is whichever CHUNK matched — in practice
        "Section 1" or "Section 10" — not the section the draft is about. A
        whitelist naming those exact sections would be actively harmful: the
        critic flags any "quoted statutory provision" not present, so a draft
        correctly citing Section 316(2) BNS (the section the USER named) would
        be reported as a hallucination, and the refiner would then strip or
        replace the user's own statute. That is precisely the doc-type-flip
        failure — a wrong signal the refiner faithfully acts on.

        Act-level is the honest claim: we retrieved this Act, not this
        section. It preserves the valuable half of the check — a fabricated
        CASE has no entry at all — without inventing precision we do not have.
        """
        for h in (result or {}).get("hits", [])[:8]:
            act = _act_from_source(h.get("source") or "")
            if not act:
                continue
            registry.add(RetrievedSource(
                id=f"{prefix}-{abs(hash(act)) % 1000000}",
                agent=agent, type=source_type,
                canonical_citation=act, title=act,
                snippet=(h.get("content") or "")[:200],
            ))

    def _lift_judgments(result) -> None:
        """Judgment hits carry parties and court as top-level fields."""
        for h in (result or {}).get("hits", [])[:8]:
            pet = h.get("petitioner_names") or ""
            res = h.get("respondent_names") or ""
            if isinstance(pet, list):
                pet = ", ".join(str(x) for x in pet)
            if isinstance(res, list):
                res = ", ".join(str(x) for x in res)
            court = h.get("court_name") or ""
            if pet and res:
                citation = f"{pet} v. {res}"
            else:
                citation = _act_from_source(h.get("source") or "")
            if not citation:
                continue
            if court:
                citation = f"{citation} ({court})"
            registry.add(RetrievedSource(
                id=f"hc-{abs(hash(citation)) % 1000000}",
                agent="Judgment", type="judgment",
                canonical_citation=citation, title=citation,
                snippet=(h.get("content") or "")[:200],
            ))

    try:
        _lift_statute(newacts_t, "newacts", "Newacts", "na")
        _lift_statute(legis_t, "legislation", "Legislation", "leg")
        _lift_judgments(judg_t)
    except Exception as e:
        log.warning("Source registry build failed; critic will skip "
                    "unretrieved_citation for this request", error=short_err(e))
        return None
    return registry


async def _gather_relevant_context(query: str) -> dict[str, str]:
    """Run the domain retrievers in parallel; return labelled blocks.

    Each block is a string ready to drop into the generation prompt.
    Returns {} when ES is down — generation still proceeds (degraded).
    """
    def _safe_invoke(tool, kwargs):
        try:
            return tool.invoke(kwargs)
        except Exception as e:
            log.warning("Context retriever failed",
                        tool=getattr(tool, "name", "?"),
                        error=str(e)[:200])
            return {"hits": [], "total": 0}

    async def _run(tool, kwargs):
        return await asyncio.to_thread(_safe_invoke, tool, kwargs)

    from tools.shared.elasticsearch_tools import (
        search_newacts, search_legislation, search_judgments,
    )
    from tools.shared import sci_judgment_tools

    # Statute retrieval gets the SECTION and ACT parsed out of the query,
    # not just the query text.
    #
    # `search_newacts` supports exact `section_numbers` / `act_name` filters
    # and drafting was passing neither. Every row in the index begins
    # "Section Number: Section N of The <Act> Section Name: ...", so a plain
    # BM25 match on a query like "...under Sections 316(2) and 318(4) of the
    # Bharatiya Nyaya Sanhita, 2023..." scores the SHORTEST rows highest —
    # the short-title clauses. Measured: the query-only call returns
    # sections [1, 1, 1, 1, 1, 1, 10, 10] and never the section asked for,
    # while the same call with section_numbers=["309"] returns exactly the
    # right row.
    #
    # That is why the citation registry was full of "Section 1, BNS" and
    # "Section 10, IPC" — treated at the time as chunk-level noise and
    # worked around by dropping statutes to Act level. It was this bug.
    #
    # The Newacts agent already parses both correctly, so reuse its parser
    # rather than writing a second one that can drift out of step.
    _sec_nums, _act_name = _parse_statute_refs(query)
    _newacts_args: dict = {"query": query}
    if _sec_nums:
        _newacts_args["section_numbers"] = _sec_nums
    if _act_name:
        _newacts_args["act_name"] = _act_name
    if _sec_nums or _act_name:
        log.info("Statute retrieval filters parsed from query",
                 sections=_sec_nums, act=_act_name)

    # Run all retrievers in parallel.
    newacts_t, legis_t, judg_t, sci_t = await asyncio.gather(
        _run(search_newacts, _newacts_args),
        _run(search_legislation, {"query": query}),
        _run(search_judgments, {"query": query}),
        # SCI gets a SHORTENED query. Its semantic search builds one boolean
        # clause per term and Elasticsearch caps that at 1024, so a full
        # drafting instruction (41 words in the measured case) fails with
        # "maxClauseCount is set to 1024" while an 8-word topical query
        # succeeds. Drafting was passing the whole instruction, so SCI failed
        # on precisely the requests that ask for case law.
        _run(sci_judgment_tools.search_by_topic,
             {"query": _topical_query(query), "top_k": 3}),
        return_exceptions=False,
    )

    def _format_es_hits(result, label: str, max_hits: int = 3,
                       max_chars_per_hit: int = 800) -> str:
        """Format ES-tool hits as a compact bullet block."""
        hits = (result or {}).get("hits", [])[:max_hits]
        if not hits:
            return ""
        chunks = []
        for h in hits:
            src = h.get("source") or h.get("metadata", {}).get("source") or "(unknown)"
            content = h.get("page_content") or h.get("content") or ""
            if not content:
                continue
            chunks.append(
                f"- **Source**: `{src}`\n  {content[:max_chars_per_hit].strip()}"
            )
        if not chunks:
            return ""
        body = "\n\n".join(chunks)
        return f"## {label}\n{body}\n"

    def _format_sci(result, max_chars: int = 2400) -> str:
        """Format the SCI tool's output, or nothing when it did not succeed.

        The tool has FOUR observed modes, not two:

            HITS          "Found 3 relevant judgment(s):\\n\\n**PARTIES** (DB ID: n)…"
            EMPTY         "No matching judgments found."
            ERROR-STRING  "Semantic search error: TransportError(500, …)"
            (exception)   raised, handled by _safe_invoke

        Only EMPTY was filtered. An error string does not start with "No ", so
        it was injected into the writer's prompt underneath a heading reading
        "## RELEVANT SUPREME COURT JUDGMENTS" — the writer saw a promise of
        Supreme Court authority followed by a stack trace, had none, was asked
        for case law, and supplied it from memory in the tool's own
        "(DB ID: n)" format.

        That is the mechanism behind the ungrounded citations: retrieval
        failed and the failure was formatted as content.
        """
        if not result:
            return ""
        text = result if isinstance(result, str) else str(result)
        text = text.strip()
        if not text or text.startswith("No "):
            return ""
        if not text.startswith("Found "):
            # Anything that is not a hit block is a failure, however it is
            # worded. Fail closed: no block beats a block that lies.
            log.warning("SCI search did not return hits; omitting the block",
                        preview=text[:160])
            return ""
        return f"## RELEVANT SUPREME COURT JUDGMENTS\n{text[:max_chars]}\n"

    # Build a SourceRegistry from the SAME hits, before they are flattened
    # into prompt strings.
    #
    # Why this exists: self_refine's `unretrieved_citation` category — the
    # rule that catches fabricated case citations — is skipped whenever the
    # caller passes no source_registry. Drafting never passed one, so that
    # check has never run on a single drafting request. For a legal drafting
    # product a hallucinated authority in a filed document is the most costly
    # defect the system can produce, and it was the one category guaranteed
    # not to fire.
    #
    # The records were always here; `_gather_relevant_context` retrieved them
    # and threw the structure away, keeping only the formatted text. Same
    # shape as the discarded English translation: the data existed, nothing
    # carried it to the stage that needed it.
    blocks = _ContextBlocks()
    n = _format_es_hits(newacts_t, "RELEVANT BNS / BNSS / BSA SECTIONS")
    if n: blocks["newacts"] = n
    l = _format_es_hits(legis_t, "RELEVANT LEGISLATION SECTIONS")
    if l: blocks["legislation"] = l
    j = _format_es_hits(judg_t, "RELEVANT HIGH COURT JUDGMENTS")
    if j: blocks["judgments"] = j
    s = _format_sci(sci_t)
    if s: blocks["sci"] = s
    blocks.registry = _registry_from_hits(newacts_t, legis_t, judg_t)
    log.info("Drafting source registry built",
             records=len(blocks.registry) if blocks.registry else 0)

    log.info("Context gather completed",
             blocks=list(blocks.keys()),
             total_chars=sum(len(v) for v in blocks.values()))
    return blocks


# ---------------------------------------------------------------------------
# Stage 2 — Generation.
#
# Public entry: `_generate_draft(...) -> str`. Thin DISPATCHER — asks
# `_judge_fanout` whether the document benefits from section-by-section
# generation, then runs one of:
#
#   - `_generate_single_pass` — one Gemini 2.5 Pro call for the whole
#     document. Used for short letters, notices, single-page applications,
#     simple transactional instruments.
#
#   - `_generate_sectionwise` — sequential per-section loop, two sections
#     per Pro call, each call seeing the document drafted so far. Used for
#     long multi-section instruments (writs, plaints, written statements,
#     detailed bail applications).
#
# Multilingual: both paths flow `user_language` through `localize_prompt`,
# so Hindi, Marathi, Gujarati, Kannada, Tamil, Telugu, Malayalam, Bengali,
# Punjabi, Urdu, Odia, Assamese, Sanskrit drafts inherit the same script /
# numeral / ceremonial-block directives. The judge call ALSO emits each
# section's heading in the user's target language and script, so the
# section pair generator gets a localized heading directly.
#
# The public `_generate_draft` signature stays `-> str` so callers (i.e.
# `drafting_node`) don't change.
# ---------------------------------------------------------------------------

async def _generate_single_pass(
    query: str,
    user_facts: str,
    reference_draft: str,
    user_intent,
    user_language: str,
    progress_emit,
    gathered_context: dict[str, str] | None = None,
    review_and_redraft_mode: bool = False,
    niche_overlay: str = "",
) -> str:
    """Produce the full document in one Gemini 2.5 Pro call.

    Inputs: (system_prompt + user_query + UPLOADED SOURCE DOCUMENTS +
    reference_draft + gathered legal context). The LLM decides headings,
    sections, length, footer, signature block based on the reference and
    the user's ask.

    `user_facts` is the RAW extracted text of uploaded documents, passed
    verbatim — no bullet-summary middleman. Gemini 2.5 Pro's 2M-token
    window can consume multi-100K-char PDFs; a raw source is required for
    tasks like rejoinder / para-wise reply where the model must walk the
    source document paragraph-by-paragraph.

    `niche_overlay` is the resolved ``## NICHE OVERLAY`` block from
    ``config.drafting_niches``. When non-empty, it's appended to the
    system prompt AFTER localisation so overlay heading anchors and
    statutory citations survive the localiser (which does not translate
    English legal terms of art). Empty string is a no-op — the base
    prompt handles the matter on its own.
    """
    from config.prompts import DRAFTING_SYSTEM_PROMPT
    from langchain_core.messages import SystemMessage, HumanMessage

    system_prompt = localize_prompt(DRAFTING_SYSTEM_PROMPT, user_language, user_intent)
    if niche_overlay:
        system_prompt = system_prompt + "\n\n" + niche_overlay
    if review_and_redraft_mode:
        # The default system prompt frames REFERENCE DRAFT as "from a
        # different matter — MUST NOT appear in your output". In
        # review-and-redraft mode the reference IS the uploaded document,
        # so that default framing produces a generic template (Master
        # Services Agreement / Contract-Analysis Memorandum) instead of a
        # corrected version of the uploaded document. Prepend a MODE
        # OVERRIDE that flips the framing at the system-prompt level.
        system_prompt = _REVIEW_AND_REDRAFT_MODE_OVERRIDE + "\n\n---\n\n" + system_prompt

    # In review-and-redraft mode, `reference_draft` IS `user_facts` (the
    # caller sets `reference_text = user_facts` in drafting_node when
    # `_is_review_and_redraft_of_upload` fires). Emitting BOTH blocks
    # duplicates the same content and blows the 1M-token ceiling on
    # multi-MB uploads. Live 2026-09-03: 1.9M-char arbitration paperbook
    # upload -> pair_user_facts + reference_draft = ~3.8M chars ≈ 1M+
    # tokens → 400 INVALID_ARGUMENT on both Pro and Flash. The reference
    # block's flipped wording ("PRESERVE these values verbatim") already
    # tells the model to treat the block as authoritative source, so
    # skipping source_docs_block here loses zero information.
    source_docs_block = ""
    if user_facts and user_facts.strip() and not review_and_redraft_mode:
        source_docs_block = (
            "## UPLOADED SOURCE DOCUMENTS (verbatim raw text — every "
            "party name, date, address, amount, statutory reference, and "
            "paragraph-level assertion in your output MUST be sourced "
            "from this block or the USER QUERY below. Do NOT compress, "
            "summarise, or skip content. When a section requires walking "
            "the source paragraph-by-paragraph — a rejoinder, para-wise "
            "reply, counter-affidavit, or written statement — use the "
            "paragraph structure and numbering from this block directly.)\n"
            f"{user_facts.strip()}\n\n"
        )

    if review_and_redraft_mode:
        # The reference draft IS the uploaded document. The user is asking
        # us to REVIEW-AND-REDRAFT their own document — every party name,
        # court name, case number, statutory citation, and case-specific
        # detail must be PRESERVED VERBATIM. Only the substantive legal
        # errors and formatting are to be corrected. This block is the
        # ONLY carrier of the source content in review-and-redraft mode
        # (source_docs_block is intentionally skipped above to avoid
        # doubling the payload).
        reference_block = (
            "## REFERENCE DRAFT / UPLOADED SOURCE DOCUMENT (this is the "
            "SAME document the user uploaded — the user is performing a "
            "REVIEW-AND-REDRAFT of THEIR OWN document. PRESERVE every "
            "party name, court name, case number, forum, statutory "
            "citation, address, date, monetary amount, and case-specific "
            "detail in this block VERBATIM. Every fact, paragraph number, "
            "and stated assertion in your output MUST be sourced from "
            "this block or the USER QUERY. Correct ONLY the substantive "
            "legal errors (wrong statute, wrong Act name, missing "
            "procedural section, misstated law, missing verification / "
            "prayer conventions) and the formatting. Do NOT rewrite this "
            "as a generic template. Do NOT replace real values with "
            "`[placeholders]`. Do NOT introduce a contract-analysis / "
            "memorandum / MOU / lease-deed shape unless the uploaded "
            "document itself is one of those.)\n"
            f"{reference_draft.strip()}\n\n"
        )
    else:
        reference_block = (
            "## REFERENCE DRAFT (STRUCTURE-ONLY example from a different matter — "
            "IGNORE every name, date, address, amount, party detail, and case-"
            "specific value in this block. They belong to a different person's "
            "matter and MUST NOT appear in your output. Use ONLY the reference's "
            "shape: section ordering, headings, salutations, conventions, "
            "phrasing patterns, and statutory-citation style.)\n"
            f"{reference_draft.strip() if reference_draft else '(no reference draft available — produce the document from the user query and uploaded source documents alone, following Indian-law conventions for the document type)'}\n\n"
        )

    # Gathered context — relevant statutes / judgments retrieved by the
    # domain agents' own ES tools. Injected BEFORE the reference / source
    # blocks because it's legal-grounding background; the user-specific
    # blocks sit closer to the closing instruction (recency).
    context_block = ""
    if gathered_context:
        parts = []
        for key in ("newacts", "legislation", "judgments", "sci"):
            v = gathered_context.get(key)
            if v:
                parts.append(v)
        if parts:
            context_block = (
                "## RELEVANT LEGAL CONTEXT (statutes and precedents the "
                "drafting system retrieved for this matter — use these for "
                "INLINE STATUTORY CITATIONS and LEGAL REASONING; do NOT "
                "wholesale copy their party names or case facts into the "
                "draft)\n"
                + "\n".join(parts)
                + "\n"
            )

    # TODAY'S DATE - required by the MISSING-FACT POLICY rule, which tells the
    # model "today's date and the drafting place are legitimate defaults if the
    # source is silent". Until 2026-09-07 the drafting prompt never said what
    # today WAS, so a model following that rule had to guess. Measured:
    # claude-sonnet-5 emitted "Date: 05 August 2026" on a notice drafted
    # 07 September 2026 - neither today nor a supplied date - while
    # gemini-3.8-flash sidestepped the rule with [TO_FILL:]. Neither is right on
    # a statutory notice where limitation runs from the notice date. Every other
    # agent already passes "Current Date" (document, legislation, judgment,
    # newacts, constitution_maxim); drafting was the outlier.
    #
    # Deliberately in the USER block, not the system prompt: the system prompt
    # carries cache_control for Anthropic, and a date that changes daily inside
    # the cached prefix would invalidate the cache every day.
    _today_block = (
        f"## TODAY'S DATE\n{date.today().strftime('%d %B %Y')}\n"
        "Use this when the document needs a notice / verification / drafting "
        "date and the source is silent. Never infer such a date from other "
        "dates in the matter.\n\n"
    )
    user_block = (
        f"{_today_block}"
        f"{context_block}"
        f"{reference_block}"
        f"{source_docs_block}"
        "## USER QUERY (the document-type request — what to draft)\n"
        f"{query.strip()}\n\n"
        "Produce the complete document the user asked for, in standard "
        "Indian-law conventions for the document type the user named. "
        "EVERY party name, date, address, monetary amount, statutory "
        "reference, and case-specific detail MUST come VERBATIM from the "
        "UPLOADED SOURCE DOCUMENTS block or the USER QUERY above — those "
        "are the only sources of fact for this matter. Cite inline statutes "
        "and (where applicable) precedents from the RELEVANT LEGAL CONTEXT "
        "above. Do NOT invent, substitute, paraphrase, or carry over "
        "canonical-sounding Indian-law example values from your training "
        "data (e.g. 'Priyanka', 'Sneha', 'Bhausaheb', 'Sakore', 'Anjali "
        "Deshmukh', 'Nashik', 'Sangamner', 'Ahmednagar', '29 May 2022', "
        "'1 June 2020') — using any of those when the source names "
        "different real parties is a CRITICAL error. If a value is "
        "genuinely absent from both case sources above, use a clearly-"
        "bracketed placeholder (e.g. [Advocate's Address], [Reference "
        "Number]). NEVER copy a party name / fact value from the REFERENCE "
        "DRAFT — its values belong to a different matter. Output ONLY the "
        "document itself — no preamble, no postscript, no meta-commentary."
    )

    # Temperature 0 + larger thinking budget. The previous default
    # (temperature 0.4) was producing creative deviations from the CASE
    # FACTS — Gemini Pro was substituting cliché Indian-law example
    # values (Sneha / Priyanka / Nashik / 29 May 2022) for the user's
    # actual party names and dates. Drafting is a fact-transcription task,
    # not a creative one — zero temperature forces strict instruction
    # following; the larger thinking budget gives the model headroom to
    # cross-reference each emitted entity back to the CASE FACTS block.
    llm = get_drafting_llm(
        max_output_tokens=24000,
        thinking_budget=4096,
    )

    progress_emit("drafting", "Generating your draft...", step="generate")

    # Gemini occasionally trips the RECITATION safety filter on redraft
    # prompts (it thinks the model is reciting the source document too
    # directly). When that happens, `response.content` is "" and
    # `response_metadata.finish_reason` is "RECITATION". On a single retry,
    # we append an instruction asking the model to substantially paraphrase
    # — that usually clears the filter. If the retry also comes back empty,
    # we surface a clean error instead of leaking the LangChain AIMessage
    # repr (which used to ship as the final response, looking like
    # "content='' additional_kwargs={} response_metadata={...}").
    async def _invoke_once(extra_instruction: str = "") -> "object":
        final_user_block = (
            user_block + "\n\n" + extra_instruction if extra_instruction
            else user_block
        )
        return await asyncio.to_thread(
            llm.invoke,
            [SystemMessage(content=cacheable_system(system_prompt)),
             HumanMessage(content=final_user_block)],
        )

    from core.token_tracker import record as _record_tokens

    def _finish_reason(r) -> str:
        meta = getattr(r, "response_metadata", None) or {}
        return str(meta.get("finish_reason") or "").upper()

    try:
        with log_time(log, "Single-pass draft generation"):
            response = await _invoke_once()
    except Exception as e:
        log.error("Draft generation LLM call failed",
                  error=str(e)[:200], exc_info=True)
        raise

    _record_tokens("Drafting", "generate", response)
    # `.text` flattens Gemini 3.x list-of-content-blocks into a string and
    # returns the plain `.content` for Gemini 2.5 unchanged. See LangChain
    # docs on "Gemini 3 series models return a list of content blocks".
    text = getattr(response, "text", "") or ""

    if not text:
        reason = _finish_reason(response)
        log.warning(
            "Draft generation produced empty content — Gemini block",
            finish_reason=reason or "unknown",
        )

        if reason in ("RECITATION", "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            paraphrase_instruction = (
                "IMPORTANT: Your previous draft response was empty because "
                f"Gemini's {reason.title()} filter flagged it. Rewrite the "
                "document so that, while every party name, date, monetary "
                "amount, and case-specific fact still comes VERBATIM from "
                "the CASE FACTS and USER QUERY NARRATIVE above (those are "
                "non-negotiable), the surrounding LEGAL PROSE, statutory "
                "phrasing, headings, and structural language are "
                "substantially paraphrased in your own words — do not copy "
                "long verbatim passages from the REFERENCE DRAFT or the "
                "uploaded source document. Vary sentence structure, choose "
                "synonyms for non-fact language, and re-order grounds and "
                "sub-clauses where it does not change meaning."
            )
            try:
                with log_time(log, "Draft generation retry (paraphrase)"):
                    response = await _invoke_once(paraphrase_instruction)
                _record_tokens("Drafting", "generate_retry", response)
                text = getattr(response, "text", "") or ""
            except Exception as e:
                log.error("Draft generation retry failed",
                          error=str(e)[:200], exc_info=True)
                # Keep text="" so we surface the error below.

        if not text:
            second_reason = _finish_reason(response)
            log.error(
                "Draft generation blocked twice; surfacing clean error",
                first_reason=reason or "unknown",
                second_reason=second_reason or "unknown",
            )
            raise RuntimeError(
                "Draft generation was blocked by the language model's "
                f"safety filter ({second_reason or reason or 'unknown'}). "
                "Please try rephrasing your request or removing direct "
                "copies of source-document text from the prompt."
            )

    log.info("Draft generated", length=len(text))
    return text


# ---------------------------------------------------------------------------
# Stage 2 — Fan-out judge + section-by-section generator.
#
# The judge runs ONCE per drafting request to decide single-pass vs
# section-wise. When section-wise, it also emits the section list with
# headings already adapted to the user's matter and rendered in the
# user's target output language. The section pair generator then walks
# the list in pairs of two (last is solo if odd), each pair seeing the
# document drafted so far for continuity of numbering / party labels /
# tone.
# ---------------------------------------------------------------------------


class _Section(BaseModel):
    """One section of a fan-out section list. Emitted by `_judge_fanout`."""
    id: str = Field(
        ...,
        description=(
            "Short English slug for internal control (e.g. 'cause_title', "
            "'facts', 'grounds', 'prayer', 'verification'). NOT user-visible."
        ),
    )
    heading: str = Field(
        ...,
        description=(
            "Display heading for the FINAL draft, written in the user's "
            "target output language and script."
        ),
    )
    summary: str = Field(
        "",
        description=(
            "One short sentence (English) of what content goes in this "
            "section. Used internally to brief the section writer."
        ),
    )


# Safety ceiling on the fan-out section list. NOT a target and NOT a default:
# the planner sizes each plan to the request in front of it — a short
# undertaking may need 5 sections, a writ petition with synopsis, grounds,
# affidavit, schedule and index 18 — and this number only bounds the worst
# case so a mis-behaving judge cannot blow the request budget.
#
# Interpolated into DRAFTING_FANOUT_JUDGE_PROMPT as `{max_sections}` rather
# than restated there. Stating a ceiling in two places is what produced the
# bug this constant replaces: the prompt allowed 15, the code kept 12, and
# every plan of 13+ silently lost its closing section.
MAX_SECTIONS = 20


class _FanoutStrategy(BaseModel):
    """Structured output from `_judge_fanout`."""
    should_fanout: bool = Field(
        ...,
        description=(
            "True iff the document benefits from sequential per-section "
            "generation. False = single-pass for short documents."
        ),
    )
    sections: list[_Section] = Field(
        default_factory=list,
        description=(
            "Ordered section list — only meaningful when should_fanout=True. "
            "Size it to what THIS document needs — no target count, no "
            "default shape — and never more than the ceiling stated in "
            "the prompt. The LAST entry must be the block the document "
            "ends with (prayer / verification / signature / execution)."
        ),
    )
    reasoning: str = Field(
        "",
        description="One short sentence explaining the decision.",
    )


# ---------------------------------------------------------------------------
# Per-section source-chunk router (opt-in, off by default).
#
# When enabled and the uploaded user_facts blob is large, each section-pair
# call routes to a subset of paragraph chunks instead of receiving the full
# raw source. This is a cost + overflow optimisation:
#
#   - Before: raw user_facts sent 1× per pair (N pairs × full source)
#   - After:  each pair sees only chunks the router picked as relevant
#
# Preserves CLAUDE.md invariant #6 (raw source flows into every pair) via
# fallback: when the flag is OFF, the router is never called; when it's ON
# but the router fails / returns empty / the upload is below threshold, the
# full raw source is sent unchanged.
#
# Enable per-worker via `DRAFTING_PER_SECTION_CHUNKING=1`. Threshold below
# which chunking is skipped is `_PER_SECTION_CHUNKING_MIN_CHARS`.
# ---------------------------------------------------------------------------

_PER_SECTION_CHUNKING_MIN_CHARS = 100_000

# Split on 2+ newlines. Legal PDFs / DOCX extractions typically have blank
# lines between paragraphs; when they don't (single-paragraph huge blob) we
# return a single chunk and the router-guard bails to passthrough.
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def _chunk_user_facts(user_facts: str) -> list[str]:
    """Split user_facts into paragraph-like chunks preserving order.

    Empty user_facts → empty list. Single-paragraph blob (no blank lines) →
    one chunk containing the whole text. Chunks preserve internal newlines
    and original numbering / heading text so `## paragraph 5` in the source
    still starts with `## paragraph 5` after picking.
    """
    if not user_facts:
        return []
    return [c.strip() for c in _PARAGRAPH_SPLIT_RE.split(user_facts) if c.strip()]


class _SelectedChunks(BaseModel):
    """Structured output from `_pick_relevant_chunk_indices`."""
    chunk_indices: list[int] = Field(
        default_factory=list,
        description=(
            "0-indexed integers into the paragraph-chunk catalog. Empty "
            "list = 'no chunk-level filtering possible for this section' — "
            "the caller falls back to sending the raw source unchanged."
        ),
    )
    reasoning: str = Field(
        "", description="One short sentence rationale.",
    )


async def _pick_relevant_chunk_indices(
    *,
    user_facts_chunks: list[str],
    section: "_Section",
    query: str,
    preview_chars_per_chunk: int = 300,
) -> list[int] | None:
    """Ask Gemini Flash Lite which chunks the section-writer will need.

    Returns:
      * `list[int]` — router SUCCEEDED. May be empty when the model
        genuinely thinks the section needs no source-doc content
        (typical for Verification / signature / cause-title sections
        that generate cleanly from the system prompt + reference draft
        alone).
      * `None` — router FAILED (timeout, provider error, malformed
        output). The caller must distinguish this from a legitimate
        empty pick so it can honour the CLAUDE.md drafting invariant #6
        and fall back to raw source, rather than sending empty
        user_facts (which caused the writer to copy the reference
        template's cause title verbatim on 2026-09-06).
    """
    if not user_facts_chunks:
        return []
    try:
        from config.prompts import DRAFTING_CHUNK_ROUTER_PROMPT

        preview_lines: list[str] = []
        for i, chunk in enumerate(user_facts_chunks):
            preview = chunk[:preview_chars_per_chunk].replace("\n", " ").strip()
            if len(chunk) > preview_chars_per_chunk:
                preview += " ..."
            preview_lines.append(f"[{i}] {preview}")
        catalog = "\n".join(preview_lines)

        llm = get_gemini_flash_lite(temperature=0.0).with_structured_output(_SelectedChunks, include_raw=True)

        prompt = ChatPromptTemplate.from_template(DRAFTING_CHUNK_ROUTER_PROMPT)
        chain = prompt | llm

        # Circuit breaker: if Gemini Flash is unhealthy, skip the router
        # and let the caller fall back to raw user_facts (existing safe path).
        if not is_gemini_flash_available():
            log.warning("Gemini Flash circuit open — skipping chunk router",
                        section=section.heading[:40], fast_fail=True)
            return []

        with log_time(log, f"Chunk router (section {section.heading[:40]})"):
            from core.deadline import bounded_wait_for
            raw_and_parsed = await bounded_wait_for(
                chain.ainvoke({
                    "query": query[:2000],
                    "section_heading": section.heading,
                    "section_summary": section.summary or "(no summary)",
                    "chunk_catalog": catalog,
                    "total_chunks": len(user_facts_chunks),
                }),
                local_timeout=20,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "chunk_router", raw_and_parsed.get("raw"))

        parsed: _SelectedChunks = raw_and_parsed["parsed"]
        picked = [
            i for i in parsed.chunk_indices
            if isinstance(i, int) and 0 <= i < len(user_facts_chunks)
        ]
        record_gemini_flash_success()
        log.info(
            "Chunk router selected",
            section=section.heading[:40],
            picked=len(picked),
            total=len(user_facts_chunks),
            reasoning=parsed.reasoning[:120],
        )
        return picked
    except Exception as e:
        record_gemini_flash_failure()
        log.warning(
            "Chunk router failed; caller will fall back to raw source",
            section=section.heading[:40],
            error=short_err(e),
        )
        # Return None (not []) so the caller can distinguish router
        # FAILURE from legitimate empty picks. Returning [] here was
        # the root cause of the 2026-09-06 canonical-name-substitution
        # regression (caller sent empty user_facts, writer copied the
        # reference template's cause title verbatim).
        return None


def _reference_excerpt(
    reference_draft: str, head: int = 3000, tail: int = 1000,
) -> str:
    """Format a reference draft for the judge's view.

    For short references (<= head+tail), pass the whole thing. For long
    references, take the head (cause title + opening Parts) and tail
    (Prayer + Verification + signature) so the judge sees the structural
    bookends without paying for the full middle.
    """
    if not reference_draft:
        return "(no reference draft available — fan-out is unlikely)"
    text = reference_draft.strip()
    if len(text) <= head + tail:
        return text
    return (
        f"{text[:head]}\n\n"
        f"[...middle of reference omitted for brevity — "
        f"{len(text) - head - tail} chars elided...]\n\n"
        f"{text[-tail:]}"
    )


def _summarise_prior_ai_turn(chat_history) -> str:
    """Build the `chat_history_hint` block for the fan-out judge.

    Looks at the last AIMessage in `chat_history` (if any). When present,
    returns a compact summary the judge can read to decide whether the
    current user query is a polish/redraft follow-up on that response.
    Empty/None history → returns the "no prior turn" phrasing so the
    prompt template's `{chat_history_hint}` slot always has a value.
    """
    # No prior AI turn → follow-up detection is INAPPLICABLE. Emit a
    # neutral instruction so the section header ("Follow-up detection")
    # doesn't accidentally prime the judge toward single-pass. Crucially,
    # this branch must NOT contain a "prefer single-pass when in doubt"
    # hint — that biased the judge to single-pass fresh drafts too
    # (observed on a bail-app prompt post-G-18 ship).
    _no_prior_turn_hint = (
        "This is a fresh drafting request — no prior AI turn exists "
        "in this thread. Follow-up detection does NOT apply here. "
        "Apply the fan-out rules from the earlier sections above as if "
        "this section were absent."
    )
    if not chat_history:
        return _no_prior_turn_hint

    try:
        from langchain.messages import AIMessage
    except Exception:  # pragma: no cover — dep drift
        AIMessage = None  # type: ignore[assignment]

    prior_ai_text = ""
    for msg in reversed(chat_history):
        # Duck-type: some callers pass BaseMessage subclasses, some pass
        # dicts, some pass the raw AIMessage from langchain.messages.
        text = None
        if AIMessage is not None and isinstance(msg, AIMessage):
            text = getattr(msg, "content", "") or ""
        elif isinstance(msg, dict):
            role = msg.get("role") or msg.get("type") or ""
            if role in ("ai", "assistant"):
                text = msg.get("content") or ""
        else:
            role = getattr(msg, "type", "") or getattr(msg, "role", "")
            if role in ("ai", "assistant"):
                text = getattr(msg, "content", "") or ""
        if text:
            prior_ai_text = text
            break

    if not prior_ai_text:
        return _no_prior_turn_hint

    # Head + tail so the judge sees the shape (opening block +
    # signature/prayer block) without paying for the full body.
    head_n, tail_n = 800, 400
    if len(prior_ai_text) <= head_n + tail_n:
        excerpt = prior_ai_text
    else:
        excerpt = (
            f"{prior_ai_text[:head_n]}\n"
            f"[...{len(prior_ai_text) - head_n - tail_n} chars elided...]\n"
            f"{prior_ai_text[-tail_n:]}"
        )
    # Prior-turn branch: emit the full follow-up rules HERE. The
    # "prefer single-pass when in doubt" hint fires only when a prior
    # turn actually exists — never on fresh drafts.
    return (
        "The PRIOR AI TURN in this thread (excerpt shown below) has "
        "already produced a document.\n\n"
        "When the current user query looks like a POLISH / REDRAFT / "
        "TRANSLATE / SHORTEN / LENGTHEN request on that prior turn "
        "(typical phrasings: 'polish this', 'in Marathi', 'make this "
        "more formal', 'shorten to one page', 'elaborate on the "
        "grounds', 'add a prayer clause', 'translate to English'), "
        "STAY SINGLE-PASS regardless of the depth signal or reference "
        "structure. The whole document already exists in the prior "
        "turn; re-fanning-out from scratch would blow the 5-minute "
        "request budget.\n\n"
        "When in doubt about whether the current query is a follow-up "
        "on the prior turn (vs a fresh unrelated drafting task), "
        "prefer single-pass — a mis-fanned-out follow-up costs the "
        "user 4 minutes and returns an error banner; a mis-single-"
        "passed follow-up is still a complete document.\n\n"
        "PRIOR AI TURN EXCERPT:\n" + excerpt
    )


async def _judge_fanout(
    query: str,
    reference_draft: str,
    user_language: str,
    user_intent,
    chat_history=None,
) -> _FanoutStrategy:
    """Decide single-pass vs section-by-section. Always falls back to
    single-pass on any error so traffic never breaks.
    """
    try:
        from core.language import language_name
        from config.prompts import DRAFTING_FANOUT_JUDGE_PROMPT

        lang_name = language_name(user_language)
        excerpt = _reference_excerpt(reference_draft)

        # Depth hint — the fan-out judge previously never saw the depth
        # intent, so "in depth" / "detailed" requests on medium docs
        # (legal notices, complaints, one-page applications) collapsed
        # into single-pass and produced thin output even after the
        # per-section depth-directive shipped. Pass an explicit hint so
        # the judge can bias toward fan-out on depth=detailed.
        depth = getattr(user_intent, "response_depth", "standard") if user_intent else "standard"
        if depth == "detailed":
            depth_directive = (
                "The user asked for a DETAILED draft (response_depth = "
                "'detailed'). Bias toward FAN-OUT even for medium document "
                "types you would normally single-pass — single-pass cannot "
                "adequately deliver the depth the user requested."
            )
        elif depth == "brief":
            depth_directive = (
                "The user asked for a BRIEF draft (response_depth = 'brief'). "
                "Prefer single-pass unless the reference explicitly demands "
                "fan-out."
            )
        else:
            depth_directive = (
                "The user did not express an explicit depth preference. "
                "Apply the default fan-out rules above."
            )

        # Planner model: Flash (full) tier with a thinking budget,
        # upgraded from flash-lite.
        #
        # This is a deliberate TRADE, measured on hi/gu/ta/te + en, 3 runs
        # each, reference held constant per language:
        #
        #   PAYS FOR ITSELF — heading script correctness
        #     Gujarati headings, flash-lite : script match 0.00, 3/3
        #                                     (emitted Devanagari, i.e. Hindi)
        #     Gujarati headings, flash full : script match 1.00, 3/3
        #     A Gujarati draft whose section headings are in Hindi is the
        #     "not appropriate" half of the client complaint, and flash-lite
        #     got it wrong every single time.
        #
        #   COSTS — planned section count drops
        #     en  7,7,7 -> 4,4,4      hi  7,7,7 -> 6,6,6
        #     gu  8,8,8 -> 6,6,7      ta  5,5,5 -> 5,5,5
        #                             te  6,6,6 -> 6,6,7
        #
        # The section-count drop is real and must be watched: this is ONE
        # call per request, so cost is not the issue, but a shorter plan
        # means a shorter document. If drafts regress in length, revisit
        # this before touching the writer — the writer honours whatever plan
        # it is given (instrumentation shows planned == emitted).
        # Routed through get_gemini_flash_planning (Gemini-only, NOT the
        # generation tier) because this is a structured-output call:
        # with_structured_output() has different semantics on Anthropic vs
        # Gemini, and the fan-out judge is an internal planning step, not a
        # user-facing answer.
        llm = get_gemini_flash_planning(
            temperature=0.0, thinking_budget=2048,
        ).with_structured_output(_FanoutStrategy, include_raw=True)

        prompt = ChatPromptTemplate.from_template(DRAFTING_FANOUT_JUDGE_PROMPT)
        chain = prompt | llm

        # Circuit breaker: if Gemini Flash is unhealthy, skip the judge
        # and default to single-pass (existing fallback anyway).
        if not is_gemini_flash_available():
            log.warning("Gemini Flash circuit open — defaulting to single-pass",
                        fast_fail=True)
            return _FanoutStrategy(
                should_fanout=False,
                reasoning="Flash circuit open; defaulted to single-pass",
            )

        chat_history_hint = _summarise_prior_ai_turn(chat_history)

        with log_time(log, "Fan-out judge"):
            from core.deadline import bounded_wait_for
            raw_and_parsed = await bounded_wait_for(
                chain.ainvoke({
                    "query": query[:2000],
                    "reference_excerpt": excerpt,
                    "user_language_name": lang_name,
                    "depth_directive": depth_directive,
                    "chat_history_hint": chat_history_hint,
                    "max_sections": MAX_SECTIONS,
                }),
                # 25s, up from 15s, sized from measured Flash latency: healthy
                # calls run 5.6-14.8s, so 15s sat inside the observed spread and
                # clipped slower-but-healthy calls into the single-pass
                # fallback — which is a shorter one-call document, i.e. a
                # quieter version of the same complaint. `bounded_wait_for`
                # still clamps to whatever is left of the request deadline, so
                # the wider bound cannot push a request past its budget.
                local_timeout=25,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens("Drafting", "fanout_judge", raw_and_parsed.get("raw"))

        parsed = raw_and_parsed["parsed"]
        record_gemini_flash_success()
        log.info(
            "Fan-out judge decided",
            should_fanout=parsed.should_fanout,
            sections=len(parsed.sections),
            reasoning=parsed.reasoning[:160],
        )
        return parsed
    except Exception as e:
        record_gemini_flash_failure()
        log.warning(
            "Fan-out judge failed; defaulting to single-pass",
            error=short_err(e),
        )
        return _FanoutStrategy(
            should_fanout=False,
            reasoning="judge call failed; defaulted to single-pass",
        )


async def _generate_section_pair(
    *,
    sections_to_write: list[_Section],
    section_position_start: int,
    total_sections: int,
    query: str,
    user_facts: str,
    reference_draft: str,
    prior_text: str,
    gathered_context: dict[str, str] | None,
    user_intent,
    user_language: str,
    review_and_redraft_mode: bool = False,
    niche_overlay: str = "",
) -> str:
    """Produce 1 or 2 consecutive sections of the document in one Gemini
    2.5 Pro call. Mirrors the safety / retry pattern of single-pass.

    `user_facts` is the RAW extracted text of uploaded documents, threaded
    into every section-pair call so any section that walks the source
    paragraph-by-paragraph (para-wise reply, rejoinder denials, counter-
    affidavit response) has direct access to the source's paragraph
    structure and numbering.

    `niche_overlay` is the resolved ``## NICHE OVERLAY`` block from
    ``config.drafting_niches``. When non-empty, appended to the section-
    pair system prompt AFTER localisation so overlay statutory anchors
    and section headings survive the localiser.
    """
    from config.prompts import DRAFTING_SECTION_PAIR_PROMPT
    from langchain_core.messages import SystemMessage, HumanMessage

    # In review-and-redraft mode, `reference_draft` is the entire uploaded
    # source (drafting_node sets `reference_text = user_facts`). Multi-MB
    # uploads will push the pair-call over Gemini's 1M-token ceiling even
    # after skipping source_docs_block. Cap here to the same script-aware
    # budget the caller uses for user_facts, with a truncation marker so
    # counsel knows to split the upload.
    if review_and_redraft_mode and reference_draft:
        # Detect dense Indic scripts on the reference_draft sample.
        _ref_indic_ranges = (
            ("devanagari", "ऀ", "ॿ"), ("bengali", "ঀ", "৿"),
            ("gurmukhi", "਀", "੿"), ("gujarati", "઀", "૿"),
            ("oriya", "଀", "୿"),   ("tamil", "஀", "௿"),
            ("telugu", "ఀ", "౿"),  ("kannada", "ಀ", "೿"),
            ("malayalam", "ഀ", "ൿ"),
        )
        _ref_budget = 1_800_000  # Latin default — reference occupies the
                                  # source slot; leaves ~200K chars headroom
                                  # for system prompt + niche overlay +
                                  # gathered context + prior_text.
        _ref_sample = reference_draft[:20_000]
        _ref_sample_len = max(len(_ref_sample), 1)
        for _name, _lo, _hi in _ref_indic_ranges:
            if sum(1 for c in _ref_sample if _lo <= c <= _hi) / _ref_sample_len > 0.3:
                _ref_budget = 1_200_000  # Dense Indic scripts (~2.5 chars/token).
                break
        if len(reference_draft) > _ref_budget:
            _orig_ref_len = len(reference_draft)
            reference_draft = (
                reference_draft[:_ref_budget]
                + f"\n\n[…uploaded document truncated to fit model context: "
                + f"showing first {_ref_budget:,} of {_orig_ref_len:,} chars. "
                + f"Split the upload into smaller sections for full coverage.]"
            )
            log.warning(
                "Review-and-redraft reference_draft exceeded budget — truncated with marker",
                original_chars=_orig_ref_len,
                truncated_chars=len(reference_draft),
                budget=_ref_budget,
            )

    system_prompt = localize_prompt(
        DRAFTING_SECTION_PAIR_PROMPT, user_language, user_intent,
    )
    if niche_overlay:
        system_prompt = system_prompt + "\n\n" + niche_overlay
    if review_and_redraft_mode:
        # See rationale in _generate_single_pass — the section-pair prompt
        # carries the same "REFERENCE DRAFT belongs to a DIFFERENT matter"
        # framing (config/prompts.py DRAFTING_SECTION_PAIR_PROMPT rule #1)
        # that must be flipped at the system-prompt level for review-and-
        # redraft mode to actually preserve the uploaded document's
        # party names, court, case number, etc.
        system_prompt = _REVIEW_AND_REDRAFT_MODE_OVERRIDE + "\n\n---\n\n" + system_prompt

    # Per-section depth anchor for regional-language drafts.
    #
    # The SUBSTANTIVE DEPTH policy in core/language.py already says "same
    # depth as English", but it lands ~96% of the way through a ~43,000-
    # character system prompt and measurably does not move the output —
    # regional-language sections still came back at 61% of the English word
    # count with it in place. Restating the expectation HERE, inside the
    # task list the model is actually executing, puts it where it cannot be
    # missed. English is unaffected (empty string).
    depth_anchor = ""
    if user_language and user_language != "en":
        depth_anchor = (
            "\n   (DEPTH: write this section at full English depth — each "
            "numbered paragraph is 3-5 complete sentences carrying its own "
            "legal reasoning. Do NOT abbreviate because the output language "
            "is not English.)"
        )

    section_lines: list[str] = []
    for offset, sec in enumerate(sections_to_write):
        position = section_position_start + offset
        section_lines.append(
            f"{offset + 1}. **{sec.heading}** — {sec.summary or '(see structure in reference)'}\n"
            f"   (Section {position} of {total_sections} in the full document; "
            f"slug: `{sec.id}`)"
            f"{depth_anchor}"
        )
    sections_block = "\n\n".join(section_lines)

    # In review-and-redraft mode, `reference_draft` IS `user_facts`
    # (drafting_node sets `reference_text = user_facts`). Emitting both
    # blocks duplicates the same content and blows the 1M-token ceiling
    # on multi-MB uploads. Live 2026-09-03: 1.9M-char arbitration
    # paperbook -> pair_user_facts + reference_draft = ~3.8M chars
    # ≈ 1M+ tokens → 400 INVALID_ARGUMENT on both Pro and Flash. The
    # reference block below already tells the model to treat the block
    # as the authoritative source in review-and-redraft mode, so
    # skipping source_docs_block here loses zero information.
    source_docs_block = (
        "## UPLOADED SOURCE DOCUMENTS (verbatim raw text — every party "
        "name, date, address, amount, statutory reference, and paragraph-"
        "level assertion in your output MUST be sourced from this block or "
        "the USER QUERY below. When your section requires walking the "
        "source paragraph-by-paragraph — para-wise reply, rejoinder "
        "denials, counter-affidavit response — use the paragraph structure "
        "and numbering from this block directly, quoting or paraphrasing "
        "the specific assertions your section is responding to.)\n"
        f"{user_facts.strip()}\n\n"
    ) if user_facts and user_facts.strip() and not review_and_redraft_mode else ""

    context_block = ""
    if gathered_context:
        parts = []
        for key in ("newacts", "legislation", "judgments", "sci"):
            v = gathered_context.get(key)
            if v:
                parts.append(v)
        if parts:
            context_block = (
                "## RELEVANT LEGAL CONTEXT (statutes and precedents retrieved "
                "for this matter — use these for INLINE STATUTORY CITATIONS "
                "and LEGAL REASONING; do NOT wholesale copy their party names "
                "or case facts into the draft)\n"
                + "\n".join(parts)
                + "\n"
            )

    if review_and_redraft_mode:
        # This block is the ONLY carrier of the source content in
        # review-and-redraft mode (source_docs_block is intentionally
        # skipped above to avoid doubling the payload past 1M tokens).
        reference_block = (
            "## REFERENCE DRAFT / UPLOADED SOURCE DOCUMENT (this is the "
            "SAME document the user uploaded — the user is performing a "
            "REVIEW-AND-REDRAFT of THEIR OWN document. PRESERVE every "
            "party name, court name, case number, forum, statutory "
            "citation, address, date, monetary amount, paragraph number, "
            "and case-specific detail in this block VERBATIM. Every fact, "
            "stated assertion, and paragraph reference in your output "
            "MUST be sourced from this block or the USER QUERY. When "
            "your section is a para-wise reply, rejoinder, or "
            "counter-affidavit, mirror the paragraph numbering from this "
            "block. Correct ONLY the substantive legal errors (wrong "
            "statute, wrong Act name, missing procedural section, "
            "misstated law, missing verification / prayer conventions) "
            "and formatting. Do NOT rewrite this as a generic template. "
            "Do NOT replace real values with `[placeholders]`. Do NOT "
            "introduce a contract-analysis / memorandum / MOU / "
            "lease-deed shape unless the uploaded document itself is "
            "one of those.)\n"
            f"{reference_draft.strip()}\n\n"
        )
    else:
        reference_block = (
            "## REFERENCE DRAFT (STRUCTURE-ONLY example from a different matter — "
            "IGNORE every name, date, address, amount, party detail in this block. "
            "Use ONLY the reference's shape: section ordering, headings, conventions, "
            "phrasing patterns, and statutory-citation style.)\n"
            f"{reference_draft.strip() if reference_draft else '(no reference draft available — follow Indian-law conventions for the document type)'}\n\n"
        )

    if prior_text and prior_text.strip():
        prior_block = (
            "## DOCUMENT SO FAR (sections of THIS document already drafted — "
            "continue numbering and party labels from here; do NOT re-emit "
            "any of this content)\n"
            f"{prior_text.strip()}\n\n"
        )
    else:
        prior_block = (
            "## DOCUMENT SO FAR\n"
            "(This is the FIRST section batch — no prior content. Start the "
            "global paragraph counter at 1 where appropriate.)\n\n"
        )

    # TODAY'S DATE - required by the MISSING-FACT POLICY rule, which tells the
    # model "today's date and the drafting place are legitimate defaults if the
    # source is silent". Until 2026-09-07 the drafting prompt never said what
    # today WAS, so a model following that rule had to guess. Measured:
    # claude-sonnet-5 emitted "Date: 05 August 2026" on a notice drafted
    # 07 September 2026 - neither today nor a supplied date - while
    # gemini-3.8-flash sidestepped the rule with [TO_FILL:]. Neither is right on
    # a statutory notice where limitation runs from the notice date. Every other
    # agent already passes "Current Date" (document, legislation, judgment,
    # newacts, constitution_maxim); drafting was the outlier.
    #
    # Deliberately in the USER block, not the system prompt: the system prompt
    # carries cache_control for Anthropic, and a date that changes daily inside
    # the cached prefix would invalidate the cache every day.
    _today_block = (
        f"## TODAY'S DATE\n{date.today().strftime('%d %B %Y')}\n"
        "Use this when the document needs a notice / verification / drafting "
        "date and the source is silent. Never infer such a date from other "
        "dates in the matter.\n\n"
    )
    user_block = (
        f"{_today_block}"
        f"{context_block}"
        f"{reference_block}"
        f"{prior_block}"
        f"{source_docs_block}"
        "## USER QUERY (the full document the user asked for — your section(s) "
        "are part of this larger document)\n"
        f"{query.strip()}\n\n"
        "## SECTIONS YOU MUST WRITE NOW\n"
        f"{sections_block}\n\n"
        "Produce ONLY the section bodies named above, in order, each starting "
        "with its own `## ` heading line. No preamble. No postscript. No "
        "transition text between two sections. Continue paragraph numbering "
        "from DOCUMENT SO FAR. Every party name, date, address, monetary "
        "amount, and case-specific detail MUST come VERBATIM from the "
        "UPLOADED SOURCE DOCUMENTS or the USER QUERY. Do NOT substitute "
        "canonical Indian-legal example values (e.g. 'Priyanka', 'Sneha', "
        "'Bhausaheb', 'Sakore', 'Anjali Deshmukh', 'Nashik', 'Sangamner', "
        "'Ahmednagar', '29 May 2022', '1 June 2020') for the real parties "
        "and dates named in the source — that is a CRITICAL error. Cite "
        "statutes inline from RELEVANT LEGAL CONTEXT where applicable."
    )

    llm = get_drafting_llm(
        max_output_tokens=20000,
        thinking_budget=2048,
    )

    # Bound to the request deadline, then fail over to OpenAI.
    #
    # This call used to be an unbounded `asyncio.to_thread(llm.invoke, ...)`.
    # `get_drafting_llm` is configured timeout=180, max_retries=1, so the SDK
    # could spend ~540 s+ inside a single call against a 300 s request
    # budget. Observed in production logs: one section-pair call ran for
    # 592 s, the connection dropped, the retry was then skipped as "budget
    # exhausted", the section was dropped, and the user received an empty
    # draft with no error shown.
    #
    # Two changes:
    #   1. `bounded_wait_for` clamps the wait to whatever is left of the
    #      request deadline, so a hung provider can no longer blow the
    #      envelope for everything downstream.
    #   2. On timeout / error / open circuit we retry the SAME prompt on
    #      GPT-4o instead of dropping the section. Every generation path in
    #      this service is Gemini-only; this is the first path with a real
    #      second provider behind it.
    #
    # Timeout chosen from 124 measured healthy calls:
    #   min 9.3s | median 21.8s | p90 32.2s | max 56.0s
    # 60s sits above the observed maximum, so healthy traffic is not pushed
    # onto the fallback. Measured end-to-end against a fully hung Gemini
    # (5 section pairs, 300s request budget):
    #   120s -> 477 words,  2/5 pairs recovered, draft flagged incomplete
    #    60s -> 1168 words, 4/5 pairs recovered, draft flagged incomplete
    #    45s -> 1240 words, 5/5 pairs recovered, draft COMPLETE
    # 45s recovers more during an outage but would divert ~3% of healthy
    # pairs (~15% of drafts) to GPT-4o during normal operation. Outages are
    # rare and normal traffic is constant, so this favours the common case
    # and lets the `draft_incomplete` flag tell the user when a section was
    # lost. Lower it if outage-time completeness matters more than provider
    # consistency day to day.
    _SECTION_PAIR_LOCAL_TIMEOUT = 60

    def _messages(extra_instruction: str) -> list:
        final_user_block = (
            user_block + "\n\n" + extra_instruction if extra_instruction
            else user_block
        )
        return [SystemMessage(content=cacheable_system(system_prompt)),
                HumanMessage(content=final_user_block)]

    async def _invoke_once(extra_instruction: str = "") -> object:
        from core.deadline import bounded_wait_for
        msgs = _messages(extra_instruction)
        try:
            return await bounded_wait_for(
                asyncio.to_thread(llm.invoke, msgs),
                local_timeout=_SECTION_PAIR_LOCAL_TIMEOUT,
            )
        except Exception as primary_err:
            # 2026-09-02: fallback switched from OpenAI GPT-4o to Gemini
            # 2.5 Flash. The old GPT-4o fallback had a 128 K token limit,
            # which is smaller than legal source-document uploads
            # routinely reach (a full arbitration paperbook can push
            # 500 K-1 M tokens even after Gap #5's MMR router). Observed
            # in a live test 2026-09-02: primary Gemini Pro hit its 1 M
            # cap, failover to GPT-4o hit ITS 128 K cap ~immediately,
            # both retries failed identically -- fallback added zero
            # value while burning latency + OpenAI cost. Gemini Flash
            # shares the 1 M context ceiling AND is markedly cheaper
            # than either Gemini Pro or GPT-4o, so as a second-provider
            # substitute for Pro's tool-planning / reasoning quirks it's
            # an unambiguous win: same reach, lower cost, no cross-
            # provider tokenizer surprise. Anything genuinely over 1 M
            # tokens needs Gap #5's MMR router or per-section chunking
            # (DRAFTING_PER_SECTION_CHUNKING=1); the fallback path was
            # never the right lever for that class of overflow.
            from core.clients import get_gemini_flash_full
            log.warning(
                "Section-pair generation failed — failing over to the Flash tier",
                error=short_err(primary_err),
                provider_from=MODELS["drafting"],
                provider_to=GEMINI_MODELS["flash"],
            )
            fb = get_gemini_flash_full(
                temperature=0.0,
                max_output_tokens=12000,
                thinking_budget=0,
            )
            resp = await bounded_wait_for(
                asyncio.to_thread(fb.invoke, msgs),
                local_timeout=_SECTION_PAIR_LOCAL_TIMEOUT,
            )
            log.info(
                "Gemini Flash fallback produced the section pair",
                provider=GEMINI_MODELS["flash"],
            )
            return resp

    from core.token_tracker import record as _record_tokens

    def _finish_reason(r) -> str:
        meta = getattr(r, "response_metadata", None) or {}
        return str(meta.get("finish_reason") or "").upper()

    if len(sections_to_write) == 1:
        section_label = f"section {section_position_start}"
    else:
        end = section_position_start + len(sections_to_write) - 1
        section_label = f"sections {section_position_start}-{end}"

    try:
        with log_time(log, f"Section pair gen ({section_label})"):
            response = await _invoke_once()
    except Exception as e:
        log.error(
            "Section pair LLM call failed",
            section_label=section_label,
            error=str(e)[:200], exc_info=True,
        )
        raise

    _record_tokens("Drafting", "generate_section_pair", response)
    text = getattr(response, "text", "") or ""

    if not text:
        reason = _finish_reason(response)
        log.warning(
            "Section pair produced empty content",
            section_label=section_label, finish_reason=reason or "unknown",
        )
        if reason in ("RECITATION", "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            paraphrase_instruction = (
                f"IMPORTANT: Your previous response was empty because Gemini's "
                f"{reason.title()} filter flagged it. Rewrite the section(s) "
                f"so that the surrounding LEGAL PROSE, statutory phrasing, "
                f"headings, and structural language are substantially "
                f"paraphrased in your own words — do not copy long verbatim "
                f"passages from the REFERENCE DRAFT or any source document. "
                f"Every party name, date, monetary amount, and case-specific "
                f"fact still comes VERBATIM from CASE FACTS and the USER "
                f"QUERY (those are non-negotiable). Vary sentence structure, "
                f"choose synonyms for non-fact language, and re-order "
                f"sub-clauses where it does not change meaning."
            )
            try:
                with log_time(log, f"Section pair retry ({section_label})"):
                    response = await _invoke_once(paraphrase_instruction)
                _record_tokens(
                    "Drafting", "generate_section_pair_retry", response,
                )
                text = getattr(response, "text", "") or ""
            except Exception as e:
                log.error(
                    "Section pair retry failed",
                    section_label=section_label,
                    error=str(e)[:200], exc_info=True,
                )

        if not text:
            log.error(
                "Section pair blocked twice; emitting empty section",
                section_label=section_label,
            )
            return ""

    log.info(
        "Section pair generated",
        section_label=section_label, length=len(text),
    )
    return text


# Repair budget. A section pair costs ~25s against a 300s request ceiling, so
# repair is capped at two pairs and only runs with real budget left — a draft
# missing its Prayer is worth 25s, but not a gateway timeout.
_MAX_REPAIR_SECTIONS = 4
_REPAIR_MIN_BUDGET_S = 40


# Not every planned section that fails to appear is a defect.
#
# The planner works from a canonical checklist and lists what a document of
# this family CAN carry; the section prompt then tells the writer to "OMIT
# ONLY FOR A FACTUAL REASON" — drop parity when there are no co-accused, drop
# medical grounds when none are pleaded. That omission is CORRECT, and the
# test scenario (one accused, no co-accused) triggers it constantly.
#
# Measured over 20 drafts: planned-vs-emitted disagreed in half of them, but
# almost every gap was a conditional section the writer was right to drop.
# Exactly ONE draft was genuinely defective — a Bengali filing missing its
# Verification. Repairing indiscriminately would have re-inserted parity
# sections into cases with no co-accused, i.e. made the drafts worse while
# spending ~25s per call to do it.
#
# So repair is limited to the sections a filing cannot be without. Matching is
# on the planner's English `id` slug rather than the localized heading, so it
# works identically for Odia and Kannada without a per-language word list —
# an earlier keyword-based attempt produced five false positives on Kannada
# because it used the wrong word for "facts".
_MANDATORY_SECTION_HINTS = (
    "title", "cause", "fact", "ground", "prayer", "relief", "verif",
    # Transactional instruments (MOU, agreement, deed, lease) end in an
    # execution block, not a prayer. Without these a contract that lost its
    # signature section reads as complete to the repair pass and ships as an
    # unsigned fragment — the exact defect reported on a commercial MOU.
    "execut", "signat", "attest", "witness",
)
_CONDITIONAL_SECTION_HINTS = (
    "parity", "co_accused", "coaccused", "medical", "family", "undertak",
    "advocate", "document", "annexure", "schedule", "index",
)


def _is_mandatory_section(sec: "_Section") -> bool:
    """Is this a section the document is invalid without?

    Conditional hints are checked FIRST: "medical_family_grounds" contains
    "ground" and would otherwise read as mandatory when it is exactly the
    kind of section the writer is supposed to drop when the facts are silent.
    """
    slug = f"{getattr(sec, 'id', '')} {getattr(sec, 'heading', '')}".lower()
    if any(h in slug for h in _CONDITIONAL_SECTION_HINTS):
        return False
    return any(h in slug for h in _MANDATORY_SECTION_HINTS)

# A full redraft costs about as much as the first pass (~150s measured), and
# the request ceiling is 300s. Only retry with real budget left.
_OFF_TARGET_RETRY_MIN_BUDGET_S = 150


def _normalise_heading(text: str) -> str:
    """Fold a heading for comparison: strip markdown, punctuation, case, space.

    Deliberately script-agnostic — `str.lower()` is a no-op on Indic scripts,
    and the comparison must work identically for Odia and English.
    """
    t = re.sub(r"[#*_`>]", " ", text or "")
    t = re.sub(r"[\s ]+", " ", t)
    t = t.strip(" :.-—–\t").lower()
    return t


# Sections whose "heading" is inherently the document's opening block rather
# than a descriptive label. The writer legitimately adapts the planner's
# abstract placeholder ("BEFORE THE HON'BLE COURT / TRIBUNAL", "Cause Title
# and Parties", "TO,") into the actual court caption or addressee block for
# THIS matter. Substring matching between the abstract and adapted forms
# systematically fails ("cause title" vs "before the adjudicating authority,
# central goods & services tax..." share no substring), so these ids are
# treated as PRESENT whenever the draft has any heading in its opening
# portion — the actual court name IS the heading.
_OPENING_BLOCK_SECTION_IDS = frozenset({
    "cause_title", "cause", "court_title", "court_caption", "caption",
    "heading", "addressee", "addressee_block", "party_block", "parties",
    "title", "court_and_parties", "cause_and_parties",
})


def _substantive_word_overlap(want: str, got: str, min_ratio: float = 0.5) -> bool:
    """Fuzzy match — do these two headings share at least `min_ratio` of
    their substantive words (ignoring stopwords)?

    Catches legitimate heading adaptations the strict substring check misses:
      "Preliminary Objections" vs "Preliminary Submissions and Objections"
        -> {preliminary, objections} vs {preliminary, submissions, objections}
        -> 2/2 want words present = 1.0 -> matched
      "Reply on Merits" vs "Para-wise Reply and Denial on Merits"
        -> {reply, merits} vs {para, wise, reply, denial, merits}
        -> 2/2 want words present = 1.0 -> matched
      "Grounds for Bail" vs "Legal Grounds"
        -> {grounds, bail} vs {legal, grounds}
        -> 1/2 want words present = 0.5 -> matched at threshold
    """
    _STOP = frozenset({
        "the", "a", "an", "of", "and", "or", "for", "to", "on", "in", "at",
        "by", "with", "as", "is", "are", "be", "this", "that",
    })
    def _words(t: str) -> set[str]:
        return {w for w in re.split(r"[^a-z0-9]+", t.lower()) if w and w not in _STOP and len(w) > 2}
    w_want = _words(want)
    w_got = _words(got)
    if not w_want or not w_got:
        return False
    shared = w_want & w_got
    return len(shared) / len(w_want) >= min_ratio



def _insert_repaired_in_plan_order(
    draft: str,
    sections: list["_Section"],
    missing: list["_Section"],
    repaired: list[str],
) -> str:
    """Splice repaired sections back at their planned positions.

    The repair pass regenerates sections the writer skipped. Appending them
    put a missing "Statement of Facts" after the Prayer - structurally wrong
    for a filing, and what advocates flagged 2026-09-08 as sections being
    "up and down".

    For each repaired chunk, find the planned section that FOLLOWS it and is
    present in the draft, then insert immediately before that heading. When
    no following section is present the gap is at the end, so appending is
    correct there. Never loses content: an unlocatable anchor degrades to the
    previous append behaviour.
    """
    if not repaired:
        return draft

    heading_pos: dict[str, int] = {}
    for line in draft.splitlines():
        st = line.strip()
        if st.startswith("#") or (st.startswith("**") and st.endswith("**")):
            norm = _normalise_heading(st)
            if norm and norm not in heading_pos:
                heading_pos[norm] = draft.find(line)

    def _anchor_for(sec: "_Section") -> int:
        try:
            idx = next(i for i, s in enumerate(sections) if s.id == sec.id)
        except StopIteration:
            return -1
        for later in sections[idx + 1:]:
            pos = heading_pos.get(_normalise_heading(later.heading))
            if pos is not None and pos >= 0:
                return pos
        return -1

    plan_order = {s.id: i for i, s in enumerate(sections)}
    paired = list(zip(missing, repaired))[: len(repaired)]
    # Insert from the LAST planned position backwards so earlier offsets stay
    # valid as the string grows.
    paired.sort(key=lambda pr: plan_order.get(pr[0].id, 10_000), reverse=True)

    appended: list[str] = []
    for sec, chunk in paired:
        at = _anchor_for(sec)
        if at > 0:
            draft = draft[:at] + chunk.strip() + "\n\n" + draft[at:]
        else:
            appended.append(chunk)
    if appended:
        draft = draft + "\n\n" + "\n\n".join(reversed(appended))
    return draft

def _missing_planned_sections(draft: str, sections: list["_Section"]) -> list["_Section"]:
    """Planned sections that never made it into the draft.

    Three-tier presence check per section, in order of strictness:

      1. OPENING-BLOCK exemption: sections in `_OPENING_BLOCK_SECTION_IDS`
         (cause_title, addressee, court_caption, etc.) are treated as
         present whenever the draft has any heading in its opening ~40
         lines. Rationale: the writer legitimately replaces the planner's
         abstract heading with the real court caption for THIS matter, so
         substring / word-overlap on the abstract heading is unreliable.
         Their absence downstream causes the repair to blindly re-emit the
         planner's abstract heading and append it as a trailing fragment
         (2026-09-06 duplicated-cause-title incident).

      2. SUBSTRING match (original behaviour): normalised planner heading
         appears in one of the draft's headings, or vice versa.

      3. WORD-OVERLAP match (new): at least 50% of the substantive words
         in the planner's heading appear in one of the draft's headings.
         Catches legitimate rewordings like "Grounds" -> "Legal Grounds"
         and "Preliminary Objections" -> "Preliminary Submissions and
         Objections" that substring matching misses in both directions.

    Any of the three matching → section present. All three fail → missing.
    """
    if not draft or not sections:
        return []
    draft_headings: list[str] = []
    opening_has_heading = False
    _opening_lines_scanned = 0
    _OPENING_WINDOW_LINES = 40
    for line in draft.splitlines():
        s = line.strip()
        if not s:
            _opening_lines_scanned += 1
            continue
        _opening_lines_scanned += 1
        # `## Heading`, and also `**Heading**` alone on a line — some drafts
        # mark sections in bold rather than with hashes, and treating those as
        # "no heading" would flag a complete document as entirely missing.
        if s.startswith("#") or (s.startswith("**") and s.endswith("**") and len(s) < 120):
            h = _normalise_heading(s)
            if h:
                draft_headings.append(h)
                if _opening_lines_scanned <= _OPENING_WINDOW_LINES:
                    opening_has_heading = True

    # No headings recognised at all: the writer is not marking sections in a
    # form we can read, so we cannot tell a dropped section from a differently
    # formatted one. Repairing here would burn ~25s per pair to append
    # duplicates of content that may already be present. Judge nothing.
    if not draft_headings:
        return []

    missing: list["_Section"] = []
    for sec in sections:
        sec_id = (getattr(sec, "id", "") or "").lower().strip()
        want = _normalise_heading(sec.heading)
        if not want:
            continue

        # Tier 1 — opening-block sections are present iff the draft opens
        # with any heading. The writer's adapted court caption IS the heading.
        if sec_id in _OPENING_BLOCK_SECTION_IDS and opening_has_heading:
            continue

        # Tier 2 — original substring match (either direction).
        if any(want in got or got in want for got in draft_headings):
            continue

        # Tier 3 — word-overlap match (>= 50% of substantive words shared).
        if any(_substantive_word_overlap(want, got) for got in draft_headings):
            continue

        missing.append(sec)
    return missing


async def _generate_sectionwise(
    *,
    sections: list[_Section],
    query: str,
    user_facts: str,
    reference_draft: str,
    user_intent,
    user_language: str,
    progress_emit,
    gathered_context: dict[str, str] | None,
    review_and_redraft_mode: bool = False,
    niche_overlay: str = "",
) -> str:
    """Walk the section list in pairs of two (last is solo if odd).

    Each pair is one Gemini Pro call seeing the document so far AND the
    uploaded source documents. `user_facts` is threaded into every pair
    call (not just the first) so any section that needs to walk the source
    paragraph-by-paragraph (para-wise reply, rejoinder denials, counter-
    affidavit response) has direct access. A failed pair is logged and
    skipped — the loop continues so the user gets a partial draft instead
    of a hard failure.
    """
    completed: list[str] = []
    failed_pairs: list[dict] = []   # {position_start, headings, reason}
    total = len(sections)

    # Per-section source-chunk routing (opt-in via env flag).
    # When ON and user_facts is above threshold, each pair sees only the
    # paragraph chunks the router picked as relevant to that pair's
    # sections (union across the pair). Union is taken so a section that
    # needs paragraphs 3-5 and its pair-mate that needs paragraphs 7-9
    # still see 3,4,5,7,8,9 in a single call. On router failure / empty
    # selection, the pair falls back to the raw user_facts unchanged —
    # this preserves CLAUDE.md invariant #6 whenever the router can't help.
    #
    # Skipped entirely in review-and-redraft mode: the section-pair prompt
    # deliberately drops source_docs_block in that mode because the
    # reference_block carries the same content (see 2026-09-03 fix in
    # _generate_section_pair — double-loading blew the 1M-token ceiling).
    # With source_docs_block skipped, routing over user_facts is dead
    # work — every pick is discarded.
    chunking_enabled = (
        os.getenv("DRAFTING_PER_SECTION_CHUNKING", "0") == "1"
        and user_facts
        and len(user_facts) > _PER_SECTION_CHUNKING_MIN_CHARS
        and not review_and_redraft_mode
    )
    all_chunks: list[str] = _chunk_user_facts(user_facts) if chunking_enabled else []
    # A single-chunk (no blank lines) or trivially-few-chunks blob has no
    # routing signal to extract; skip the router and pass raw source.
    if chunking_enabled and len(all_chunks) < 4:
        chunking_enabled = False
        log.info(
            "Per-section chunking skipped — too few paragraph chunks to route",
            chunks=len(all_chunks),
        )

    # Per-pair char budget — headroom below Gemini's 1M-token ceiling
    # after accounting for system prompt (~20K tokens), reference draft
    # (~50K), prior_text (~30K), section instructions (~5K), and gen
    # output (~12K). Live 2026-09-02: an arbitration paperbook upload
    # at 1.88M chars pushed a section-pair call to 1.03M tokens even
    # after routing (router picked 99.2% of chunks) or fell back to raw
    # source (Verification section legitimately needed no source docs
    # but raw fallback still overflowed). This cap protects both paths.
    # Latin ~4 chars/token -> 2.4M chars = ~600K tokens.
    # Dense Indic scripts ~2.5 chars/token -> 1.6M chars = ~640K tokens.
    _PAIR_USER_FACTS_BUDGET_LATIN = 2_400_000
    _PAIR_USER_FACTS_BUDGET_INDIC = 1_600_000
    # Same script detection as `_generate_draft`'s whole-request preflight.
    _INDIC_RANGES = (
        ("devanagari", "ऀ", "ॿ"),
        ("bengali",    "ঀ", "৿"),
        ("gurmukhi",   "਀", "੿"),
        ("gujarati",   "઀", "૿"),
        ("oriya",      "଀", "୿"),
        ("tamil",      "஀", "௿"),
        ("telugu",     "ఀ", "౿"),
        ("kannada",    "ಀ", "೿"),
        ("malayalam",  "ഀ", "ൿ"),
    )
    _detected_script = "latin"
    _pair_budget = _PAIR_USER_FACTS_BUDGET_LATIN
    if user_facts:
        _sample = user_facts[:20_000]
        _sample_len = max(len(_sample), 1)
        for _name, _lo, _hi in _INDIC_RANGES:
            if sum(1 for c in _sample if _lo <= c <= _hi) / _sample_len > 0.3:
                _detected_script = _name
                _pair_budget = _PAIR_USER_FACTS_BUDGET_INDIC
                break

    i = 0
    while i < total:
        pair = sections[i:i + 2]
        position_start = i + 1

        for offset, sec in enumerate(pair):
            position = position_start + offset
            progress_emit(
                "drafting",
                f"Drafting section {position} of {total}: {sec.heading}",
                substep=True,
                step=f"section:{position}",
            )

        prior_text = "\n\n".join(completed)

        pair_user_facts = user_facts
        if chunking_enabled:
            # return_exceptions=True keeps one section's router crash from
            # killing the entire sectionwise loop — the guard downstream
            # already falls back to raw user_facts on empty picks, so an
            # exception from one section is equivalent to it picking [].
            per_section_picks_raw = await asyncio.gather(
                *[
                    _pick_relevant_chunk_indices(
                        user_facts_chunks=all_chunks,
                        section=sec,
                        query=query,
                    )
                    for sec in pair
                ],
                return_exceptions=True,
            )
            per_section_picks = []
            any_router_failure = False
            for sec, picks in zip(pair, per_section_picks_raw):
                if isinstance(picks, BaseException):
                    log.warning(
                        "Chunk router raised for section; treating as router "
                        "failure (raw-source fallback for the pair)",
                        section=sec.heading[:40],
                        error=short_err(picks),
                    )
                    any_router_failure = True
                    per_section_picks.append([])
                elif picks is None:
                    # `_pick_relevant_chunk_indices` returns None (not [])
                    # to signal a real failure (timeout, provider error).
                    # An empty list means "picked nothing legitimately".
                    any_router_failure = True
                    per_section_picks.append([])
                else:
                    per_section_picks.append(picks)
            union_indices = sorted({idx for picks in per_section_picks for idx in picks})
            if union_indices:
                pair_user_facts = "\n\n".join(all_chunks[idx] for idx in union_indices)
                log.info(
                    "Per-section chunking applied",
                    pair_start=position_start,
                    picked_chunks=len(union_indices),
                    total_chunks=len(all_chunks),
                    original_chars=len(user_facts),
                    reduced_chars=len(pair_user_facts),
                    reduction_pct=round(
                        100 * (1 - len(pair_user_facts) / max(len(user_facts), 1)), 1,
                    ),
                )
            elif any_router_failure:
                # 2026-09-06: router TIMED OUT or ERRORED (not the same as
                # "router intentionally picked 0"). Per the CLAUDE.md
                # drafting invariant #6 ("Whenever the router fails,
                # returns empty, or the blob has <4 chunks, the pair
                # falls back to the raw source"), fall back to raw
                # source here rather than sending empty user_facts.
                # Sending empty on a failure caused the writer to
                # copy the reference-template's cause title verbatim
                # (canonical-name substitution) because it had no
                # source facts to work from.
                pair_user_facts = user_facts
                log.info(
                    "Per-section chunking: router failed for at least one "
                    "section in this pair — falling back to raw source",
                    pair_start=position_start,
                    section_headings=[s.heading[:60] for s in pair],
                    raw_chars=len(pair_user_facts),
                )
            else:
                # 2026-09-02: router INTENTIONALLY picked 0 chunks -> this
                # section legitimately doesn't need source-document content
                # (typical for Verification / signature / cause-title
                # sections that generate cleanly from the system prompt +
                # reference draft + prior_text alone). Send EMPTY
                # user_facts instead of raw-source fallback; the raw
                # fallback used to overflow the 1M-token ceiling on very
                # large uploads and caused legitimate 0-pick sections to
                # fail with a context_length error.
                pair_user_facts = ""
                log.info(
                    "Per-section chunking: no chunks picked — sending empty "
                    "user_facts (section doesn't need source docs)",
                    pair_start=position_start,
                    section_headings=[s.heading[:60] for s in pair],
                )

        # 2026-09-02: post-router / raw-fallback safety cap. Even after
        # routing (or with raw fallback when chunking is disabled), the
        # blob may still exceed our safe per-pair budget on very large
        # uploads. Truncate with a marker so the section produces output
        # rather than failing on 1M-token overflow. Trade-off: partial-
        # verbatim source vs a fully-failed section — the fully-failed
        # section is unambiguously worse for the user.
        if len(pair_user_facts) > _pair_budget:
            _orig_chars = len(pair_user_facts)
            pair_user_facts = (
                pair_user_facts[:_pair_budget]
                + f"\n\n[…source material truncated to fit model context: "
                + f"showing first {_pair_budget:,} of {_orig_chars:,} chars. "
                + f"Split the upload into smaller PDFs for full-verbatim access.]"
            )
            log.warning(
                "Per-pair user_facts exceeded budget — truncated with marker",
                pair_start=position_start,
                original_chars=_orig_chars,
                truncated_chars=len(pair_user_facts),
                budget=_pair_budget,
                script=_detected_script,
            )

        # One retry per pair (Buglist Phase 2). The first attempt uses
        # the Gemini SDK's default timeout; the retry uses the SAME
        # timeout but is bounded by the request deadline via
        # `core.deadline` — so a pair that costs 90s on attempt 1 and
        # has only 40s of budget left will NOT re-fire and burn another
        # 90s. Retry fires on either exception or empty content.
        pair_text = ""
        _pair_err: Exception | None = None
        for attempt in (1, 2):
            try:
                pair_text = await _generate_section_pair(
                    sections_to_write=pair,
                    section_position_start=position_start,
                    total_sections=total,
                    query=query,
                    user_facts=pair_user_facts,
                    reference_draft=reference_draft,
                    prior_text=prior_text,
                    gathered_context=gathered_context,
                    user_intent=user_intent,
                    user_language=user_language,
                    review_and_redraft_mode=review_and_redraft_mode,
                    niche_overlay=niche_overlay,
                )
                _pair_err = None
                if pair_text.strip():
                    break
                # Empty content on attempt 1 → retry once.
                if attempt == 1:
                    log.info(
                        "Section pair returned empty content; retrying once",
                        position_start=position_start,
                    )
                    await asyncio.sleep(1)
                    continue
                break
            except Exception as e:
                _pair_err = e
                if attempt == 1:
                    # Deadline check: don't retry if we've already
                    # blown the request budget. The retry itself would
                    # just fail again after another N seconds.
                    from core.deadline import remaining as _remaining
                    rem = _remaining()
                    if rem is not None and rem <= 15:
                        log.warning(
                            "Section pair failed; skipping retry (budget exhausted)",
                            position_start=position_start,
                            error=short_err(e),
                            remaining_s=round(rem, 1),
                        )
                        break
                    log.warning(
                        "Section pair failed; retrying once",
                        position_start=position_start,
                        error=short_err(e),
                    )
                    await asyncio.sleep(1)
                    continue
                # Attempt 2 failed too.
                log.warning(
                    "Section pair failed on retry; giving up",
                    position_start=position_start,
                    error=short_err(e),
                )
                pair_text = ""
                break

        if pair_text.strip():
            completed.append(pair_text.strip())
        else:
            # Both attempts produced empty content OR both raised.
            failed_pairs.append({
                "position_start": position_start,
                "headings": [s.heading for s in pair],
                "reason": short_err(_pair_err) if _pair_err else "empty_content",
            })

        i += 2

    draft = "\n\n".join(completed)

    # --- Plan adherence -----------------------------------------------------
    #
    # `failed_pairs` above only catches a pair that threw or returned nothing.
    # It does NOT catch the more damaging failure: a pair that returns fluent
    # text while ignoring the section list it was given.
    #
    # Observed on Odia (3/3 runs planned correctly, the writer still deviated):
    # the plan was cause title / facts / grounds / family / undertakings /
    # PRAYER / VERIFICATION, and the writer emitted "An analysis regarding a
    # regular bail application", silently dropped Prayer and Verification, and
    # invented a "Relevant legal provisions" section into which it dumped the
    # retrieved statutes verbatim in English. The result reads well and is not
    # a filing — no cause title, no prayer, no verification.
    #
    # Section-pair rule 2 already says "USE THE EXACT HEADING TEXT GIVEN".
    # Bug 1 established that a prompt rule alone does not hold, so this is a
    # deterministic check with a bounded repair rather than more prompt text.
    _all_missing = _missing_planned_sections(draft, sections)
    missing = [s for s in _all_missing if _is_mandatory_section(s)]
    _conditional = [s for s in _all_missing if s not in missing]
    if _conditional:
        # Expected and correct — the facts gave these nothing to say. Logged
        # at info so the planned-vs-emitted gap is explainable in the logs
        # without looking like a defect.
        log.info(
            "Conditional sections omitted (expected)",
            omitted=[s.id for s in _conditional],
        )
    if missing:
        log.warning(
            "Writer dropped MANDATORY sections",
            missing=[s.id for s in missing],
            planned=total, emitted=total - len(_all_missing),
        )
        # Repair in pairs, cheapest-first, and only while the request budget
        # allows. A full regeneration would cost another ~150s against a 300s
        # ceiling, so repair is capped rather than unbounded.
        from core.deadline import remaining as _remaining
        repaired: list[str] = []
        for j in range(0, min(len(missing), _MAX_REPAIR_SECTIONS), 2):
            if _remaining() is not None and _remaining() < _REPAIR_MIN_BUDGET_S:
                log.warning("Skipping section repair — request budget exhausted",
                            remaining_s=_remaining())
                break
            chunk = missing[j:j + 2]
            try:
                text = await _generate_section_pair(
                    sections_to_write=chunk,
                    section_position_start=total - len(missing) + j + 1,
                    total_sections=total,
                    query=query,
                    user_facts=user_facts,
                    reference_draft=reference_draft,
                    prior_text=draft,
                    gathered_context=gathered_context,
                    user_intent=user_intent,
                    user_language=user_language,
                    review_and_redraft_mode=review_and_redraft_mode,
                )
                if text.strip():
                    repaired.append(text.strip())
                    log.info("Section repair succeeded",
                             headings=[s.heading[:40] for s in chunk])
            except Exception as e:
                log.warning("Section repair failed", error=short_err(e),
                            headings=[s.heading[:40] for s in chunk])
        if repaired:
            # SAFETY NET (2026-09-06): before blind-appending, drop any
            # repaired chunk whose substantive body is already present in
            # the draft. Belt-and-suspenders against a future false
            # positive from `_missing_planned_sections` (that function
            # was tightened in the same commit, but the append is the
            # last line of defence — a duplicate never lands even if the
            # detector is later loosened by accident). The check compares
            # normalised heading-shingles between the repaired chunk and
            # the existing draft; ~85% word overlap on the chunk's own
            # words with the existing draft's headings means we already
            # have this section.
            deduped_repaired: list[str] = []
            existing_headings_norm = [
                _normalise_heading(line.strip())
                for line in draft.splitlines()
                if line.strip().startswith("#")
                or (line.strip().startswith("**") and line.strip().endswith("**"))
            ]
            for chunk_text in repaired:
                chunk_first_heading = ""
                for line in chunk_text.splitlines():
                    s = line.strip()
                    if s.startswith("#") or (s.startswith("**") and s.endswith("**")):
                        chunk_first_heading = _normalise_heading(s)
                        break
                already_present = bool(chunk_first_heading) and any(
                    _substantive_word_overlap(chunk_first_heading, got, min_ratio=0.85)
                    or chunk_first_heading in got
                    or got in chunk_first_heading
                    for got in existing_headings_norm
                )
                if already_present:
                    log.warning(
                        "Repaired section already present in draft — skipping "
                        "append to prevent duplicate section fragment",
                        chunk_heading_preview=chunk_first_heading[:80],
                        chunk_chars=len(chunk_text),
                    )
                    continue
                deduped_repaired.append(chunk_text)
            if deduped_repaired:
                # Insert each repaired section at its PLANNED position rather than
                # appending. Appending put a regenerated "Statement of Facts"
                # after the Prayer and Verification - advocates 2026-09-08
                # reported "sections up and down". The plan is ordered and
                # `missing` preserves that order, so the anchor is the next
                # planned section that DID reach the draft.
                draft = _insert_repaired_in_plan_order(
                    draft, sections, missing, deduped_repaired,
                )
            still_missing = _missing_planned_sections(draft, sections)
            log.info("Plan adherence after repair",
                     recovered=len(missing) - len(still_missing),
                     still_missing=[s.heading[:40] for s in still_missing],
                     appended_chunks=len(deduped_repaired),
                     deduped_chunks=len(repaired) - len(deduped_repaired))

    # G-24: emit draft_incomplete SSE + prepend a banner when any pair
    # failed. The frontend already listens for draft_incomplete (see
    # chat_runner.py handler); the banner is belt-and-suspenders so
    # users who paste the raw markdown see the note too.
    if failed_pairs:
        failed_headings: list[str] = []
        failed_sections_meta: list[dict] = []
        for fp in failed_pairs:
            for idx, heading in enumerate(fp["headings"]):
                section_position = fp["position_start"] + idx
                failed_headings.append(heading)
                failed_sections_meta.append({
                    "position": section_position,
                    "heading": heading,
                    "reason": fp["reason"],
                })
        try:
            from langgraph.config import get_stream_writer as _gsw
            _sw = _gsw()
            _sw({
                "type": "draft_incomplete",
                "failed_sections": failed_sections_meta,
                "total_sections": total,
                "completed_sections": total - len(failed_sections_meta),
            })
        except (RuntimeError, ImportError):
            # Not in streaming context (batch endpoint) — SSE event not applicable.
            pass

        _banner_names = ", ".join(f"'{h}'" for h in failed_headings[:4])
        if len(failed_headings) > 4:
            _banner_names += f", and {len(failed_headings) - 4} more"
        banner = (
            "> ⚠ **Draft incomplete** — "
            f"{len(failed_sections_meta)} of {total} section(s) "
            f"could not be generated ({_banner_names}). "
            "Please re-send your prompt to retry.\n\n"
        )
        draft = banner + draft
        log.warning(
            "Sectionwise draft incomplete",
            failed=len(failed_sections_meta), total=total,
            failed_positions=[fp["position_start"] for fp in failed_pairs],
        )

    return draft


# ---------------------------------------------------------------------------
# Fast-path generator — Level 2 of the follow-up simplification.
#
# One Gemini 2.5 Pro call, no ES lookup, no fan-out judge, no self-refine.
# The prior draft flows verbatim into the prompt as the FORMAT / FACT / IDENTITY
# anchor; the user's directive tells the model what to change. Everything else
# is preserved. See config/prompts.DRAFTING_MODIFICATION_PROMPT for the rules.
# ---------------------------------------------------------------------------

async def _generate_draft_modification(
    *,
    user_directive: str,
    prior_draft: str,
    user_intent,
    user_language: str,
    user_facts: str,
    progress_emit,
) -> str:
    """One-shot 'modify the prior draft per this directive' Pro call.

    Falls back to empty string on any failure — the caller (drafting_node)
    detects an empty result and routes through the full pipeline instead,
    so the fast-path is always a strict improvement over today.
    """
    from config.prompts import DRAFTING_MODIFICATION_PROMPT
    from core.language import _format_intent_directives, localize_prompt
    from langchain_core.messages import SystemMessage, HumanMessage

    intent_directives_block = (
        _format_intent_directives(user_intent) if user_intent is not None else ""
    ) or "(no explicit user directives — apply the directive as literally as stated)"

    new_source_block = (
        user_facts.strip() if user_facts and user_facts.strip()
        else "(no new files uploaded this turn — modify the EXISTING DRAFT alone)"
    )

    # Order matters: format() FIRST (fills our known placeholders on the raw
    # template), localize_prompt() SECOND (appends language + intent directive
    # blocks that may contain literal { characters we don't want format() to
    # see). Swapping the order can throw KeyError on the localized text.
    system_prompt = DRAFTING_MODIFICATION_PROMPT.format(
        existing_draft=prior_draft,
        user_directive=user_directive.strip(),
        intent_directives_block=intent_directives_block,
        new_source_documents=new_source_block,
    )
    system_prompt = localize_prompt(system_prompt, user_language, user_intent)

    llm = get_drafting_llm(
        max_output_tokens=24000,
        thinking_budget=4096,
    )

    progress_emit(
        "drafting",
        "Modifying your prior draft...",
        step="modify",
    )

    try:
        with log_time(log, "Draft modification (fast path)"):
            response = await asyncio.to_thread(
                llm.invoke,
                [SystemMessage(content=cacheable_system(system_prompt)),
                 HumanMessage(content="Produce the complete modified draft now.")],
            )
    except Exception as e:
        log.error("Draft modification LLM call failed",
                  error=short_err(e), exc_info=True)
        return ""

    from core.token_tracker import record as _record_tokens
    _record_tokens("Drafting", "modify_draft", response)

    text = getattr(response, "text", "") or ""

    if not text:
        meta = getattr(response, "response_metadata", None) or {}
        reason = str(meta.get("finish_reason") or "").upper()
        log.warning(
            "Draft modification produced empty content",
            finish_reason=reason or "unknown",
        )
        return ""

    log.info(
        "Draft modification succeeded",
        prior_len=len(prior_draft), modified_len=len(text),
    )
    return text


async def _generate_draft(
    query: str,
    user_facts: str,
    reference_draft: str,
    user_intent,
    user_language: str,
    progress_emit,
    gathered_context: dict[str, str] | None = None,
    review_and_redraft_mode: bool = False,
    chat_history=None,
    english_query: str = "",
) -> str:
    """Thin dispatcher: judge call decides single-pass vs section-wise.

    `user_facts` is the RAW extracted text of uploaded documents, passed
    verbatim through to whichever generation strategy runs. Returns a
    single assembled string regardless of the strategy.

    Before dispatching, this fires the niche selector once (Gemini Flash
    Lite) to identify the filing niche (bail, writ, plaint, rejoinder,
    NI Act notice, etc.) — the resolved overlay is threaded into both
    the single-pass and section-wise paths so overlay statutory anchors
    and skeleton conventions travel with every generation call. Selector
    failure returns no overlay; the base senior-counsel prompt still
    produces a competent draft.
    """
    # Niche selection — cheap, one Flash Lite call, no fallback cost.
    # Run BEFORE the preflight so the selector's telemetry is captured
    # even for oversized-upload rejections (helps analytics see niche
    # distribution across attempted requests, not just successful ones).
    niche_overlay = ""
    try:
        from agents.drafting_niche import pick_drafting_niche
        from config.drafting_niches import get_niche_overlay
        niche_key = await pick_drafting_niche(query, user_facts)
        niche_overlay = get_niche_overlay(niche_key)
        log.info(
            "Drafting niche resolved",
            niche_key=niche_key or "none",
            overlay_chars=len(niche_overlay),
        )
    except Exception as e:
        log.warning(
            "Niche selection failed; proceeding with base prompt only",
            error=short_err(e),
        )

    # Preflight: Gemini 2.5 Pro caps input at 1,048,576 tokens. English
    # text tokenises at ~4 chars/token so the raw-char ceiling is ~4M
    # chars, and 3.5M leaves room for system prompt + reference + context
    # + query. Every dense Indic script (Devanagari, Bengali, Tamil,
    # Telugu, Kannada, Malayalam, Gujarati, Gurmukhi, Odia) tokenises at
    # ~2.5 chars/token, so the effective ceiling drops to ~2.4M chars
    # for those uploads. Earlier we only detected Devanagari; a 3M-char
    # Kannada PDF would sail through the guard and 400 at generation
    # time. When we exceed the applicable budget, short-circuit here
    # with a user-actionable message instead.
    _USER_FACTS_BUDGET_LATIN = 3_500_000
    _USER_FACTS_BUDGET_INDIC = 2_400_000
    # Unicode ranges for the dense Indic scripts we protect against.
    # (script name, start-char, end-char)  — inclusive at both ends.
    _INDIC_RANGES = (
        ("devanagari", "ऀ", "ॿ"),  # Hindi, Marathi, Sanskrit
        ("bengali",    "ঀ", "৿"),  # Bengali, Assamese
        ("gurmukhi",   "਀", "੿"),  # Punjabi
        ("gujarati",   "઀", "૿"),
        ("oriya",      "଀", "୿"),
        ("tamil",      "஀", "௿"),
        ("telugu",     "ఀ", "౿"),
        ("kannada",    "ಀ", "೿"),
        ("malayalam",  "ഀ", "ൿ"),
    )
    budget = _USER_FACTS_BUDGET_LATIN
    detected_script = "latin"
    if user_facts:
        sample = user_facts[:20_000]
        sample_len = max(len(sample), 1)
        for name, lo, hi in _INDIC_RANGES:
            count = sum(1 for c in sample if lo <= c <= hi)
            if count / sample_len > 0.3:
                budget = _USER_FACTS_BUDGET_INDIC
                detected_script = name
                break
    if user_facts and len(user_facts) > budget:
        log.warning(
            "Drafting user_facts exceeds token budget — returning friendly message",
            facts_chars=len(user_facts),
            budget=budget,
            script=detected_script,
        )
        return (
            "The uploaded documents are too large to draft from in a "
            "single response — they exceed the model's context limit. "
            "Please narrow the drafting task to a specific section (for "
            "example, \"draft a reply to paragraph 3 of the notice\"), "
            "or upload smaller or fewer documents so I can process them "
            "properly."
        )

    # Plan against ENGLISH when the user wrote in a regional language.
    #
    # The judge decides how many sections the document gets. Fed a
    # regional-script query it consistently plans FEWER sections than the
    # same request in English — measured 6 vs 8 for an identical bail
    # request, deterministic across runs — which is roughly a quarter of
    # the document silently dropped (Undertakings, advocate block, parity
    # and medical grounds collapsed into one).
    #
    # `english_query` is the translation `_acquire_reference_draft` already
    # computed for the English-only corpus lookup, so this costs no extra
    # LLM call. `user_language` is still passed through unchanged, so the
    # judge continues to emit headings in the user's script — only the
    # PLANNING becomes language-invariant.
    strategy = await _judge_fanout(
        query=english_query or query,
        reference_draft=reference_draft,
        user_language=user_language,
        user_intent=user_intent,
        chat_history=chat_history,
    )

    # Diagnostic: splits the regional-language section deficit into its three
    # possible causes. Some languages deliver 4-6 sections where English
    # reliably delivers 9, and the fix differs per cause:
    #   english_query_is_ascii False -> translation failed for this language
    #   is_ascii True but planned low -> the judge is biased even in English
    #   planned high but emitted low  -> sections dropped during writing
    # Pair this with the `draft_done` line at the end of drafting_node.
    _plan_q = english_query or query
    # The judge mirrors the REFERENCE TEMPLATE's structure, and each language
    # retrieves a different template (translation wording differs, so ES
    # returns different documents). Log the template's own section count so a
    # low plan can be attributed to the template rather than the language or
    # the model. `ref_*` counts several conventions because corpus templates
    # are CSV-derived and do not use markdown headings.
    _ref = reference_draft or ""
    log.info(
        "draft_plan",
        user_language=user_language,
        english_query_head=_plan_q[:60],
        english_query_is_ascii=_plan_q.isascii(),
        planned_sections=len(strategy.sections),
        ref_chars=len(_ref),
        ref_numbered=len(re.findall(r"^\s*\d{1,2}[\.\)]\s+\S", _ref, re.M)),
        ref_allcaps_lines=len(re.findall(r"^[A-Z][A-Z \-:/,\.]{6,}$", _ref, re.M)),
        planned_headings=[s.heading[:24] for s in strategy.sections],
    )

    # Code-level safety ceiling. The plan SIZE is the judge's decision and must
    # not be normalised to a fixed number, but a mis-behaving judge emitting 40
    # sections would blow the request budget, so the count is bounded here as
    # well as in the prompt. Both read MAX_SECTIONS.
    #
    # KEEP THE FIRST N-1 AND THE LAST, never a plain `[:N]` head slice. Every
    # document this pipeline drafts ends with a structurally required closing
    # block — prayer, verification, signature / execution, schedule — and it is
    # always LAST in the plan, so a head slice deletes precisely the section the
    # document cannot be delivered without.
    #
    # Measured on a commercial-MOU request (2026-09-02): the judge planned 13
    # sections, `[:12]` dropped `SIGNATURES`, and the draft was delivered ending
    # after its governing-law clause with no execution block, no banner and no
    # error — silent on every run. The section-repair pass below does not cover
    # this case either: its mandatory list is pleading-shaped, so a missing
    # `Execution` is never restored. Middle sections are the safe thing to drop.
    if strategy.sections and len(strategy.sections) > MAX_SECTIONS:
        _dropped = strategy.sections[MAX_SECTIONS - 1:-1]
        log.warning(
            "Fan-out judge emitted more sections than the ceiling — trimming",
            emitted=len(strategy.sections), max_sections=MAX_SECTIONS,
            kept_last=strategy.sections[-1].heading[:40],
            dropped=[s.heading[:32] for s in _dropped],
        )
        strategy.sections = (
            strategy.sections[:MAX_SECTIONS - 1] + strategy.sections[-1:]
        )

    if not strategy.should_fanout or not strategy.sections:
        log.info(
            "Drafting: single-pass selected",
            should_fanout=strategy.should_fanout,
            section_count=len(strategy.sections),
            reasoning=strategy.reasoning[:200],
        )
        return await _generate_single_pass(
            query=query,
            user_facts=user_facts,
            reference_draft=reference_draft,
            user_intent=user_intent,
            user_language=user_language,
            progress_emit=progress_emit,
            gathered_context=gathered_context,
            review_and_redraft_mode=review_and_redraft_mode,
            niche_overlay=niche_overlay,
        )

    log.info(
        "Drafting: section-wise selected",
        sections=len(strategy.sections),
        reasoning=strategy.reasoning[:200],
    )
    progress_emit(
        "drafting",
        f"Drafting {len(strategy.sections)} sections one by one...",
        step="generate",
    )
    draft = await _generate_sectionwise(
        sections=strategy.sections,
        query=query,
        user_facts=user_facts,
        reference_draft=reference_draft,
        user_intent=user_intent,
        user_language=user_language,
        progress_emit=progress_emit,
        gathered_context=gathered_context,
        review_and_redraft_mode=review_and_redraft_mode,
        niche_overlay=niche_overlay,
    )

    # --- Off-target language gate ------------------------------------------
    #
    # 4 of 42 regional drafts came back wholly or largely in English despite a
    # regional request — two at a script ratio of 0.00, not one character of
    # the requested script. A client who asks in Kannada and receives English
    # is the same complaint that started this work.
    #
    # Deterministic, not routed through the critic: the critic was measured
    # defaulting to "pass" on exactly these drafts. Recorded as an explicit
    # exception under CLAUDE.md drafting invariant 2.
    #
    # Regeneration is budget-gated. A full redraft costs roughly as much as
    # the first, and the request ceiling is 300s — so we retry only when the
    # budget genuinely allows, and otherwise ship the draft with a visible
    # banner. A banner is a bad outcome; a gateway timeout returning nothing
    # is a worse one.
    if user_language and user_language != "en" and is_off_target_language(draft, user_language):
        ratio = output_script_ratio(draft, user_language)
        from core.deadline import remaining as _remaining
        budget = _remaining()
        log.warning(
            "Draft came back off-target language",
            user_language=user_language, script_ratio=round(ratio, 2),
            remaining_s=budget,
        )
        if budget is None or budget > _OFF_TARGET_RETRY_MIN_BUDGET_S:
            progress_emit(
                "drafting",
                "Draft came back in the wrong language — regenerating...",
                substep=True, step="generate",
            )
            retry = await _generate_sectionwise(
                sections=strategy.sections,
                query=query,
                user_facts=user_facts,
                reference_draft=reference_draft,
                user_intent=user_intent,
                user_language=user_language,
                progress_emit=progress_emit,
                gathered_context=gathered_context,
                review_and_redraft_mode=review_and_redraft_mode,
            )
            retry_ratio = output_script_ratio(retry, user_language)
            log.info("Off-target regeneration finished",
                     before=round(ratio, 2), after=round(retry_ratio, 2),
                     accepted=retry_ratio > ratio)
            # Keep whichever is closer to the requested language. A retry that
            # comes back English too is no improvement, and the first draft at
            # least had the sections.
            if retry.strip() and retry_ratio > ratio:
                return retry
            draft = retry if retry_ratio > ratio else draft
            ratio = max(ratio, retry_ratio)

        if is_off_target_language(draft, user_language):
            lang_name = language_name(user_language)
            draft = (
                f"> ⚠ **This draft came back in English rather than {lang_name}.** "
                "Please re-send your prompt to retry.\n\n"
            ) + draft

    return draft


# ---------------------------------------------------------------------------
# Agent node — wired into the LangGraph multi-agent system.
# ---------------------------------------------------------------------------

async def drafting_node(state: LegalAgentState) -> dict:
    """Simplified legal-draft generation pipeline.

    No enums, no skeletons, no per-section fan-out, no mandatory injection.

    See `docs/drafting_simplification_plan.md` for the full design.
    """
    # --- 1. Read state ---
    agent_queries = state.get("agent_queries", {})
    query = (
        agent_queries.get("Drafting")
        or state.get("query")
        or state.get("original_query", "")
    )
    original_query = state.get("original_query", query)
    user_context = state.get("user_context", "")
    user_language = state.get("user_language", "en")
    intent_obj = state.get("user_intent")
    integration_ctx = IntegrationContextData.from_state(state)
    fc = FileContextData.from_state(state)
    # Last few chat turns feed the fan-out judge so it can detect
    # polish/redraft follow-ups and stay single-pass (Bug #9 fix).
    _chat_history = state.get("chat_history") or []
    _judge_chat_history = _chat_history[-4:] if _chat_history else []

    # --- 2. Build user_facts blob from attachments / pasted context / integrations ---
    #
    # Source priority for uploaded-document facts:
    #
    #   1. ``fc.extracted_texts`` — the raw per-file text PyMuPDF/python-docx
    #      produced before chunking. Carries the *whole* document, has no
    #      Chroma dependency, and survives the Chroma pool-exhaustion failure
    #      mode (2026-06-28 incident) where ``chromadb_collections`` ends up
    #      empty. Drafting wants every fact in the document — semantic-search
    #      chunks lose information by design — so the raw text is preferred
    #      even when Chroma is healthy.
    #
    #   2. ``get_full_attachment`` over ``chromadb_collections`` — used only
    #      as a fallback when no raw text was retained (e.g. legacy state
    #      written before extracted_texts was added, or future file types
    #      that route directly to Chroma without staging through
    #      ``pf.extracted_text``).
    #
    # Per-file resolution is tracked in ``seen_names`` so we never duplicate
    # a document's text into the prompt when both paths happen to surface it.
    fact_blocks: list[str] = []
    seen_names: set[str] = set()
    facts_source = "none"   # "extracted" | "chroma" | "mixed" | "none"

    if fc and fc.extracted_texts:
        for entry in fc.extracted_texts:
            text = (entry.get("text") or "").strip()
            name = entry.get("name") or "attached"
            if not text:
                continue
            fact_blocks.append(f"[Uploaded document — {name}]\n{text}")
            seen_names.add(name)
        if fact_blocks:
            facts_source = "extracted"

    if fc and fc.chromadb_collections:
        chroma_added = False
        from tools.shared.vectordb_tools import get_full_attachment
        for cid in fc.chromadb_collections:
            try:
                attached = get_full_attachment.invoke({"collection_id": cid})
                full_text = (attached or {}).get("full_text", "")
                source_file = (attached or {}).get("source_file") or "attached"
                if full_text and source_file not in seen_names:
                    fact_blocks.append(
                        f"[Uploaded document — {source_file}]\n{full_text}"
                    )
                    seen_names.add(source_file)
                    chroma_added = True
            except Exception as e:
                log.warning("get_full_attachment failed",
                            collection=cid, error=str(e))
        if chroma_added:
            facts_source = "mixed" if facts_source == "extracted" else "chroma"

    if user_context:
        # No truncation: CLAUDE.md drafting invariant #3 + feedback_preserve_user_query
        # require the user's pasted context to flow verbatim into generation. The
        # earlier `[:30000]` slice silently dropped material past 30 KB — a common
        # failure mode on multi-affidavit uploads. Aggregate budget is enforced
        # later by the preflight in _generate_draft.
        fact_blocks.append(f"[Pasted context]\n{user_context}")
    if integration_ctx and integration_ctx.has_content:
        fact_blocks.append(integration_ctx.as_prompt_prefix().rstrip())
    user_facts = "\n\n".join(fact_blocks)

    # G-27: pre-drafting injection classifier. Off by default; enable
    # with INJECTION_CHECK_ENABLED=1 in prod once the false-positive
    # rate has been characterised. When ENABLED and the classifier
    # returns high-confidence injection, refuse to draft — return a
    # scoped error message rather than shipping an attacker-influenced
    # document.
    from core.injection_check import _enabled as _injection_enabled
    if _injection_enabled():
        from core.injection_check import check_injection, sample_chat_history
        chat_sample = sample_chat_history(_chat_history)
        verdict = await check_injection(chat_sample, user_facts)
        if verdict.is_injection and verdict.confidence >= 0.7:
            log.warning(
                "Injection classifier blocked drafting",
                confidence=round(verdict.confidence, 2),
                reason=verdict.reason,
            )
            result = AgentResult(
                agent_name="Drafting",
                content=(
                    "I'm unable to draft this document because the "
                    "provided source or chat history contains "
                    "instructions that appear to attempt to override "
                    "my guidelines. If this is a false positive, "
                    "please rephrase your request or remove any "
                    "instruction-shaped text from the uploaded "
                    "source before retrying."
                ),
                sources=[],
                tokens_consumed=0,
                error=f"injection_blocked: {verdict.reason}",
            )
            return {"agent_results": {"Drafting": result}}

    log.info(
        "Agent started",
        query=query[:100],
        has_user_context=bool(user_context),
        has_file_context=bool(fc and (fc.chromadb_collections or fc.extracted_texts)),
        attachment_collections=len(fc.chromadb_collections) if fc else 0,
        extracted_text_files=len(fc.extracted_texts) if fc else 0,
        facts_source=facts_source,
        has_integration_context=bool(integration_ctx and integration_ctx.has_content),
        facts_chars=len(user_facts),
        using_agent_query="Drafting" in agent_queries,
    )

    # --- 3. Acquire concurrency slot; emit queue_status SSE if at capacity ---
    try:
        from langgraph.config import get_stream_writer as _get_writer
        _dwriter = _get_writer()
    except (RuntimeError, ImportError):
        _dwriter = None
    # Try to acquire without blocking. If we succeed instantly, we
    # never had to queue and no queue_status event is emitted. If not,
    # emit the "queued" event AND start a periodic heartbeat so the
    # frontend knows the request is still alive while waiting.
    # `.locked()` alone is racy — two simultaneous arrivals when one
    # slot is free both see `not locked()`, one blocks silently. The
    # semaphore's own `_value` check via `try/wait_for(0)` is precise.
    _AGENT_QUEUE_HEARTBEAT_S = 15
    try:
        await asyncio.wait_for(_AGENT_SEMAPHORE.acquire(), timeout=0.05)
    except asyncio.TimeoutError:
        log.warning("Concurrency limit reached, queuing Drafting request")
        if _dwriter:
            _dwriter({"type": "queue_status", "status": "queued",
                      "message": "Drafting agent is busy, queuing your request..."})

        async def _emit_queue_heartbeat():
            waited = 0
            while True:
                await asyncio.sleep(_AGENT_QUEUE_HEARTBEAT_S)
                waited += _AGENT_QUEUE_HEARTBEAT_S
                if _dwriter:
                    try:
                        _dwriter({
                            "type": "queue_status",
                            "status": "waiting",
                            "waited_seconds": waited,
                            "message": f"Still queued — waited {waited}s so far...",
                        })
                    except Exception:
                        return

        _heartbeat_task = asyncio.create_task(_emit_queue_heartbeat())
        try:
            await _AGENT_SEMAPHORE.acquire()
        finally:
            _heartbeat_task.cancel()
            try:
                await _heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass
        if _dwriter:
            _dwriter({"type": "queue_status", "status": "acquired",
                      "message": "Drafting slot available — starting now."})

    try:
        # --- 3.5 FAST PATH: drafting follow-up directive on a prior draft ---
        #
        # Level 2 of docs/followup_pipeline_simplification_plan.md. When Turn N
        # is a SHORT directive follow-up on a PRIOR drafting turn, skip the
        # entire ~9-call reference/context/judge/section/refine pipeline and
        # run ONE Gemini Pro call that modifies the prior draft in place.
        # Reads previous_artifact_kind + previous_artifact_content populated
        # by memory_node from Level 1's SQLite storage.
        #
        # Guarded on DRAFTING_FOLLOWUP_FAST_PATH=1 (off during initial rollout).
        # Uses `original_query` — the raw user directive — so per-agent
        # rewriting or memory rewriter expansion can't hide "in Marathi" from
        # detection. On empty fast-path output falls through to the existing
        # pipeline unchanged.
        _prev_kind = state.get("previous_artifact_kind", "") or ""
        _prev_content = state.get("previous_artifact_content", "") or ""
        _new_upload_this_turn = bool(fc and fc.has_content)
        if (
            _fast_path_enabled()
            and _is_drafting_followup_directive(
                original_query, _prev_kind, _prev_content, _new_upload_this_turn,
            )
        ):
            log.info(
                "Fast path: drafting follow-up directive detected",
                directive_preview=original_query[:80],
                prior_len=len(_prev_content),
            )
            fast_draft = await _generate_draft_modification(
                user_directive=original_query,
                prior_draft=_prev_content,
                user_intent=intent_obj,
                user_language=user_language,
                user_facts=user_facts,
                progress_emit=progress,
            )
            if fast_draft.strip():
                # Cleanup pass — same validator the slow path uses so mojibake
                # / stray HTML / [CITE:] placeholders never reach the user.
                # self_refine is deliberately SKIPPED — the prior draft
                # already passed self_refine on its own turn; re-running it
                # here reliably triggers destructive-shrink guards (see
                # core/self_refine.py:1709) or wholesale rewrites.
                fast_draft, fast_warnings = validate_draft(fast_draft)
                log.info(
                    "Fast path succeeded — bypassing full pipeline",
                    modified_len=len(fast_draft),
                )
                _fast_result = AgentResult(
                    agent_name="Drafting",
                    content=fast_draft,
                    sources=[SourceMetadata(
                        source_type="drafting",
                        title="Modified from your previous draft",
                        content=[_prev_content[:300]],
                        file_name="<prior_turn:modification>",
                        agent_name="Drafting",
                        template_type="prior_turn_modification",
                    )],
                    tokens_consumed=0,
                    meta=(
                        {"draft_warnings": fast_warnings,
                         "reference_kind": "prior_turn"}
                        if fast_warnings
                        else {"reference_kind": "prior_turn"}
                    ),
                )
                return {"agent_results": {"Drafting": _fast_result}}
            else:
                log.warning(
                    "Fast path returned empty content; falling through to full pipeline"
                )
                # Fall through to the existing pipeline — no regression.

        # --- 4. Reference draft acquisition + relevant-context gather.
        #
        # Two branches:
        #
        #   (a) REVIEW-AND-REDRAFT of an uploaded document: the uploaded
        #       document IS the reference the user wants preserved. Skip the
        #       ES picker + web fallback (both discard the format the user
        #       already showed us — see _is_review_and_redraft_of_upload).
        #       Only gather relevant legal context (statutes + precedents).
        #
        #   (b) Fresh draft (or upload without review verbs): existing
        #       parallel gather — ES picker → web fallback for reference,
        #       plus context retrieval.
        #
        # No case-fact extraction step. The raw `user_facts` blob (verbatim
        # extracted text of every uploaded document) flows straight into
        # generation — Gemini 2.5 Pro's 2M-token window can consume
        # multi-100K-char PDFs, and the raw source is required for tasks
        # like rejoinder / para-wise reply where the model must walk the
        # source paragraph-by-paragraph.
        use_upload_as_ref = _is_review_and_redraft_of_upload(
            original_query, user_facts
        )
        if use_upload_as_ref:
            # Restore the raw user prompt for downstream generation. The
            # per-agent rewriter compresses "Review the attached word document
            # and Find out every legal error from the application and redraft
            # with removing all legal error and with most relevant and
            # landmark case laws of supreme court" into a topic-loose
            # "Redraft legal document to remove all legal errors, incorporating
            # relevant Supreme Court landmark cases", which the fanout judge
            # and section-writer LLM cannot anchor to the actual matter
            # (Section 290 BNSS plea bargaining / MV Act 185 in the smoke).
            # The result was a generic contract-analysis memorandum even
            # though the reference draft was correctly set to the uploaded
            # DOCX. Per feedback_preserve_user_query: never lose information
            # from the user's prompt anywhere in the pipeline.
            if query != original_query:
                log.info(
                    "Review-and-redraft mode: restoring raw prompt for downstream",
                    rewritten_len=len(query), raw_len=len(original_query),
                )
                query = original_query
            log.info(
                "Review-and-redraft mode: uploaded document is the reference",
                query_len=len(original_query), user_facts_chars=len(user_facts),
            )
            progress(
                "drafting",
                "Using uploaded document as the reference (review-and-redraft mode)",
                step="reference",
            )
            reference_text = user_facts
            reference_source = "<uploaded:review_and_redraft>"
            reference_kind = "uploaded"
            # No corpus lookup happened on this path, so there is no English
            # translation to plan against; the judge falls back to `query`.
            english_query = ""
            gathered_ctx = await _gather_relevant_context(query)
        elif _INLINED_PRIOR_DRAFT_MARKER in query:
            # (c) Directive follow-up whose query already carries the full
            #     prior draft (inlined by agents/memory.py). The prior draft
            #     IS the source document — skip the ES picker and its web
            #     fallback entirely. Still gather relevant legal context so
            #     statutes/precedents remain available to the generator, and
            #     still fall through to the normal generation + self_refine
            #     flow below (this branch does NOT set use_upload_as_ref).
            log.info(
                "Prior draft inlined in query — skipping reference acquisition",
                query_chars=len(query),
            )
            progress(
                "drafting",
                "Using your previous draft as the source document",
                step="reference",
            )
            reference_text = ""
            reference_source = "<prior_turn:inlined>"
            reference_kind = "prior_turn"
            english_query = ""
            gathered_ctx = await _gather_relevant_context(query)
        else:
            progress("drafting", "Searching templates and relevant law...",
                     step="reference")
            (reference_text, reference_source, reference_kind,
             english_query), gathered_ctx = \
                await asyncio.gather(
                    _acquire_reference_draft(
                        query,
                        progress,
                        user_language=user_language,
                        intent=intent_obj,
                        original_query=original_query,
                    ),
                    _gather_relevant_context(query),
                )
        if gathered_ctx:
            progress(
                "drafting",
                f"Gathered legal context: {', '.join(gathered_ctx.keys())}",
                substep=True, step="reference",
                found=len(gathered_ctx),
            )

        # --- 5. Generation (Gemini 2.5 Pro) with raw source + gathered context.
        # The dispatcher picks single-pass or per-section fan-out based on
        # `_judge_fanout`. Raw `user_facts` is threaded through both paths.
        #
        # `use_upload_as_ref` propagates to the generators so the reference
        # block wording flips to "PRESERVE these values verbatim" instead of
        # the default "IGNORE these values (they belong to a different
        # matter)". Without this flip, the section writer receives the
        # uploaded doc twice (once as reference, once as source) with
        # contradictory directives and falls back to a generic template.
        draft = await _generate_draft(
            query=query,
            user_facts=user_facts,
            reference_draft=reference_text,
            user_intent=intent_obj,
            user_language=user_language,
            progress_emit=progress,
            gathered_context=gathered_ctx,
            review_and_redraft_mode=use_upload_as_ref,
            chat_history=_judge_chat_history,
            english_query=english_query,
        )

        # --- 6. Mechanical cleanup (mojibake, HTML strip, [CITE:] strip) ---
        progress("drafting", "Cleaning up draft...", step="cleanup")
        draft, draft_warnings = validate_draft(draft)

        # Old section number bolted to a new act name — "Section 439 BNSS".
        # 439 is the CrPC bail provision; the BNSS counterpart is 483, so
        # "439 BNSS" cites a provision that exists in neither code. The
        # retrieved newacts row for CrPC 439 states its own counterpart, so
        # the correction comes from the source material, not from us.
        #
        # Corrected in place rather than flagged with a banner: a draft is
        # copied into a filing, where a caution at the top is lost and a
        # wrong section number is not. Only the digits move — the act the
        # writer named stays, because which era governs turns on the offence
        # date and that is the advocate's call.
        try:
            from core.statute_citation_check import (
                correct_cross_pairs, log_era_mismatch,
            )
            _newacts_ctx = (gathered_ctx or {}).get("newacts", "")
            draft, _xpairs = correct_cross_pairs(draft, _newacts_ctx)
            if _xpairs:
                draft_warnings = list(draft_warnings or []) + [
                    f"Corrected {c['cited']} to {c['correct']}"
                    for c in _xpairs
                ]
            # Era mismatch — BNS charges pleaded under the CrPC — is measured
            # here and repaired by the critic's `statute_era_mismatch`
            # category, not rewritten in place. "439 CrPC" is a real
            # provision that a draft may name legitimately (a pre-2024
            # judgment, a proceeding begun before commencement), and telling
            # that apart from stale recall needs the surrounding argument.
            # Logging it before self_refine gives a per-draft number for
            # whether the prompt rule and the critic actually hold.
            log_era_mismatch(draft, where="pre_refine")
        except Exception as e:
            log.warning("Statute citation checks failed; draft unchanged",
                        error=short_err(e))

        # A citation the pipeline cannot vouch for has to be visible IN the
        # document, not only in response metadata the UI may never render.
        # The advocate is the last check before filing; a judgment dated in
        # the future is one they must see. Stripped signals (DB IDs,
        # placeholder markers) need no banner — removing them is the whole
        # fix — but a suspect authority that REMAINS in the text does.
        # Strict citation whitelisting — SHADOW BY DEFAULT. Computes and logs
        # which citations are not traceable to retrieval, and changes nothing
        # unless CITATION_STRIP_MODE=enforce. Enforce must not be switched on
        # until advocate review of production shadow logs classifies the
        # would-be strips as fabrication rather than retrieval gaps; the SCI
        # index is already known to return no hits for most bail queries, so
        # enforcing today would delete real authorities.
        try:
            from core.citation_whitelist import apply_citation_policy
            draft, _cite_audit = apply_citation_policy(draft, gathered_ctx)
            if _cite_audit.get("ungrounded"):
                draft_warnings.append(
                    f"[{_cite_audit['mode']}] "
                    f"{len(_cite_audit['ungrounded'])} case citation(s) not "
                    f"traceable to retrieved sources"
                )
        except Exception as _cite_err:
            log.warning("Citation whitelist audit failed; draft unchanged",
                        error=short_err(_cite_err))

        _unverifiable = [w for w in draft_warnings
                         if w.startswith("UNVERIFIABLE CITATION")]
        if _unverifiable:
            draft = (
                "> ⚠ **Verify the case law before filing.** "
                + _unverifiable[0].replace("UNVERIFIABLE CITATION: ", "")
                + "\n\n"
            ) + draft
            log.warning("Draft carries an unverifiable citation",
                        detail=_unverifiable[0][:160])

        # --- 7. self_refine — the scope critic + audit pass against UserIntent ---
        #
        # Skipped in review-and-redraft mode. The critic is calibrated against
        # a "fresh draft produced from scratch" — it expects things like a
        # full landmark-precedent block, standard prayer/verification wording,
        # or specific procedural blocks the user's uploaded document may
        # legitimately have omitted. When the user's task is "review my
        # existing document and correct the legal errors", the critic
        # reliably fires 3+ violations against the correct section-writer
        # output, and the refiner then wholesale rewrites the corrected draft
        # into a generic template (observed on smoke: Master Services
        # Agreement / Contract-Analysis Memorandum replacing the Section 290
        # BNSS plea-bargaining redraft that the section writer had produced
        # correctly — confirmed via SECTION_PAIR_PEEK diagnostic).
        #
        # Long-term the critic prompt should gain a review-and-redraft mode
        # branch that trusts the uploaded document's shape; for now the
        # cleanest fix is to short-circuit the loop.
        if draft and intent_obj is not None and not use_upload_as_ref:
            try:
                progress(
                    "drafting",
                    "Auditing draft against your directives...",
                    step="self_refine",
                )
                source_langs = detect_source_languages(user_facts)
                # Thread the retrieved sources through. Without this the
                # critic's `unretrieved_citation` category — the one that
                # catches fabricated case citations — is skipped outright,
                # because self_refine substitutes "(none — the caller passed
                # no source registry; skip ... for this call)". Drafting has
                # never passed one, so hallucinated authorities in generated
                # filings have never been checked.
                # SHADOW-MODE SAFETY. Handing the registry to self_refine
                # arms the critic's `unretrieved_citation` category, and the
                # REFINER acts on whatever the critic raises — so passing it
                # here would strip citations through a second code path even
                # with CITATION_STRIP_MODE=shadow. Shadow must mean shadow on
                # every path, not just the one that says "shadow" in its name.
                #
                # In shadow/off the registry is withheld, self_refine
                # substitutes its "(none — skip unretrieved_citation)" marker,
                # and behaviour is byte-identical to before this work.
                from core.citation_whitelist import ENFORCE as _CITE_ENFORCE
                from core.citation_whitelist import strip_mode as _cite_mode
                _registry = (getattr(gathered_ctx, "registry", None)
                             if _cite_mode() == _CITE_ENFORCE else None)
                if _registry is None:
                    log.info("Citation whitelist withheld from self_refine "
                             "(shadow/off) — critic cannot strip citations",
                             citation_strip_mode=_cite_mode())
                refined_draft, refine_history = await self_refine(
                    draft,
                    user_query=query,
                    intent=intent_obj,
                    critic_llm=get_drafting_llm(max_output_tokens=8192),
                    refiner_llm=get_drafting_llm(max_output_tokens=24000),
                    source_languages=source_langs,
                    source_registry=_registry,
                    # A finished draft has no business getting materially
                    # shorter. The shared default (0.7) exists for
                    # conversational answers that can legitimately compress on
                    # a format fix; applied to a filing it let a 13% loss ship
                    # silently. 0.90 still leaves room for the shrink that IS
                    # correct — stripping a fabricated citation or a duplicated
                    # paragraph costs 1-2% of a 20K draft, not 13%.
                    shrink_floor=0.90,
                    cumulative_shrink_floor=0.85,
                )
                # Did the critic's `statute_era_mismatch` category actually
                # repair it? Same check, after the refiner, so the pair of
                # log lines answers that per draft instead of by impression.
                try:
                    from core.statute_citation_check import log_era_mismatch
                    log_era_mismatch(refined_draft, where="post_refine")
                except Exception:
                    pass
                if refined_draft != draft:
                    log.info(
                        "Self-refine altered draft",
                        iterations=len(refine_history),
                        original_len=len(draft),
                        refined_len=len(refined_draft),
                    )
                    # The refiner must never change the LANGUAGE of a draft.
                    #
                    # Measured (Odia, req 65f6dfd1): the generator produced a
                    # 9,740-char draft in Odia — the generation-time script
                    # gate passed it — and self-refine then rewrote it to
                    # 12,411 chars of English across 2 iterations. The final
                    # output contained not one Odia character. This is the
                    # same failure class as the doc-type flip: the critic
                    # raises violations and the refiner "fixes" them by
                    # producing a document the user did not ask for.
                    #
                    # Reverting is the right remedy rather than regenerating:
                    # we already hold a correct draft in the requested
                    # language, and it cost ~150s to make. The refinement's
                    # improvements are forfeited, which is the cheaper loss.
                    if (
                        user_language
                        and user_language != "en"
                        and is_off_target_language(refined_draft, user_language)
                        and not is_off_target_language(draft, user_language)
                    ):
                        log.warning(
                            "Self-refine changed the draft language — reverting",
                            user_language=user_language,
                            before=round(output_script_ratio(draft, user_language), 2),
                            after=round(output_script_ratio(refined_draft, user_language), 2),
                            iterations=len(refine_history),
                        )
                    else:
                        draft = refined_draft
            except Exception as refine_err:
                log.warning("Self-refine skipped due to error",
                            error=str(refine_err))
        elif use_upload_as_ref:
            log.info(
                "Review-and-redraft mode: self_refine skipped "
                "(critic reliably rewrites correct redrafts into generic templates)"
            )

        # --- 9. Build AgentResult with source attribution ---
        if reference_kind == "es":
            template_display = os.path.splitext(os.path.basename(reference_source))[0]
            sources = [SourceMetadata(
                source_type="drafting",
                title=template_display,
                content=[reference_text[:300]],
                file_name=reference_source,
                agent_name="Drafting",
                template_type=template_display,
            )]
        elif reference_kind == "uploaded":
            sources = [SourceMetadata(
                source_type="drafting",
                title="Reference draft (uploaded document — review-and-redraft mode)",
                content=[reference_text[:300]] if reference_text else [],
                file_name=reference_source,
                agent_name="Drafting",
                template_type="uploaded",
            )]
        else:
            sources = [SourceMetadata(
                source_type="drafting",
                title=f"Reference draft (web-synthesised: {reference_source})",
                content=[reference_text[:300]] if reference_text else [],
                file_name=reference_source,
                agent_name="Drafting",
                template_type="web-synthesized",
            )]

        # Pairs with the `draft_plan` line above. `emitted_h2` counts SECTION
        # headings only (`##`); `###` is a sub-heading inside a section and
        # inflated an earlier version of this measurement.
        log.info(
            "draft_done",
            user_language=user_language,
            emitted_h2=len(re.findall(r"^##\s+\S", draft, re.M)),
            emitted_h3=len(re.findall(r"^###\s+\S", draft, re.M)),
            draft_words=len(draft.split()),
        )

        log.info(
            "Agent completed -- simplified pipeline",
            reference_kind=reference_kind,
            reference_source=reference_source[:120],
            draft_len=len(draft),
        )

        result = AgentResult(
            agent_name="Drafting",
            content=draft,
            sources=sources,
            tokens_consumed=0,
            fallback_used=(reference_kind == "web"),
            meta=(
                {"draft_warnings": draft_warnings, "reference_kind": reference_kind}
                if draft_warnings else {"reference_kind": reference_kind}
            ),
        )
        state_update = {"agent_results": {"Drafting": result}}

    except Exception as e:
        from core.metrics import record_agent_error
        record_agent_error("Drafting", e)
        log.error("Agent failed", error=short_err(e), exc_info=True)
        result = AgentResult(
            agent_name="Drafting",
            content="",
            sources=[],
            tokens_consumed=0,
            error=short_err(e),
        )
        state_update = {"agent_results": {"Drafting": result}}
    finally:
        _AGENT_SEMAPHORE.release()

    return state_update
