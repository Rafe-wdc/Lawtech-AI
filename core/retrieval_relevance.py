"""Shared retrieval-relevance gate for legal RAG agents.

Used by Legislation, Judgment, Newacts, and Constitution/Maxim agents to
verify that BM25/hybrid-retrieved hits actually answer the user's query
BEFORE feeding them to the response LLM.

WHY THIS MODULE EXISTS
======================
BM25 (and hybrid BM25+vector) retrieval can return documents that share
rare phrases with the query but discuss them in unrelated contexts. Two
production examples observed for the same injunction-draft prompt:

  Run 1: Telangana Municipalities Act sections that PROHIBIT injunctions
         for electoral-roll proceedings (verbatim mentions "Code of Civil
         Procedure, 1908" and "temporary injunction" — high BM25 score).

  Run 2: Civil Liability for Nuclear Damage Act, 2010 (incidentally mentions
         "Order XXXIX of the CPC" in the context of preventing evasion of
         award payments).

Both retrievals were near-identical in BGE-large embedding similarity to
the actual CPC Order XXXIX text (~0.73 vs ~0.74) — bi-encoder cosine
cannot reliably separate them because it captures topical overlap, not
subject-matter direction (grants vs prohibits).

The fix follows LexRAG (Li et al., Feb 2025) and the 2025 legal-RAG
literature: a small structured-output LLM call directly judges whether
retrieved chunks answer the query. The LLM CAN reason about direction.
On a "not relevant" verdict, callers fall back to web search rather than
ship wrong-corpus citations.

DESIGN
======
- Agent-agnostic API — pass already-extracted chunk text (str), not ES
  hit dicts, so all four agents can use the same helper without coupling
  to their varied hit formats.
- Single-pass LLM-as-judge using Gemini Flash Lite at temperature=0 with
  structured output. ~500 tokens, ~3s warm. On error: fail-open (let the
  downstream apology guard catch it) so an LLM hiccup doesn't break the
  agent.
- Apology guard is shared too — scans head + tail of the response, so
  hedges added at the end (the common LLM pattern) don't slip through.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from core.clients import get_gemini_flash_full
from core.logger import get_logger, log_time

log = get_logger("RetrievalGate")


# --- Tunables (env-driven for runtime adjustment without code change) ---

# Per-chunk floor and ceiling for the adaptive judge window.
# `_RELEVANCE_CHUNK_CHARS` is kept as a name (imported by tests) but is
# now the FLOOR, not a hard limit. See `_adaptive_chunk_window` below.
_RELEVANCE_CHUNK_CHARS = 1200
_RELEVANCE_CHUNK_CHARS_MAX = 6000
_RELEVANCE_TOTAL_CHARS_BUDGET = 12000
# Number of top hits the judge sees. Raised from 3 to 10: a multi-section
# query ("compare BNS 115, 118, 189, 190, 191, 351, 352") produces 7+ hits
# and the judge was rejecting them because it only saw the first 3
# (sections 115, 118, 189) and concluded the others were "missing". Newacts
# now also short-circuits the gate entirely on exact-filter queries, but
# bumping the cap is defense in depth for Legislation / Constitution / Maxim.
_RELEVANCE_TOP_N = 10
_JUDGE_TIMEOUT_SEC = 15.0


# --- Domain hints (per-agent overlays on the universal decision procedure) ---
#
# The main judge prompt now runs the UNIVERSAL filters (jurisdiction,
# statute, direction, temporal, forum) for every agent. Each domain hint
# below is a consistent 3-block overlay — ACCEPT WHEN / REJECT WHEN /
# COMMON FALSE POSITIVES — that adds agent-specific rules on top of the
# universal ones. Keeping the shape identical across agents prevents
# drift (the Bombay-vs-Jharkhand bug was drift: the Judgment hint had a
# jurisdiction clause and the Legislation hint didn't).

_DOMAIN_HINTS: dict[str, str] = {
    "Legislation": (
        "Retrieval target: statutory provisions (sections / rules / orders) "
        "of central or state legislation, or High Court / tribunal rules, "
        "that govern the user's scenario.\n\n"
        "ACCEPT WHEN\n"
        "- Retrieved sections come from the statute the user named (or the "
        "  statute that governs the user's scenario when the query is issue-"
        "  based, not section-based) AND from the correct jurisdictional "
        "  scope (central law: always in scope; state / High-Court / tribunal "
        "  law: only from the state or forum the user named).\n"
        "- Retrieved sections address the same LEGAL EFFECT the user asked "
        "  about (grant vs bar, entitlement vs prohibition, procedure vs "
        "  substantive right).\n\n"
        "REJECT WHEN\n"
        "- User named a specific COURT / TRIBUNAL / STATE and the hits are "
        "  the corresponding rules of a DIFFERENT forum (Bombay HC query → "
        "  Jharkhand HC Rules; Delhi HC query → Karnataka HC Rules; NCLT "
        "  Mumbai query → NCLT Chennai practice directions; Maharashtra "
        "  land-rev query → Karnataka Land Revenue Code).\n"
        "- User named a specific ACT and hits are a DIFFERENT act that "
        "  happens to share the section number (BNS §318 query → §318 of a "
        "  different code).\n"
        "- Hits discuss the same topic but in the OPPOSITE DIRECTION (query "
        "  about granting an injunction → provision that PROHIBITS "
        "  injunctions for an unrelated proceeding).\n\n"
        "COMMON FALSE POSITIVES (from prod)\n"
        "- HC Rules of a different HC surfacing because they cross-reference "
        "  §482 CrPC / §528 BNSS.\n"
        "- Municipalities / local-body / tax acts matched only because they "
        "  mention \"temporary injunction\" or \"Order XXXIX\" in passing.\n"
        "- Old-law (CrPC / IPC / IEA) provisions surfaced for a new-law "
        "  (BNSS / BNS / BSA) query without cross-mapping — accept only if "
        "  the hit substantively discusses the corresponding current "
        "  provision or the transitional rule."
    ),
    "Judgment": (
        "Retrieval target: decisions of Indian courts (typically High "
        "Courts, sometimes tribunals) on the legal question the user is "
        "asking about.\n\n"
        "ACCEPT WHEN\n"
        "- Case materially discusses the user's cited section, doctrine, "
        "  or legal issue — even if the case ALSO arose under other "
        "  provisions. Example: a §302 IPC murder appeal with substantive "
        "  §313 CrPC reasoning IS on-topic for a \"§313 CrPC case law\" "
        "  query.\n"
        "- Case is from a court that BINDS the jurisdiction the user "
        "  named. Supreme Court decisions always bind, so an SC judgment "
        "  satisfies any HC-jurisdiction query.\n\n"
        "REJECT WHEN\n"
        "- User named a specific COURT and the case is from a DIFFERENT "
        "  High Court or tribunal (Bombay HC query → Kerala HC case).\n"
        "- Case shares only a party surname, judge name, or a rare keyword "
        "  with the query but decides an unrelated dispute (BM25 / fuzzy "
        "  matching noise).\n"
        "- Case cites the user's section only in incidental boilerplate "
        "  (operative-order recital, not a discussed issue).\n"
        "- User asked about POST-amendment law and the case is a pre-"
        "  amendment decision that is no longer good law (accept only if "
        "  the case survives the amendment or the query is about "
        "  legislative history).\n\n"
        "COMMON FALSE POSITIVES\n"
        "- ES fuzzy matching surfaces cases where a party's surname "
        "  coincides with a query word (\"Kumar\", \"Sharma\", \"State\").\n"
        "- Cases that mention the section number in the cause-title or "
        "  order recital but never reason about it."
    ),
    "SCI_Judgment": (
        "Retrieval target: Supreme Court of India decisions on the user's "
        "legal question. SC binds all courts — jurisdiction is NEVER a "
        "reason to reject an SC judgment for an HC-scoped query.\n\n"
        "ACCEPT WHEN\n"
        "- SC case materially discusses the user's cited section, "
        "  doctrine, or issue.\n"
        "- Case is a landmark / leading authority even if older "
        "  (e.g. Bhajan Lal 1992 for quashing categories).\n\n"
        "REJECT WHEN\n"
        "- Case surfaces only because of fuzzy full-text overlap "
        "  (multi-word party name inflates BM25; judge surname coincides "
        "  with a query word).\n"
        "- Case is factually unrelated and mentions the user's section "
        "  only in incidental cross-reference.\n"
        "- User is asking about a SPECIFIC SC judgment by name / year / "
        "  citation, and the hit is a DIFFERENT case that shares words.\n\n"
        "COMMON FALSE POSITIVES\n"
        "- Multi-word party names inflating BM25 on unrelated matters.\n"
        "- Cases about the same act but a completely different section."
    ),
    "Newacts": (
        "Retrieval target: sections of the post-2024 codes (BNS, BNSS, "
        "BSA) or their old-law counterparts (IPC, CrPC, IEA).\n\n"
        "ACCEPT WHEN\n"
        "(1) Specific-section lookup (\"§138 NI Act\", \"BNS §115\") — "
        "    chunk must be the exact section from the exact act.\n"
        "(2) Topic / chapter lookup (\"right of private defence\", "
        "    \"general exceptions\", \"culpable homicide\", \"unlawful "
        "    assembly\", \"criminal conspiracy\", \"abetment\", \"theft "
        "    and extortion\", \"offences against property\") — hits are "
        "    EXPECTED to be a batch of consecutive sections covering the "
        "    chapter (BNS §34-44 for private defence, IPC §76-106 for "
        "    general exceptions). Accept when chunks address the named "
        "    doctrine even if the user didn't enumerate section numbers.\n"
        "(3) Cross-act comparison (\"compare X under BNS vs IPC\", \"BNS "
        "    equivalent of §379 IPC\") — hits from EITHER act OR both are "
        "    relevant. Do not reject just because hits come from one act "
        "    when the query named both.\n\n"
        "REJECT WHEN\n"
        "- Retrieved sections genuinely address a different subject "
        "  (user asked about cheque dishonour but retrieval returned "
        "  theft sections).\n"
        "- Section-number collision across acts (user named BNS §318, "
        "  hits are §318 of a different code).\n"
        "- Hits are pre-amendment provisions offered as CURRENT law for a "
        "  post-2024 query without any cross-mapping.\n\n"
        "DO NOT REJECT FOR\n"
        "- \"User didn't name these specific sections\" — that is EXPECTED "
        "  for topic / doctrine queries. Accept if chunks address the "
        "  doctrine."
    ),
    "Constitution": (
        "Retrieval target: Articles of the Constitution of India that "
        "materially address the user's question.\n\n"
        "ACCEPT WHEN\n"
        "- Retrieved Article(s) are the operative provisions governing the "
        "  scenario (Art. 226 for HC writ jurisdiction, Art. 32 for SC "
        "  writ jurisdiction, Art. 21 for procedural due process, etc.).\n\n"
        "REJECT WHEN\n"
        "- Article is cited only incidentally in an unrelated provision or "
        "  case (Art. 14 boilerplate in a taxation section).\n"
        "- User named a specific Article and the hits are a different "
        "  Article entirely.\n\n"
        "COMMON FALSE POSITIVES\n"
        "- Article 14 and Article 21 surface for almost any query because "
        "  of their broad citation. Accept only if the retrieved chunk "
        "  actually reasons about them."
    ),
    "Maxim": (
        "Retrieval target: legal maxims whose meaning and use directly "
        "answer the user's question.\n\n"
        "ACCEPT WHEN\n"
        "- Maxim's definition and typical use directly bear on the user's "
        "  scenario.\n\n"
        "REJECT WHEN\n"
        "- Hit merely shares a Latin / English keyword with the query but "
        "  is a different maxim (\"audi alteram partem\" retrieved for a "
        "  query about \"alter ego doctrine\").\n\n"
        "COMMON FALSE POSITIVES\n"
        "- Fuzzy Latin-word matches surfacing unrelated maxims."
    ),
    "Document": (
        "Retrieval target: passages from the user's UPLOADED document(s) "
        "responsive to the user's question. Retrieval is MMR with k=30 "
        "and ALWAYS returns 30 chunks regardless of similarity — the gate "
        "MUST be strict because unrelated chunks still fill the response.\n\n"
        "ACCEPT WHEN\n"
        "- Chunks contain the specific information answering the "
        "  question, even if the uploaded file's overall genre differs "
        "  from the framing (e.g. user uploaded a contract and asked "
        "  about a specific indemnity clause — accept if the clause is "
        "  in the chunks).\n\n"
        "REJECT WHEN\n"
        "- Chunks are about a different subject than the question "
        "  (user uploaded a lease and asked about §138 NI Act — the "
        "  lease has nothing on cheque dishonour).\n"
        "- Chunks are OCR-noise, boilerplate footers/headers, cover pages, "
        "  or blank scans without substantive responsive content."
    ),
}


# --- Public response type from the judge ---

class _RelevanceJudgment(BaseModel):
    """Structured verdict from the LLM-as-judge relevance gate."""
    relevant: bool = Field(
        ...,
        description=(
            "True only if the provided documents DIRECTLY answer the user's "
            "query — i.e. they govern the same legal scenario, not merely "
            "a different scenario that happens to mention the same statute, "
            "section number, party, or share rare phrases."
        ),
    )
    reason: str = Field(
        "",
        description="One-sentence explanation of the verdict.",
    )


_RELEVANCE_JUDGE_PROMPT = """You are a strict legal-retrieval relevance judge for an Indian legal
research system. Your job is to block false-positive retrievals BEFORE
they reach the answer LLM. When in doubt, REJECT — the system will run
a web-search fallback that is strictly better than shipping wrong-
jurisdiction, wrong-statute, or wrong-direction citations.

===============================================================
USER QUERY
===============================================================
{query}

===============================================================
RETRIEVED DOCUMENTS   (top {n_chunks} hits, source: "{source}")
===============================================================
{chunks_block}

===============================================================
AGENT-SPECIFIC RETRIEVAL EXPECTATIONS
===============================================================
{domain_hint}

===============================================================
DECISION PROCEDURE — apply in order
===============================================================

STEP 0 — HARD SOURCE-NAME GATE (fires before anything else).

Before reading the chunks, look at the SOURCE metadata above
(`source: "..."`) and compare it to the user's query.

If the source name identifies a SPECIFIC forum (a specific High
Court's Rules, a specific tribunal's practice directions, a
specific state's local act, a specific district court's manual)
AND the user's query names a DIFFERENT specific forum, IMMEDIATELY
return `relevant=false` with `reason` starting `[wrong_jurisdiction]`.
Do NOT proceed to Step 1. The chunk content may look topically
relevant because it references the correct central statute (e.g.
§482 CrPC / §528 BNSS) — but the RULES / PRACTICE surrounding it
are forum-bound and the user asked about a different forum.

Concrete triggers for this hard gate:
- source contains "High Court of X Rules" and user named a
  different High Court.
- source contains "NCLT [City]" or "NCLAT [Bench]" and user named
  a different bench.
- source contains a state name (e.g. "Karnataka Land Revenue",
  "Telangana Municipalities") and user named a different state.
- source is a district-court manual and user named a different
  district / state.

Exceptions where this gate does NOT fire:
- Source is a CENTRAL statute (BNS, BNSS, BSA, IPC, CrPC, CPC, NI
  Act, GST Act, IT Act, etc.) — central law binds all jurisdictions,
  so proceed to Step 1.
- Source is a Supreme Court judgment — SC binds all courts, proceed.
- User's query did NOT name a specific forum — no jurisdiction
  anchor to violate, proceed.

STEP 1 — EXTRACT the user's ANCHORS from the query.
An anchor is a constraint the retrieval must satisfy. Extract only the
ones the user actually named; do NOT infer anchors the user did not
name.

  (a) SUBJECT-MATTER anchor — the legal issue (e.g. "quashing of
      criminal complaint", "temporary injunction for property",
      "anticipatory bail", "cheque dishonour").
  (b) JURISDICTION anchor — a specific court / tribunal / state, if
      named (e.g. "Bombay High Court", "NCLT Mumbai", "Karnataka HC",
      "Delhi HC", "Madras HC"). If the user names one, retrieved docs
      MUST come from that jurisdiction or from a source that BINDS it
      (Supreme Court judgments and central statutes bind all HCs).
  (c) STATUTE anchor — the specific act, if named (e.g. "BNS", "CrPC",
      "IPC", "CPC", "NI Act", "GST Act", "MV Act"). Same section
      number in a DIFFERENT act is NOT the same anchor.
  (d) SECTION / RULE / ARTICLE anchor, if named.
  (e) DIRECTION anchor — is the user asking about GRANT / PERMISSION /
      ENTITLEMENT (positive) or about BAR / PROHIBITION / RESTRICTION
      (negative)? Same-topic provisions with the OPPOSITE direction
      are NOT relevant.
  (f) TEMPORAL anchor — does the query reference CURRENT law (BNS /
      BNSS / BSA, post-2024) or OLD law (IPC / CrPC / IEA), or is it
      neutral? A repealed provision offered as if current, or vice
      versa without a cross-map, is NOT relevant.
  (g) FORUM anchor — civil vs criminal, original vs appellate,
      writ vs statutory appeal, if the query makes this clear.

STEP 2 — SCORE each retrieved chunk against the extracted anchors.

  A chunk is NOT RELEVANT if it violates any anchor the user named:
    - anchors any of the SUBJECT-MATTER anchors superficially but
      binds a DIFFERENT jurisdiction (violates 1b);
    - shares the section number but is a DIFFERENT statute (violates
      1c);
    - discusses the same topic in the OPPOSITE direction (violates
      1e);
    - is a repealed / superseded provision offered as if current with
      no cross-map (violates 1f);
    - is from the WRONG forum (violates 1g);
    - merely MENTIONS the user's keywords in an unrelated context —
      incidental cross-reference, boilerplate cite, party-name
      coincidence, cause-title recital without reasoning.

  A chunk IS RELEVANT if it directly ADDRESSES the user's ask AND
  respects every named anchor, even if it also covers collateral
  topics.

STEP 3 — AGGREGATE across the top hits.

  - If NO top hit passes Step 2, return relevant=false.
  - If AT LEAST ONE top hit passes Step 2 and it directly answers the
    ask, return relevant=true.
  - Partial retrievals (right statute, only some sub-clauses match)
    lean toward relevant=true — the generation LLM can work with them.
  - Chapter / doctrine queries expect a BATCH of consecutive sections;
    do not reject just because the user did not enumerate specific
    section numbers.

STEP 4 — COMPOSE the `reason` field.

  When relevant=false, PREFIX the reason with one of the following
  bracketed rejection tags so telemetry can aggregate failure modes:

    [wrong_jurisdiction] — user named a court / tribunal / state and
                           hits bind a different one.
    [wrong_statute]      — same section number, different act.
    [wrong_direction]    — same topic but opposite legal effect
                           (bars vs grants, prohibits vs permits).
    [wrong_temporal]     — repealed / pre-amendment provision offered
                           as current, or vice versa, with no cross-map.
    [wrong_forum]        — civil provision for a criminal query, or
                           original-jurisdiction rule for an appellate
                           query, etc.
    [wrong_topic]        — keyword overlap only; hits govern an
                           unrelated legal scenario.
    [incidental_mention] — user's terms appear only in passing —
                           cross-reference, boilerplate, cause-title
                           recital, party-name coincidence.
    [no_anchor_match]    — none of the above; hits fail to address
                           any of the user's anchors.

  When relevant=true, name the anchors the hits matched (e.g.
  "matches BNSS §528 quashing procedure with Bombay HC writ
  jurisdiction preserved").

===============================================================
CANONICAL REJECTION EXAMPLES (calibrated across prod incidents)
===============================================================

- Query "Bombay High Court quashing procedure" → hit is "High Court
  of Jharkhand Rules, 2001" (mentions §482 CrPC in Rule 35). Reject
  as [wrong_jurisdiction] — user named a specific HC and the hit is
  a different HC's rules.

- Query "CPC Order XXXIX injunction for property" → hit is a state
  Municipalities Act that PROHIBITS injunctions for electoral-roll
  proceedings. Reject as [wrong_direction] — opposite legal effect.

- Query "BNS Section 318 cheating" → hit is "Section 318" of a
  different act (e.g. Motor Vehicles Act, Companies Act). Reject as
  [wrong_statute] — same number, different statute.

- Query "2019 SC judgment on anticipatory bail" → hit is an
  unrelated 2015 High Court case sharing a party name. Reject as
  [wrong_jurisdiction] or [incidental_mention].

- Query "how anticipatory bail is GRANTED" → hit is a POCSO-style
  provision that BARS anticipatory bail. Reject as [wrong_direction].

- Query "current position under BNSS on FIR quashing" → hit is only
  the repealed CrPC §482 with no BNSS §528 discussion or transitional
  note. Reject as [wrong_temporal].

- Query "Section 138 NI Act" but hit chunk is a lease-agreement PDF
  passage (uploaded document) with no cheque discussion. Reject as
  [wrong_topic].

- Query "Delhi HC bail cases" → hit is a Bombay HC bail judgment.
  Reject as [wrong_jurisdiction] — but ONLY for Judgment agent.
  Legislation from central acts is jurisdictionally in-scope. SC
  judgments always bind and are always in-scope.

===============================================================
NON-REJECTION EXAMPLES (do NOT over-reject)
===============================================================

- Query "Section 313 CrPC case law" → SC judgment where the appeal
  arose under §302 IPC but the reasoning substantively discusses
  §313 CrPC. Accept — the section is a DISCUSSED issue, not
  boilerplate.

- Query "right of private defence" (topic, no sections named) →
  hits are BNS §34-44 (or IPC §96-106) — the chapter batch for
  private defence. Accept — chapter / doctrine queries expect batch
  retrieval.

- Query "compare cheating under BNS vs IPC" → hits are only BNS
  §318. Accept — cross-act comparison queries do not require both
  acts in retrieval; the LLM can supply the counterpart.

===============================================================
OUTPUT
===============================================================
Return your structured verdict. Follow the reason-tag convention
above so downstream telemetry can categorize misses. Bias toward
REJECTION when unsure — web fallback is preferable to wrong-corpus
citations.
"""


# --- Helpers ---

def _adaptive_chunk_window(n_chunks: int) -> int:
    """Per-chunk char budget scaled to hit count.

    Motivation: a single filtered hit (e.g. Legislation's exact
    section-number lookup returning one long definitions block) needs
    a large window so the judge can see the requested sub-clause,
    which often lives past char 1200. Multi-section retrievals stay
    at today's 1200-char default so total judge input stays bounded.

    Returns min=1200, max=6000. Total input to the judge is capped at
    ~12000 chars (n_chunks * per_chunk).
    """
    if n_chunks <= 0:
        return _RELEVANCE_CHUNK_CHARS
    return min(
        _RELEVANCE_CHUNK_CHARS_MAX,
        max(_RELEVANCE_CHUNK_CHARS, _RELEVANCE_TOTAL_CHARS_BUDGET // n_chunks),
    )


def _build_chunks_block(chunks: list[str]) -> tuple[str, int]:
    """Render top chunks as a numbered block for the judge prompt.

    Chunk window sizes adaptively — see `_adaptive_chunk_window`.
    """
    non_empty = [c for c in chunks[:_RELEVANCE_TOP_N] if c]
    per_chunk = _adaptive_chunk_window(len(non_empty))
    parts: list[str] = []
    for i, c in enumerate(non_empty, 1):
        text = c[:per_chunk].strip()
        if text:
            parts.append(f"[Doc {i}]\n{text}")
    return "\n\n".join(parts), len(parts)


async def check_retrieval_relevance(
    query: str,
    chunks: list[str],
    source_name: Optional[str],
    agent_name: str,
) -> tuple[bool, dict]:
    """Public entry point: should the caller proceed with these chunks?

    Args:
        query: User's question — passed in full to the judge.
        chunks: Already-extracted page_content strings from top retrieval
                hits. Pass the same order ES returned. Empty strings/None
                are tolerated; only the first _RELEVANCE_TOP_N non-empty
                entries are evaluated.
        source_name: Display name of the source the hits came from (e.g. an
                ES index entry's `source` field). Used only for log context
                and the judge prompt. None is fine.
        agent_name: Caller agent ("Legislation", "Judgment", "Newacts",
                "Constitution", "Maxim") — used for token-tracking
                attribution and to pick the right domain hint.

    Returns:
        (is_relevant, telemetry) where telemetry has keys:
            judge_relevant, reason. Always populated so logs are consistent.

    On any internal error: fail-open (return True). The agent's apology
    guard downstream is the second line of defense — we never want to
    block legitimate retrievals because of a transient LLM outage.
    """
    telemetry: dict = {"judge_relevant": None, "reason": ""}
    if not chunks:
        telemetry["reason"] = "no chunks"
        return False, telemetry

    chunks_block, n_chunks = _build_chunks_block(chunks)
    if n_chunks == 0:
        telemetry["reason"] = "no non-empty chunks"
        return False, telemetry

    domain_hint = _DOMAIN_HINTS.get(agent_name, _DOMAIN_HINTS["Legislation"])

    try:
        llm = get_gemini_flash_full(temperature=0.0).with_structured_output(
            _RelevanceJudgment, include_raw=True,
        )
        prompt = ChatPromptTemplate.from_template(_RELEVANCE_JUDGE_PROMPT)
        chain = prompt | llm
        with log_time(log, "Relevance judge LLM", agent=agent_name):
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({
                    "query": query,
                    "n_chunks": n_chunks,
                    "source": source_name or "unknown",
                    "chunks_block": chunks_block,
                    "domain_hint": domain_hint,
                }),
                timeout=_JUDGE_TIMEOUT_SEC,
            )
        from core.token_tracker import record as _record_tokens
        _record_tokens(agent_name, "relevance_judge", raw_and_parsed.get("raw"))
        verdict: _RelevanceJudgment = raw_and_parsed["parsed"]

        telemetry["judge_relevant"] = verdict.relevant
        telemetry["reason"] = verdict.reason

        return verdict.relevant, telemetry
    except Exception as e:
        log.warning("Relevance judge errored — fail-open, downstream guards apply",
                    agent=agent_name, error=str(e)[:200])
        telemetry["reason"] = f"judge error: {str(e)[:100]}"
        return True, telemetry


# --- Shared apology detector ---
#
# LLMs commonly write up irrelevant retrievals first and only hedge at the
# end ("However, the provided provisions do not contain information
# regarding..."). A head-only window misses those — scan both head and tail.

COMMON_APOLOGY_PATTERNS: list[str] = [
    "i am sorry", "i'm sorry", "does not contain", "no information",
    "not contain information", "cannot find", "no relevant",
    "cannot provide information", "no direct information",
    "no specific information", "no information available",
    "unable to find", "could not find", "not available in",
    "there is no", "does not have information",
    "based on the provided agent",
    # End-of-response hedging patterns
    "do not contain information regarding",
    "do not contain information about",
    "provided text does not",
    "provided provisions do not",
    "provided legislation provisions do not",
    "provided judgments do not",
    "provided documents do not",
    "however, the provided",
    "however, the search results",
    "however, the retrieved",
]


def head_tail_apology_detected(
    text: str,
    extra_patterns: Optional[list[str]] = None,
    head_chars: int = 500,
    tail_chars: int = 1000,
    min_length: int = 50,
) -> bool:
    """Scan the head and tail of an LLM response for hedging/apology phrases.

    Returns True if the response is too short OR contains an apology phrase
    in either window. Use to trigger a web-search fallback for retrieval
    misses that the relevance gate let through (or that the agent didn't
    run the gate on).

    `extra_patterns` is appended to `COMMON_APOLOGY_PATTERNS` — pass
    agent-specific phrases (e.g. "provided judgments do not contain a case
    matching") without losing the shared baseline.
    """
    if not text or len(text) < min_length:
        return True
    patterns = COMMON_APOLOGY_PATTERNS + (extra_patterns or [])
    full = text.lower()
    head = full[:head_chars]
    tail = full[-tail_chars:] if len(full) > head_chars + tail_chars else ""
    window = head + "\n" + tail
    return any(p in window for p in patterns)
