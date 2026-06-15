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
- Coarse semantic floor (BGE cosine >= 0.15) catches degenerate cases
  (empty chunks, pure keyword spam) without burning an LLM call. The
  threshold is intentionally permissive — the LLM judge does the real work.
- Judge uses Gemini Flash Lite at temperature=0 with structured output.
  ~500 tokens, ~3s warm. On error: fail-open (let the downstream apology
  guard catch it) so an embedding-service hiccup doesn't break the agent.
- Apology guard is shared too — scans head + tail of the response, so
  hedges added at the end (the common LLM pattern) don't slip through.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from core.clients import get_gemini_flash, get_retriever_embeddings
from core.logger import get_logger, log_time

log = get_logger("RetrievalGate")


# --- Tunables (env-driven for runtime adjustment without code change) ---

_COARSE_SEMANTIC_FLOOR = float(
    os.environ.get("RETRIEVAL_GATE_COARSE_FLOOR", "0.15")
)
_RELEVANCE_CHUNK_CHARS = 1200       # Per-chunk text window shown to the judge
# Number of top hits the judge sees. Raised from 3 to 10: a multi-section
# query ("compare BNS 115, 118, 189, 190, 191, 351, 352") produces 7+ hits
# and the judge was rejecting them because it only saw the first 3
# (sections 115, 118, 189) and concluded the others were "missing". Newacts
# now also short-circuits the gate entirely on exact-filter queries, but
# bumping the cap is defense in depth for Legislation / Constitution / Maxim.
_RELEVANCE_TOP_N = 10
_RELEVANCE_CONFIDENCE_MIN = int(
    os.environ.get("RETRIEVAL_GATE_MIN_CONFIDENCE", "60")
)
_JUDGE_TIMEOUT_SEC = 15.0


# --- Domain hints (lets the judge be more specific for each agent) ---
#
# Each entry frames what the agent is supposed to retrieve, so the judge
# can apply the right "wrong-subject" heuristics. Generic enough to not
# overfit to known failure modes.

_DOMAIN_HINTS: dict[str, str] = {
    "Legislation": (
        "These should be statutory provisions (sections/rules/orders) of "
        "central or state legislation that govern the legal scenario the "
        "user is asking about. Provisions from acts that merely *mention* "
        "the same statute or share rare phrases, while governing a "
        "different subject-matter (different domain, different parties, "
        "or opposite legal effect such as prohibiting vs permitting), "
        "are NOT relevant."
    ),
    "Judgment": (
        "These should be court judgments that materially address the "
        "legal question the user is asking about. Judgments that merely "
        "name the same parties or share keywords but decide a completely "
        "unrelated dispute are NOT relevant. IMPORTANT: a judgment that "
        "applies or discusses the user's cited section IS relevant, even "
        "if the case ALSO arose under other provisions -- e.g. a Sec 302 "
        "IPC murder appeal that contains a substantive Sec 313 CrPC "
        "discussion is on-topic for a 'Section 313 CrPC case law' query. "
        "Only reject if the section is incidental boilerplate, not a "
        "discussed issue. ALSO: if the user named a specific COURT "
        "(e.g. 'Bombay High Court bail cases'), judgments from a "
        "DIFFERENT court are NOT relevant even when the legal subject "
        "matches."
    ),
    "SCI_Judgment": (
        "These should be Supreme Court of India judgments that "
        "materially address the user's legal question. Judgments that "
        "merely fuzzy-match a query term (e.g. judge surname matching "
        "a common phrase, or party name matching a generic word) but "
        "decide an unrelated dispute are NOT relevant. The retrieval "
        "uses fuzzy and phrase matching together, so off-topic cases "
        "with strong full-text term overlap can surface — reject when "
        "the case's actual subject doesn't match the user's question."
    ),
    "Document": (
        "These should be passages from the user's UPLOADED document(s) "
        "that contain information answering the user's question. The "
        "retrieval is MMR with k=30 and always returns 30 chunks "
        "regardless of similarity — when the user's question is "
        "off-topic for the uploaded file (e.g. they uploaded a "
        "contract and asked about Section 138 NI Act), the chunks "
        "will be unrelated and NOT relevant. Reject when the chunks "
        "are about a different subject than the user's question. "
        "Accept when chunks contain the answer even if the uploaded "
        "file's overall genre is different from the question's framing."
    ),
    "Newacts": (
        "These should be sections of the new criminal-law codes (BNS, "
        "BNSS, BSA) or their old-law counterparts (IPC, CrPC, IEA) that "
        "address the user's query. ACCEPT both kinds of retrieval:\n"
        "  (1) Specific-section lookups — user named 'Section 138 NI Act' "
        "    style. Retrieved chunk must match that section/act.\n"
        "  (2) Topic / chapter / comparison lookups — user named a "
        "    DOCTRINE or CHAPTER name (e.g. 'right of private defence', "
        "    'general exceptions', 'culpable homicide', 'unlawful "
        "    assembly', 'criminal conspiracy', 'abetment', 'theft and "
        "    extortion', 'offences against property'). For these, the "
        "    retrieved hits are EXPECTED to be a BATCH of consecutive "
        "    sections covering that chapter (BNS Sec 34-44 for private "
        "    defence, IPC Sec 76-106 for general exceptions, etc.). "
        "    ACCEPT them as relevant when the chunk content addresses "
        "    the named doctrine, EVEN IF the user didn't enumerate "
        "    individual section numbers.\n"
        "  (3) Cross-act comparison queries (e.g. 'compare X under BNS "
        "    vs IPC', 'BNS equivalent of Section 379 IPC') — retrieved "
        "    hits from EITHER act OR both are relevant. Don't reject "
        "    just because hits come from one act when the query named "
        "    both — the comparison can be assembled from partial "
        "    retrieval plus the LLM's knowledge of the corresponding "
        "    sections.\n"
        "REJECT only when the retrieved sections genuinely address a "
        "different subject (e.g. user asked about cheque dishonour but "
        "retrieval returned sections on theft). Do not reject for "
        "'user didn't name these specific sections' — that's expected "
        "for topic/doctrine queries."
    ),
    "Constitution": (
        "These should be Articles of the Constitution of India that "
        "materially address the user's question. Articles cited "
        "incidentally or in an unrelated context are NOT relevant."
    ),
    "Maxim": (
        "These should be legal maxims whose meaning and use directly "
        "answer the user's question. Maxims merely sharing a Latin/"
        "English keyword with the query are NOT relevant."
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
    confidence: int = Field(
        ..., ge=0, le=100,
        description="Confidence in the verdict, 0 to 100.",
    )
    matched_subject: str = Field(
        "",
        description=(
            "What the retrieved documents actually govern, in 1-2 phrases "
            "(e.g. 'tax assessment proceedings, NOT property injunctions'). "
            "Empty when relevant=true."
        ),
    )
    reason: str = Field(
        "",
        description="One-sentence explanation of the verdict.",
    )


_RELEVANCE_JUDGE_PROMPT = """You are a strict legal-retrieval relevance judge.

USER QUERY:
{query}

RETRIEVED LEGAL DOCUMENTS (top {n_chunks} hits, source = "{source}"):
{chunks_block}

DOMAIN CONTEXT (what this retrieval was supposed to produce):
{domain_hint}

Decide: do these documents DIRECTLY answer the user's query?

CRITICAL: "Directly answer" means the documents govern the same legal
scenario the user is asking about. Documents that merely *mention* the
same statute name, share rare phrases, name the same parties, or discuss
the same topic in an UNRELATED context are NOT relevant — they are false
positives from keyword-based retrieval.

Examples of NON-RELEVANT retrieval to reject:
- Query about CPC Order XXXIX injunctions for property → retrieved
  Municipalities Act sections that *prohibit* injunctions for electoral
  rolls. (Wrong subject, opposite direction.)
- Query about BNS Section 318 cheating → retrieved provisions about
  Section 318 of a different act. (Same number, different statute.)
- Query asking how to GRANT relief → retrieved provisions that BAR relief
  for a different proceeding. (Same domain, opposite direction.)
- Query about a 2019 SC judgment → retrieved unrelated case from a
  different court that happens to share a party name.

Return your structured verdict. Be strict: if in doubt, mark relevant=false
so the system can fall back to web search rather than ship wrong citations.
"""


# --- Helpers ---

def _build_chunks_block(chunks: list[str]) -> tuple[str, int]:
    """Render top chunks as a numbered block for the judge prompt."""
    parts: list[str] = []
    for i, c in enumerate(chunks[:_RELEVANCE_TOP_N], 1):
        text = (c or "")[:_RELEVANCE_CHUNK_CHARS].strip()
        if text:
            parts.append(f"[Doc {i}]\n{text}")
    return "\n\n".join(parts), len(parts)


def _coarse_semantic_floor(query: str, chunks: list[str]) -> tuple[bool, float]:
    """Cheap embedding pre-filter (sync — kept for non-async callers).

    Fail-open on errors. The async path below is preferred when called
    from an asyncio handler — it skips the executor hop entirely.
    """
    if not chunks:
        return False, 0.0
    try:
        embeddings = get_retriever_embeddings()
        non_empty = [(c or "")[:_RELEVANCE_CHUNK_CHARS] for c in chunks if c]
        if not non_empty:
            return False, 0.0
        q_vec = embeddings.embed_query(query)
        c_vecs = embeddings.embed_documents(non_empty[:_RELEVANCE_TOP_N])
        # BGE embeddings are L2-normalized -> dot product == cosine similarity.
        max_sim = max(sum(a * b for a, b in zip(q_vec, cv)) for cv in c_vecs)
        return max_sim >= _COARSE_SEMANTIC_FLOOR, max_sim
    except Exception as e:
        log.warning("Coarse semantic floor errored — fail-open",
                    error=str(e)[:200])
        return True, 0.0


async def _coarse_semantic_floor_async(query: str, chunks: list[str]) -> tuple[bool, float]:
    """Async coarse embedding pre-filter — uses aembed_query/aembed_documents.

    Phase 7: switching this from `asyncio.to_thread(_coarse_semantic_floor, ...)`
    to a real async path removes the executor hop. Under 50+ concurrent users
    the executor was saturating (per-worker thread pool ≈ 12 threads, demand
    ≈ 50 in-flight embed calls) and the queue depth was the throughput cliff.

    Gracefully falls back to the sync path when the configured embeddings
    object doesn't implement the async methods (e.g. dev HuggingFaceEmbeddings
    where the base ABC default would just thread-wrap the sync call anyway).
    """
    if not chunks:
        return False, 0.0
    try:
        embeddings = get_retriever_embeddings()
        non_empty = [(c or "")[:_RELEVANCE_CHUNK_CHARS] for c in chunks if c]
        if not non_empty:
            return False, 0.0
        # If the embeddings object overrides aembed_query (RemoteEmbeddings does),
        # this is a real async HTTP call. Otherwise the ABC default runs the
        # sync version in a thread pool — same as before, no regression.
        q_vec = await embeddings.aembed_query(query)
        c_vecs = await embeddings.aembed_documents(non_empty[:_RELEVANCE_TOP_N])
        max_sim = max(sum(a * b for a, b in zip(q_vec, cv)) for cv in c_vecs)
        return max_sim >= _COARSE_SEMANTIC_FLOOR, max_sim
    except Exception as e:
        log.warning("Async coarse semantic floor errored — fail-open",
                    error=str(e)[:200])
        return True, 0.0


async def check_retrieval_relevance(
    query: str,
    chunks: list[str],
    source_name: Optional[str],
    agent_name: str,
) -> tuple[bool, dict]:
    """Public entry point: should the caller proceed with these chunks?

    Args:
        query: User's question (will be truncated to 1500 chars for the judge).
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
            coarse_sim, judge_relevant, judge_confidence, matched_subject,
            reason. Always populated so logs are consistent.

    On any internal error: fail-open (return True). The agent's apology
    guard downstream is the second line of defense — we never want to
    block legitimate retrievals because of a transient embedding/LLM
    outage.
    """
    telemetry: dict = {
        "coarse_sim": 0.0, "judge_relevant": None,
        "judge_confidence": None, "matched_subject": "", "reason": "",
    }
    if not chunks:
        telemetry["reason"] = "no chunks"
        return False, telemetry

    # Pass 1: coarse embedding floor (cheap). Real async — no executor hop.
    floor_ok, max_sim = await _coarse_semantic_floor_async(query, chunks)
    telemetry["coarse_sim"] = round(max_sim, 4)
    if not floor_ok:
        telemetry["reason"] = f"coarse semantic floor failed ({max_sim:.3f})"
        return False, telemetry

    # Pass 2: LLM-as-judge (the real call)
    chunks_block, n_chunks = _build_chunks_block(chunks)
    if n_chunks == 0:
        telemetry["reason"] = "no non-empty chunks"
        return False, telemetry

    domain_hint = _DOMAIN_HINTS.get(agent_name, _DOMAIN_HINTS["Legislation"])

    try:
        llm = get_gemini_flash(temperature=0.0).with_structured_output(
            _RelevanceJudgment, include_raw=True,
        )
        prompt = ChatPromptTemplate.from_template(_RELEVANCE_JUDGE_PROMPT)
        chain = prompt | llm
        with log_time(log, "Relevance judge LLM", agent=agent_name):
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({
                    "query": query[:1500],
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
        telemetry["judge_confidence"] = verdict.confidence
        telemetry["matched_subject"] = verdict.matched_subject
        telemetry["reason"] = verdict.reason

        if not verdict.relevant or verdict.confidence < _RELEVANCE_CONFIDENCE_MIN:
            return False, telemetry
        return True, telemetry
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
