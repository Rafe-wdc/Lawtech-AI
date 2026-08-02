"""L4 Observability — Response Quality Scorer

Samples 10% of responses and scores them using Gemini Flash Lite as judge.
Scores 3 dimensions (0.0–1.0 each):
  - faithfulness:   Is the answer grounded in what was retrieved?
  - relevance:      Does it actually answer what was asked?
  - completeness:   Did it cover the key aspects of the question?

Design principles:
- Fire-and-forget: never blocks the response pipeline
- 10% sampling: keeps cost negligible (~$0.00001/scored request)
- Graceful degradation: any failure → silent skip, no impact on user
- Skips: greetings, blocked queries, empty responses, drafts
"""

from __future__ import annotations

import asyncio
import json
import random

from core.logger import get_logger

log = get_logger("QualityScorer")

# Agents worth scoring (skip Non_legal greetings, Document — no signal
# in a Q&A over an uploaded PDF that we can meaningfully compare
# faithfulness against without also parsing the source doc).
_SCOREABLE_AGENTS = {
    "Legislation", "Judgment", "SCI_Judgment", "GST_Judgment", "Newacts",
    "Constitution", "Maxim", "Legal_Concepts", "Scenario",
    "Drafting",  # Added 2026-08-02 (P2 G-31). Sampled at 5% (see
                 # should_score below) — half the rate of retrieval
                 # agents because per-scored request costs ~$0.005
                 # against Drafting's long response.
}

# Per-agent sample rate — retrieval agents at 10%, Drafting at 5% to
# amortise the cost of scoring a long draft.
_SAMPLE_RATE_DEFAULT = 0.10
_SAMPLE_RATE_DRAFTING = 0.05


def should_score(agents_used: list[str]) -> bool:
    """Coin-flip that respects per-agent sample rates.

    Central so the two callsites (gateway batch, chat_runner stream)
    both apply the same policy — callers previously hardcoded 0.10 and
    would drift over time.
    """
    if not agents_used or not any(a in _SCOREABLE_AGENTS for a in agents_used):
        return False
    # If Drafting is the ONLY scoreable agent, use the lower rate.
    # If Drafting is present alongside retrieval agents, keep the
    # default rate (the retrieval work is worth scoring anyway).
    non_drafting = [
        a for a in agents_used
        if a in _SCOREABLE_AGENTS and a != "Drafting"
    ]
    rate = _SAMPLE_RATE_DEFAULT if non_drafting else _SAMPLE_RATE_DRAFTING
    return random.random() < rate

_SCORE_PROMPT = """\
You are a strict legal AI evaluator. Score the following AI response on 3 dimensions.

Question: {query}

AI Response (first 1500 chars): {response}

Score each dimension from 0.0 to 1.0:
- faithfulness: Is every claim in the response grounded in verifiable legal facts? \
  (1.0 = fully grounded, 0.0 = hallucinated/unsupported)
- relevance: Does the response directly answer the question asked? \
  (1.0 = perfectly on-topic, 0.0 = completely off-topic)
- completeness: Does it cover the key aspects of the question? \
  (1.0 = comprehensive, 0.0 = missing critical information)

Respond ONLY with valid JSON, no explanation:
{{"faithfulness": <float>, "relevance": <float>, "completeness": <float>}}"""


async def score_response(
    query: str,
    response: str,
    agents_used: list[str],
    request_log_id: int | None = None,
    thread_id: str = "",
) -> dict | None:
    # NOTE: do NOT pass the underlying model identifier to chat_store.log_quality
    # — model identifiers must never leave this process (see core/redact.py).
    # The DB column accepts an empty string and the admin endpoint aggregates
    # by agent, not by model.
    """Score a response with Gemini Flash Lite. Returns scores dict or None.

    Called fire-and-forget from gateway.py with 10% sampling:
        if random.random() < 0.10:
            asyncio.create_task(score_response(...))
    """
    # Skip conditions
    if not response or len(response) < 100:
        return None
    if not any(a in _SCOREABLE_AGENTS for a in agents_used):
        return None

    try:
        from core.clients import get_gemini_flash
        from core.chat_store import chat_store

        llm = get_gemini_flash(temperature=0.0)
        prompt = _SCORE_PROMPT.format(
            query=query[:400],
            response=response[:1500],
        )

        raw = await asyncio.wait_for(
            llm.ainvoke(prompt),
            timeout=30.0,
        )
        text = raw.text.strip()

        # Parse JSON — strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        scores = json.loads(text)

        # Clamp all scores to [0.0, 1.0]
        faithfulness  = max(0.0, min(1.0, float(scores.get("faithfulness", 0.5))))
        relevance     = max(0.0, min(1.0, float(scores.get("relevance", 0.5))))
        completeness  = max(0.0, min(1.0, float(scores.get("completeness", 0.5))))
        avg_score     = round((faithfulness + relevance + completeness) / 3, 4)

        result = {
            "faithfulness":  round(faithfulness, 4),
            "relevance":     round(relevance, 4),
            "completeness":  round(completeness, 4),
            "avg_score":     avg_score,
        }

        # Persist to quality_log
        dominant_agent = next(
            (a for a in agents_used if a in _SCOREABLE_AGENTS), agents_used[0] if agents_used else "unknown"
        )
        await chat_store.log_quality(
            request_log_id=request_log_id,
            thread_id=thread_id,
            agent=dominant_agent,
            query_preview=query[:300],
            **result,
        )

        # Update Prometheus gauges
        from core.metrics import METRICS
        METRICS["quality_avg_score"].set(avg_score)
        METRICS["quality_faithfulness"].set(faithfulness)
        METRICS["quality_relevance"].set(relevance)
        METRICS["quality_scored_total"].inc()
        if avg_score < 0.6:
            METRICS["quality_low_count"].inc()

        log.info("Quality scored",
                 agent=dominant_agent,
                 avg=avg_score,
                 faithfulness=faithfulness,
                 relevance=relevance,
                 completeness=completeness)

        return result

    except Exception as e:
        log.debug("Quality scoring skipped", error=str(e)[:80])
        return None
