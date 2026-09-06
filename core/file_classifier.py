"""Gap #3 — File-nature classifier at ingest.

Runs ONE Gemini Flash Lite call per uploaded file (right after text
extraction, before Chroma writes) to classify the file into one of a
small controlled taxonomy of Indian-legal document kinds.

The classification enables:
  1. Structured orchestrator routing hint — instead of a magic string
     "This query is about the uploaded document(s)", the file-hint
     now carries `[FIR + notice]` etc. so the classifier LLM has real
     signal to reason with.
  2. Per-kind specialised system prompts in Document agent (rebuilds
     the deleted _SPECIALIZED_PROMPTS dispatch, intent-driven).
  3. Per-agent context hints (e.g. Newacts sees "attached: FIR" and
     knows to map facts → sections; Judgment sees "attached: court
     order" and knows to look for citing cases).

Taxonomy (10 categories + "other"):
    judgment        SC / HC / District court judgment
    court_order     Interim orders, injunctions, non-judgment court output
    fir             First Information Report
    pleading        Plaint, written statement, application, petition
    notice          Legal notice under §138 NI Act, demand notice, etc.
    affidavit       Sworn statement
    contract        Agreement, MOU, deed, lease
    correspondence  Email, letter, reply, memo
    statute         User-pasted BNS/IPC/act text
    other           Fallback

Design goals:
  * Cheap — Flash Lite (~200ms + ~500 tokens per file).
  * Deterministic across identical text — temperature=0, structured output.
  * Fail-open — on any error (LLM timeout, empty text), returns "other".
    Downstream must not depend on non-"other" for correctness.
  * Text sample capped at 3000 chars so Flash Lite context stays snappy.
    Head of the document is usually the most identifying part (cause title
    for judgments, "First Information Report" header for FIRs, etc.).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from core.clients import get_gemini_flash
from core.logger import get_logger, log_time, short_err

log = get_logger("FileClassifier")


FileKind = Literal[
    "judgment",
    "court_order",
    "fir",
    "pleading",
    "notice",
    "affidavit",
    "contract",
    "correspondence",
    "statute",
    "other",
]


ALL_KINDS: tuple[str, ...] = (
    "judgment", "court_order", "fir", "pleading", "notice",
    "affidavit", "contract", "correspondence", "statute", "other",
)


# Cap on the text sample handed to the classifier. Head-of-document
# usually carries the identifying signal (cause title, form header,
# document type keyword) so 3000 chars is enough for reliable
# classification without paying for long-tail tokens.
_SAMPLE_CHARS = 3000


class _FileKindOutput(BaseModel):
    kind: FileKind = Field(
        ...,
        description="The single best-matching document kind from the taxonomy.",
    )
    confidence: float = Field(
        0.5, ge=0.0, le=1.0,
        description="Self-reported confidence in [0.0, 1.0]. Below 0.5 is "
                    "treated as low confidence — downstream may fall back "
                    "to 'other' or ignore the classification.",
    )


_CLASSIFIER_PROMPT = """You are an Indian-legal document classifier. Read the
sample text below (the first {sample_chars} chars of an uploaded file named
"{filename}") and classify it into EXACTLY ONE of these kinds:

  judgment        SC / HC / District court judgment (has parties, bench,
                  citation, ratio, order). Typical markers: "IN THE HIGH
                  COURT OF...", "IN THE SUPREME COURT OF INDIA",
                  "JUDGMENT", "ORDER", "coram", "per J.".
  court_order     Interim order, injunction order, procedural order, notice
                  ISSUED BY a court (bail order, stay, remand, warrant).
                  Distinguishable from judgment by absence of full ratio.
  fir             First Information Report (police report). Typical markers:
                  "FIRST INFORMATION REPORT", "F.I.R. No.", police station,
                  section numbers listed at top, cognizable-offence phrasing.
  pleading        Plaint, written statement, petition, application (filed
                  BY a party WITH the court). Typical markers: "IN THE COURT
                  OF...", "PLAINT", "WRITTEN STATEMENT", "APPLICATION UNDER
                  SECTION...", numbered paragraphs, prayer clause, verification.
  notice          Legal notice under §138 NI Act, demand notice, eviction
                  notice, notice to reply, reply-to-notice. Typical markers:
                  "LEGAL NOTICE", "TAKE NOTICE THAT", "under Section 138",
                  advocate letterhead, addressee's address at top.
  affidavit       Sworn statement (usually attached to a pleading). Typical
                  markers: "AFFIDAVIT", "I, [name] son of [name], aged
                  [age] years, R/o [address], do hereby solemnly affirm
                  and state as under".
  contract        Agreement, MOU, deed of sale, lease, partnership,
                  employment, service agreement. Typical markers:
                  "AGREEMENT", "DEED OF...", "PARTY OF THE FIRST PART",
                  "hereinafter referred to as", clause structure with
                  headings, signature blocks for multiple parties.
  correspondence  Email, business letter, memo, reply, non-legal-notice
                  communication. Typical markers: "Subject:", "Dear",
                  "Yours faithfully", short paragraphs, no legal
                  clauses / prayer.
  statute         Pasted section text from an act (BNS, IPC, CrPC, BNSS,
                  IEA, BSA, NI Act, IBC, GST Act, etc.). Typical markers:
                  "Section X.", followed by numbered sub-sections (1),
                  (2), provisos, explanations. NO parties, NO forum.
  other           Anything that clearly doesn't fit above (receipts, bills,
                  invoices, ID cards, marksheets, handwritten notes,
                  scanned photos of unrelated content, spreadsheets of
                  data, blank / garbled OCR output).

Rules:
  * Pick the SINGLE most representative kind. If a file could be two, pick
    the STRUCTURAL kind (e.g. "affidavit filed with a plaint" -> pleading
    if the plaint is the primary content; affidavit only if the whole file
    is the affidavit).
  * Prefer "other" over guessing — false positives here mislead downstream
    routing.
  * Confidence < 0.4 -> return "other".

Sample text follows. Return ONLY the structured output.

--- SAMPLE ---
{sample}
--- END SAMPLE ---
"""


def classify_file_kind(text: str, filename: str = "") -> tuple[str, float]:
    """Classify a file's text into one of the FileKind values.

    Args:
        text: Extracted text from the file. Only the first _SAMPLE_CHARS
            chars are sent to the LLM.
        filename: Original filename (used as a weak hint in the prompt).

    Returns:
        (kind, confidence). Kind is always one of ALL_KINDS; falls back
        to "other" on any error, empty text, or low-confidence output.
    """
    text = (text or "").strip()
    if not text:
        log.debug("File classifier: empty text -> other", filename=filename)
        return "other", 0.0

    sample = text[:_SAMPLE_CHARS]

    try:
        with log_time(log, "File-kind classification", filename=filename):
            llm = get_gemini_flash(temperature=0.0).with_structured_output(
                _FileKindOutput, include_raw=True,
            )
            prompt_text = _CLASSIFIER_PROMPT.format(
                sample_chars=_SAMPLE_CHARS,
                filename=filename or "(unnamed)",
                sample=sample,
            )
            raw_and_parsed = llm.invoke(prompt_text)

        from core.token_tracker import record as _record_tokens
        _record_tokens("FileClassifier", "classify_kind", raw_and_parsed.get("raw"))

        parsed = raw_and_parsed.get("parsed")
        if parsed is None:
            log.warning("File classifier: LLM returned no parsed output",
                        filename=filename)
            return "other", 0.0

        kind = parsed.kind
        confidence = float(parsed.confidence or 0.0)
        # Guard: coerce anything unexpected to "other"
        if kind not in ALL_KINDS:
            log.warning("File classifier: unknown kind returned",
                        filename=filename, raw_kind=kind)
            return "other", confidence
        if confidence < 0.4 and kind != "other":
            log.debug("File classifier: low confidence -> other",
                      filename=filename, raw_kind=kind, confidence=confidence)
            return "other", confidence

        log.info("File classified", filename=filename, kind=kind,
                 confidence=confidence)
        return kind, confidence

    except Exception as e:
        log.warning("File classifier failed — returning 'other'",
                    filename=filename, error=short_err(e))
        return "other", 0.0
