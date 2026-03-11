"""Agent #12 — Non-Legal / Greeting Agent

Handles queries that are not related to legal matters:
- Greetings (hello, hi, namaste, good morning, how are you)
- "Who are you?" / "What can you do?" questions
- Casual conversation or off-topic queries

Uses Gemini Flash to generate a warm, contextual response — never a hardcoded string.
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from core.state import LegalAgentState, AgentResult
from core.clients import get_gemini_flash
from core.language import localize_prompt
from core.logger import get_logger, log_time

log = get_logger("NonLegal")

_NON_LEGAL_PROMPT = """You are Lawttorney, a friendly and professional AI legal assistant specializing in Indian law.

The user has sent a message that is not a legal query. Respond warmly and appropriately based on the nature of their message:

- If it is a **greeting** (e.g. hello, hi, namaste, good morning): greet them back warmly and briefly introduce yourself and what you can help with.
- If they ask **who you are or what you do**: introduce yourself as Lawttorney, an AI legal assistant for Indian law, and list your key capabilities in a clear, concise way.
- If it is **casual conversation or off-topic**: acknowledge it politely and gently guide them toward asking a legal question.

Keep your response concise (3-6 sentences or a short bulleted list). Do not be verbose.
Do not make up legal information. Do not discuss non-legal topics in depth.

Your capabilities you may mention:
- Court judgments (Supreme Court & High Courts)
- Legislation and acts (all Indian laws)
- New criminal codes (BNS, BNSS, BSA)
- Constitutional provisions and legal maxims
- Legal document drafting (petitions, notices, agreements)
- Legal scenario analysis and advice

User message: {query}
"""


async def non_legal_node(state: LegalAgentState) -> dict:
    """Generate a contextual, LLM-driven response for non-legal / greeting queries."""
    query = state.get("query", state.get("original_query", ""))
    user_language = state.get("user_language", "en")

    log.info("Non-legal agent started", query=query[:80], lang=user_language)

    try:
        llm = get_gemini_flash(temperature=0.4)
        prompt = ChatPromptTemplate.from_template(
            localize_prompt(_NON_LEGAL_PROMPT, user_language)
        )
        chain = prompt | llm

        with log_time(log, "Non-legal response generation"):
            response = await chain.ainvoke({"query": query})

        content = response.content if hasattr(response, "content") else str(response)
        tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            tokens = response.usage_metadata.get("total_tokens", 0)

        log.info("Non-legal agent completed", response_len=len(content))

        result = AgentResult(
            agent_name="Non_legal",
            content=content,
            sources=[],
            tokens_consumed=tokens,
        )

    except Exception as e:
        log.error("Non-legal agent failed", error=str(e), exc_info=True)
        result = AgentResult(
            agent_name="Non_legal",
            content=(
                "👋 Hello! I'm **Lawttorney**, your AI-powered Indian legal assistant. "
                "I can help with court judgments, legislation, legal drafting, constitutional law, "
                "and legal scenario analysis. Please ask me a legal question!"
            ),
            sources=[],
            tokens_consumed=0,
            error=str(e),
        )

    return {"agent_results": {"Non_legal": result}}
