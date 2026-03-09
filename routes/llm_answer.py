from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import threading
import json
import logging

logger = logging.getLogger(__name__)
from utils.task_identifer import identify_task
from utils.get_chat_history_from_thread import get_chat_history_from_thread
from utils.expand_legal_abbreviations import expand_legal_abbreviations
from generate_response import generate_response
from utils.background_summary import background_store_recent
from utils.rewrite_query import rewrite_query_with_context
from utils.guardrails import run_input_guardrails, run_output_guardrails

router = APIRouter(prefix="/pyapi", tags=["llm"])

class SearchRequest(BaseModel):
    Promptquery: str
    globalThreadId: Optional[str] = None
# === Search Endpoint ===
@router.post("/search")
async def llm_answer(data: SearchRequest):
    logger.info("inside llm_answer new")
    try:
        chat_history = []
        query = expand_legal_abbreviations(data.Promptquery)
        thread_id = data.globalThreadId
        logger.debug("Expanded query: %s", query)

        # Input guardrails: validate query and check for prompt injection
        guardrail_result = run_input_guardrails(data.Promptquery)
        if guardrail_result.blocked:
            logger.warning(f"Input guardrail blocked: {guardrail_result.reason}")
            return {
                "globalThreadId": thread_id,
                "result": guardrail_result.reason,
                "total_tokens_consumed": 0,
                "source": {'title': 'Blocked', "content": ["Query blocked by safety filter"], "docLink": None},
                "summary": guardrail_result.reason
            }

        summary_text = ""
        final_summary = ""
        original_query = query
        if thread_id is not None:
            logger.info(f"Thread ID provided: {thread_id}")
            chat_history, summary_text  = get_chat_history_from_thread(thread_id)
            logger.debug("Chat history: %s, Summary: %s", chat_history, summary_text)

            # Rewrite query with conversational context for better retrieval
            query = rewrite_query_with_context(query, chat_history)

        # ⚙️ Identify the task type
        if len(query.split())>=100:
            task = "Scenario"
        else:
            task = identify_task(query, chatSummary=summary_text if thread_id else None)


        # ❌ Non-legal case handling
        if task == "Non_legal":
            content = "This query is not related to legal matters. Please ask a legal question."
            metadata = {"source": {'title': 'Non-legal', "content": ["Non-legal query"], "docLink": None}}
            # logger.warning(f"Non-legal query detected: {query}")
            return {
                "globalThreadId": thread_id,
                "result": content,
                "total_tokens_consumed": 0,
                "source": metadata["source"],
                "summary": content
            }

        # 🤖 LLM response
        llm_response = generate_response(query,chat_history,task)
        logger.info("LLM response generated successfully")
        # 📤 Extract source and token usage
        source = llm_response.metadata.get("source", "unknown") if hasattr(llm_response, "metadata") else "unknown"
        total_tokens_consumed = getattr(llm_response, 'tokens_consumed', 0)

        try:
            all_chats = json.loads(summary_text) if summary_text else []
        except json.JSONDecodeError:
            all_chats = []

        recent_chats = all_chats[-3:]

        # 🔹 Usage snippet
        if thread_id is not None:
            # threading.Thread(
            #     target=background_store_recent,
            #     args=(query, llm_response.content, thread_id, initial_summary)  # pass previous recent chats
            # ).start()
            threading.Thread(
                target=background_store_recent,
                args=(original_query, llm_response.content, thread_id, recent_chats, all_chats)
            ).start()

            final_summary = "Recent conversations are being stored..."
        else:
            logger.info("No thread ID provided, storing current turn only.")
            final_summary = [{"user": original_query.strip(), "ai": llm_response.content.strip()}]
                        
        # Output guardrails: sanitize markdown + add legal disclaimer
        final_content = run_output_guardrails(llm_response.content, task)

        # ✅ Final response (fast return to user)
        return {
            "globalThreadId": thread_id,
            "result": final_content,
            "total_tokens_consumed": total_tokens_consumed,
            "source": source,
            "summary": str(final_summary)
        }


    except Exception as e:
        logger.error(f"Error in llm_answer: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
   