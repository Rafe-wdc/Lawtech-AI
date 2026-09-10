"""Agent #12 — Non-Legal / Greeting Agent

Handles queries that are not related to legal matters:
- Greetings (hello, hi, namaste, good morning, how are you)
- "Who are you?" / "What can you do?" questions
- Casual conversation or off-topic queries

Uses Gemini Flash to generate a warm, contextual response — never a hardcoded string.
"""

from __future__ import annotations

import asyncio

from langchain_core.prompts import ChatPromptTemplate

from core.state import LegalAgentState, AgentResult
from core.clients import get_gemini_flash_full
from core.language import localize_prompt
from core.logger import get_logger, log_time, short_err
from core.progress import progress

log = get_logger("NonLegal")

_NON_LEGAL_PROMPT = """You are Lawttorney, a friendly and professional AI legal assistant specializing in Indian law.

The user has sent a message that is not a legal query. Respond warmly and appropriately based on the nature of their message:

- If it is a **greeting** (e.g. hello, hi, namaste, good morning): greet them back warmly and briefly introduce yourself and what you can help with.
- If they ask **who you are or what you do**: introduce yourself as Lawttorney, an AI legal assistant for Indian law, and list your key capabilities in a clear, concise way.
- If it is **casual conversation or off-topic**: acknowledge it politely and gently guide them toward asking a legal question.

IDENTITY (non-negotiable): you are Lawttorney. NEVER name, hint at or confirm the underlying model or provider - not Gemini, Google, Anthropic, Claude, OpenAI, GPT, ChatGPT, Llama, Mistral, or any other. If asked what model or technology runs behind you, or whether you are built on a named model, answer only that you are Lawttorney, a legal AI built for Indian law, and that you do not share details of the underlying technology - then move on. Do NOT say "powered by", "built on", "based on" or "developed by" any company.
Keep your response concise (3-6 sentences or a short bulleted list). Do not be verbose.
Do not make up legal information. Do not discuss non-legal topics in depth.

Your capabilities you may mention:
- Court judgments (Supreme Court & High Courts)
- Legislation and acts (all Indian laws)
- New criminal codes (BNS, BNSS, BSA)
- Constitutional provisions and legal maxims
- Legal document drafting (petitions, notices, agreements)
- Legal scenario analysis and advice

{turn_context}User message: {query}
"""


async def non_legal_node(state: LegalAgentState) -> dict:
    """Generate a contextual, LLM-driven response for non-legal / greeting queries."""
    query = state.get("query", state.get("original_query", ""))
    user_language = state.get("user_language", "en")
    # Acknowledgement of the previous turn ("ok", "thanks", "yes"):
    # answer in context, in one or two sentences, and offer the natural
    # next step for what was just delivered. Never re-deliver it.
    turn_context = ""
    if state.get("conversational_followup"):
        _kind = state.get("previous_artifact_kind") or state.get("previous_task") or "answer"
        _prev = (state.get("previous_artifact_content") or "")
        _head = " ".join(_prev.split())[:300]
        turn_context = (
            "CONTEXT: the user is replying to what you just delivered - a "
            f"{_kind}. Its opening reads: \"{_head}\". Their message is an "
            "acknowledgement, not a new request. Reply in ONE or TWO short "
            "sentences: acknowledge, then offer one concrete next step that "
            "fits that deliverable (for a draft: add a ground, change the "
            "court or party details, translate it, or export it). Do NOT "
            "introduce yourself, do NOT list capabilities, do NOT repeat or "
            "summarise the deliverable, do NOT ask them to ask a legal question. "
            "If their message is a bare yes/no, treat it as a reply to any "
            "question you asked at the end of the deliverable.\n\n"
        )

    log.info("Non-legal agent started", query=query[:80], lang=user_language)

    progress("non_legal", "Preparing response...", step="generate")

    try:
        # Small-talk replies don't need a large output budget; 2048 tokens
        # (~8k chars) is plenty and prevents accidental runaway.
        llm = get_gemini_flash_full(temperature=0.4, max_output_tokens=2048)
        prompt = ChatPromptTemplate.from_template(
            localize_prompt(_NON_LEGAL_PROMPT, user_language)
        )
        chain = prompt | llm

        with log_time(log, "Non-legal response generation"):
            response = await asyncio.wait_for(
                chain.ainvoke({"query": query, "turn_context": turn_context}),
                timeout=20,
            )

        content = response.text if hasattr(response, "text") else str(response)
        from core.token_tracker import record as _record_tokens
        tokens = _record_tokens("Non_legal", "respond", response)

        log.info("Non-legal agent completed", response_len=len(content))

        result = AgentResult(
            agent_name="Non_legal",
            content=content,
            sources=[],
            tokens_consumed=tokens,
        )

    except Exception as e:
        from core.metrics import record_agent_error
        record_agent_error("Non_legal", e)
        log.error("Non-legal agent failed", error=short_err(e), exc_info=True)
        result = AgentResult(
            agent_name="Non_legal",
            content=(
                "👋 Hello! I'm **Lawttorney**, your AI-powered Indian legal assistant. "
                "I can help with court judgments, legislation, legal drafting, constitutional law, "
                "and legal scenario analysis. Please ask me a legal question!"
            ),
            sources=[],
            tokens_consumed=0,
            error=short_err(e),
        )

    return {"agent_results": {"Non_legal": result}}
