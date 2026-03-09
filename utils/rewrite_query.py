from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, AIMessage
from typing import List, Union
import logging

logger = logging.getLogger(__name__)

REWRITE_PROMPT = """You are a legal query rewriting assistant. Rewrite the user's latest query into a standalone, self-contained query that incorporates relevant context from the conversation history.

Rules:
1. The rewritten query must be understandable WITHOUT the conversation history.
2. Preserve all legal specificity: section numbers, act names, party names, court names, dates.
3. If the user refers to something from the conversation (e.g., "that section", "the same act", "what about bail in this case"), resolve the reference using the conversation history.
4. If the latest query is already standalone and self-contained, return it as-is.
5. Do NOT answer the query. Only rewrite it.
6. Do NOT add information that wasn't in the conversation or query.
7. Keep the rewritten query concise — it should be a search query, not a paragraph.
8. Return ONLY the rewritten query text, nothing else.

Conversation History:
{chat_history_text}

Latest User Query: {query}

Rewritten Standalone Query:"""


def rewrite_query_with_context(
    query: str,
    chat_history: List[Union[HumanMessage, AIMessage]]
) -> str:
    """
    Rewrites a user query into a standalone query using conversation context.
    Uses Gemini Flash Lite for fast, cheap rewriting.
    Falls back to original query on any failure.
    """
    try:
        # Skip if no meaningful chat history
        if not chat_history or len(chat_history) <= 2:
            return query

        # Skip if it's just the default "Fresh chat started" placeholder
        if (len(chat_history) == 2
            and isinstance(chat_history[1], AIMessage)
            and "Fresh chat started" in chat_history[1].content):
            return query

        # Format chat history as text
        history_lines = []
        for msg in chat_history:
            if isinstance(msg, HumanMessage):
                history_lines.append(f"User: {msg.content}")
            elif isinstance(msg, AIMessage):
                content = msg.content[:500] + "..." if len(msg.content) > 500 else msg.content
                history_lines.append(f"Assistant: {content}")

        chat_history_text = "\n".join(history_lines)

        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0.1)
        prompt = ChatPromptTemplate.from_template(REWRITE_PROMPT)
        chain = prompt | llm

        response = chain.invoke({
            "query": query,
            "chat_history_text": chat_history_text
        })

        rewritten = response.content.strip()

        if not rewritten or len(rewritten) > 1000:
            logger.warning("Query rewrite produced invalid result, using original. Length: %d", len(rewritten) if rewritten else 0)
            return query

        logger.info("Query rewritten: '%s' -> '%s'", query, rewritten)
        return rewritten

    except Exception as e:
        logger.error("Error in query rewriting, using original query: %s", e)
        return query
