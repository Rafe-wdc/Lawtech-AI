"""Agent #2 — Guardrail Agent (output-side only, post-cleanup).

Prompt-injection detection was removed on 2026-07-25. The previous
implementation used 14 hard-coded regex patterns plus an LLM sniffer to
gate the input; both were shown to block legitimate Indian-legal drafting
prompts (e.g. `act as complainant`, `act as karta`, `act as public
prosecutor`, `next friend`) and violated the project's `no mechanical
patterns` policy (feedback_no_mechanical_patterns).

Current posture:
  INPUT   — near passthrough. Rejects:
              (1) a literally empty query with a friendly "please enter
                  something" message;
              (2) NARROW gibberish exemption: a query with zero alphabetic
                  characters in ANY script (Latin, Devanagari, Tamil,
                  Arabic, etc.) — e.g. "8883241***##", "@@@@@", "???",
                  "12345", "...". This is a deliberate exemption from the
                  no-mechanical-pattern policy because the classifier LLM
                  otherwise misreads stray `***` / `##` as drafting-
                  template markers and routes garbage to the (most
                  expensive) Drafting pipeline.
            No injection regex bank, no length ceiling, no LLM sniffer.
            Trusts Gemini's own safety layer and the 2M-token context
            window for everything with real words in it.
  OUTPUT  — markdown polish, runaway-response repair, hard char cap
            (250K). These are content-quality steps that never see the
            user's raw query and never fabricate a "your prompt was
            blocked" message. The legal disclaimer that used to be
            appended here was removed on 2026-08-20; the persistent UI
            disclaimer ("Lawttorney can make mistakes. Verify important
            legal information.") is now the canonical shield, and the
            per-response append was redundant.
"""

from __future__ import annotations

import re

from core.state import LegalAgentState
from core.logger import get_logger
from core.progress import progress
from tools.inline.markdown import sanitize_markdown
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


# Unicode-aware "any alphabetic letter, any script" matcher.
# `[^\W\d_]` in re.UNICODE mode = word-char minus digit minus underscore
# = every Unicode letter (Latin, Devanagari, Bengali, Tamil, Telugu, Kannada,
# Malayalam, Gujarati, Punjabi, Odia, Urdu/Arabic, CJK, etc.). If a query
# fails this search, it contains NO letter in any script — treat as
# unparseable input (see module docstring).
_HAS_ALPHA_RE = re.compile(r"[^\W\d_]", re.UNICODE)


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

    if not _HAS_ALPHA_RE.search(query):
        log.warning("Gibberish query rejected (no alphabetic character in any script)",
                    query=query[:80])
        return {
            "is_blocked": True,
            "block_reason": (
                "👋 I'm **Lawttorney**, your AI legal assistant for Indian law. "
                "I couldn't find a question in that input — it looks like only "
                "digits or symbols. Please type your question in words "
                "(any language). For example:\n\n"
                "- *What is Section 302 IPC?*\n"
                "- *Draft a bail application under Section 439 CrPC*\n"
                "- *Explain Article 21 of the Constitution*"
            ),
        }

    return {"is_blocked": False}


async def guardrail_output_node(state: LegalAgentState) -> dict:
    """Output guardrail — sanitizes response before returning to user.

    Steps:
    1. Structural runaway repair (sanitize_output)
    2. Markdown polish (sanitize_markdown)
    3. Hard char cap (250K) with truncation nudge
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

    # Pass 1b: em / en dashes - the "ChatGPT dash". Advocates flagged this on
    # 2026-09-08 as making the output read machine-written; Indian legal
    # register sets off a parenthetical with commas, semicolons or brackets.
    #
    # This lives HERE, not in the drafting agent, because it has to cover
    # EVERY response. The first fix went into agents.drafting.validate_draft,
    # which only runs on the drafting path - so drafts came out clean while
    # judgment summaries, legislation answers, scenario analyses and document
    # Q&A still shipped em-dashes. Reported 2026-09-09 on "Prepare detailed
    # summary for the Manjari Greens phase 3", which routes to a judgment
    # agent and never touches validate_draft.
    #
    # Character classes are [space,tab], never \s: \s matches newlines,
    # so a dash at end-of-line would swallow the break and glue two lines
    # together - the exact alignment damage this is meant to prevent.
    # Three shapes, most specific first: spaced parenthetical -> comma,
    # unspaced compound or range -> hyphen, leading list marker -> removed.
    _dash_n = cleaned.count("—") + cleaned.count("–")
    if _dash_n:
        cleaned = re.sub(r"(?m)^[ \t]*[—–][ \t]+", "", cleaned)
        cleaned = re.sub(r"(?<=\d)[ \t]+[—–][ \t]+(?=\d)", "-", cleaned)   # numeric range
        # Audit 2026-09-11: a lone dash in a table cell ("| — |") is an
        # empty-cell marker, never a parenthetical; a spaced dash becomes a
        # comma only between letters.
        cleaned = re.sub(r"(?<=[A-Za-zऀ-෿])[ \t]+[—–][ \t]+(?=[A-Za-zऀ-෿])", ", ", cleaned)
        cleaned = re.sub(r"(?<=\|)[ \t]*[—–][ \t]*(?=\|)", " - ", cleaned)
        cleaned = re.sub(r"(?<=[A-Za-z0-9])[—–](?=[A-Za-z0-9])", "-", cleaned)
        cleaned = cleaned.replace("—", "-").replace("–", "-")
        cleaned = re.sub(r",[ \t]*,", ",", cleaned)
        log.info("Normalised em/en dashes in final response",
                 count=_dash_n, task=task)
    # Pass 2: markdown polish (code fences, bullets, headings, etc.)
    cleaned = sanitize_markdown(cleaned)

    # Pass 2b: strip external web URLs (Google Search grounding, web-fallback,
    # any URL the LLM emitted in prose) and their labelled wrappers. Whitelist:
    # `lawttorney.s3.*.amazonaws.com` (HC judgment PDFs) and `api.sci.gov.in`
    # (SCI PDFs). See core.url_filter.
    from core.url_filter import sanitize_prose as _strip_urls
    pre_url_len = len(cleaned)
    cleaned = _strip_urls(cleaned)
    if len(cleaned) < pre_url_len:
        log.info("External URLs stripped from final_response",
                 before=pre_url_len, after=len(cleaned),
                 removed=pre_url_len - len(cleaned))

    # Pass 2b1: a non-drafting answer is never a pleading. Arguments /
    # analysis that came back with a cause title, application number, party
    # block, PRAYER or sign-off lose the furniture and keep the body.
    # Advocate test 2026-09-11. See core.pleading_furniture.
    from core.pleading_furniture import strip_pleading_furniture as _strip_furniture
    cleaned, _pf = _strip_furniture(cleaned, task)
    if _pf["prayer_removed"] or _pf["lines_removed"]:
        log.warning("Pleading furniture stripped from non-drafting answer", task=task, **_pf)

    # Pass 2b2: no diagrams, no code fences. A flowchart drawn as ASCII art
    # inside a fence renders in monospace, cannot wrap and misaligns; a
    # legal opinion or draft never needs one. Drawings are removed, other
    # fences are unwrapped into body text. Advocate report 2026-09-10.
    from core.diagrams import strip_diagrams as _strip_diagrams
    cleaned, _diag = _strip_diagrams(cleaned)
    if _diag["diagrams_removed"] or _diag["fences_unwrapped"]:
        log.info("Diagram / code-fence repair applied", task=task, **_diag)

    # Pass 2c: keep the response inside the answer box. A bare whitelisted
    # PDF URL (70-110 unbreakable chars) becomes a short labelled link; a
    # 20+ char run of underscores used as a blank is capped; a line of
    # dashes becomes a markdown rule. Advocate report 2026-09-10: 'a few
    # part of the answer is going beyond the answer box'. See core.overflow.
    from core.overflow import keep_inside_answer_box as _fit
    cleaned, _fit_stats = _fit(cleaned)
    if _fit_stats["urls"] or _fit_stats["runs"]:
        log.info("Overflow repair applied", task=task, **_fit_stats)

    # Pass 2d: never reveal the model or provider. Only sentences in which
    # the assistant describes ITSELF are rewritten; case law and party names
    # that mention Google or another vendor are untouched. Report 2026-09-10:
    # 'what is the model running behind you' -> 'powered by Google's Gemini'.
    from core.identity import hide_provider as _hide_provider
    cleaned, _hidden = _hide_provider(cleaned)
    if _hidden:
        log.warning("Provider self-description rewritten in final response",
                    task=task, sentences=_hidden)

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

    len_diff = len(cleaned) - len(response)
    log.info("Output sanitization completed",
             original_len=len(response), final_len=len(cleaned),
             len_diff=len_diff)

    return {"final_response": cleaned}
