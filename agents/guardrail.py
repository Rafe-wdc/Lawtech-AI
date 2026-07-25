"""Agent #2 — Guardrail Agent (output-side only, post-cleanup).

Prompt-injection detection was removed on 2026-07-25. The previous
implementation used 14 hard-coded regex patterns plus an LLM sniffer to
gate the input; both were shown to block legitimate Indian-legal drafting
prompts (e.g. `act as complainant`, `act as karta`, `act as public
prosecutor`, `next friend`) and violated the project's `no mechanical
patterns` policy (feedback_no_mechanical_patterns).

Current posture:
  INPUT   — near passthrough. Only rejects a literally empty query with a
            friendly "please enter something" message. No pattern list,
            no length ceiling, no LLM injection sniffer. Trusts Gemini's
            own safety layer and the 2M-token context window.
  OUTPUT  — unchanged: markdown polish, runaway-response repair, hard
            char cap (250K), disclaimer append. These are content-quality
            steps that never see the user's raw query and never fabricate
            a "your prompt was blocked" message.
"""

from __future__ import annotations

from core.state import LegalAgentState
from core.logger import get_logger
from core.progress import progress
from tools.inline.markdown import sanitize_markdown
from tools.inline.disclaimer import add_disclaimer
from core.sanitize import sanitize_output

log = get_logger("Guardrail")


# Output-side hard char cap. Sized to match the orchestrator's own
# synthesis cap (250 KB, see agents/orchestrator.py). Only clips
# runaway assembled responses; never sees the user's raw query.
MAX_FINAL_RESPONSE_CHARS = 250_000
TRUNCATION_SUFFIX = (
    "\n\n---\n_Response truncated -- the answer was longer than the display "
    "budget. Try asking a narrower question (specific section / specific act / "
    "single comparison) for a focused result._\n"
)


# --- Agent Nodes ---

async def guardrail_input_node(state: LegalAgentState) -> dict:
    """Input guardrail — passthrough.

    The only remaining rejection path is 'query is literally empty', which
    exists to give the user a friendly nudge rather than to filter content.
    All prompt-injection detection (regex + LLM) was removed 2026-07-25;
    see the module docstring for context.
    """
    query = state.get("original_query", "")
    log.info("Input passthrough", query=query[:100], query_len=len(query))
    progress("guardrail", "Validating query...", step="validate")

    if not query or not query.strip():
        log.warning("Empty query received")
        return {
            "is_blocked": True,
            "block_reason": "Please enter a legal question or paste a document to review.",
        }

    return {"is_blocked": False}


async def guardrail_output_node(state: LegalAgentState) -> dict:
    """Output guardrail — sanitizes response before returning to user.

    Steps:
    1. Sanitize broken markdown (code fences, bold, tables, bullets)
    2. Add legal disclaimer for applicable task types
    """
    response = state.get("final_response", "")
    task = state.get("task", "Other")

    progress("guardrail", "Finalizing response...", step="finalize")

    if not response:
        log.warning("Empty response detected in output guardrail, returning fallback message")
        return {"final_response": "I wasn't able to generate a response for your query. Please try rephrasing your question or try again shortly."}

    log.info("Output sanitization started",
             response_len=len(response), task=task)

    # Pass 1: structural runaway repair (dash-overflow tables, malformed
    # separators, repeated blocks). Operates line-by-line so it's O(lines)
    # not O(chars) -- cheap even on a 140k-char response with one 125k-char
    # runaway table separator. CRUCIAL ordering: this runs BEFORE
    # sanitize_markdown because sanitize_markdown short-circuits when input
    # is >100k chars (tools/inline/markdown.py:29). By shrinking the runaway
    # first, we let the markdown polish step actually run on the cleaned
    # output.
    pre_len = len(response)
    cleaned = sanitize_output(response)
    if len(cleaned) < pre_len - 1000:
        log.warning("Runaway output trimmed",
                    original_len=pre_len, after_sanitize_output=len(cleaned),
                    removed=pre_len - len(cleaned))

    # Pass 2: markdown polish (code fences, bullets, headings, etc.)
    cleaned = sanitize_markdown(cleaned)

    # Pass 3: hard char cap. sanitize_output handles pad-char runaway, but
    # an LLM can still emit legitimately diverse prose that runs past any
    # useful display budget. Clamp to MAX_FINAL_RESPONSE_CHARS with a
    # truncation suffix that nudges the user to narrow the query.
    if len(cleaned) > MAX_FINAL_RESPONSE_CHARS:
        keep = MAX_FINAL_RESPONSE_CHARS - len(TRUNCATION_SUFFIX)
        log.warning("Response exceeded hard char cap -- truncating",
                    original_len=len(cleaned),
                    cap=MAX_FINAL_RESPONSE_CHARS)
        cleaned = cleaned[:keep] + TRUNCATION_SUFFIX

    # Add disclaimer
    cleaned = add_disclaimer(cleaned, task or "Other")

    len_diff = len(cleaned) - len(response)
    log.info("Output sanitization completed",
             original_len=len(response), final_len=len(cleaned),
             len_diff=len_diff)

    return {"final_response": cleaned}
