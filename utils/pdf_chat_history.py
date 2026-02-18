import json
import os
from typing import List, Tuple
from langchain_core.messages import HumanMessage, AIMessage

CHROMA_STORE_ROOT = "./chroma_store"


def _get_history_path(unique_string: str) -> str:
    """Get the chat history JSON file path for a given uniqueString."""
    return os.path.join(CHROMA_STORE_ROOT, unique_string, "chat_history.json")


def load_pdf_chat_history(unique_string: str) -> Tuple[List, List[dict]]:
    """
    Load chat history from local JSON file for a PDF session.

    Returns:
        chat_history: List of HumanMessage/AIMessage for LLM context (last 5 turns)
        all_chats: Raw list of {user, ai} dicts (full history)
    """
    chat_history = []
    all_chats = []

    history_path = _get_history_path(unique_string)

    if not os.path.exists(history_path):
        return chat_history, all_chats

    try:
        with open(history_path, "r", encoding="utf-8") as f:
            all_chats = json.load(f)

        if isinstance(all_chats, list):
            for turn in all_chats[-5:]:  # last 5 turns for LLM context
                chat_history.append(HumanMessage(content=turn.get("user", "")))
                chat_history.append(AIMessage(content=turn.get("ai", "")))
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error loading PDF chat history for {unique_string}: {e}")
        all_chats = []

    return chat_history, all_chats


def save_pdf_chat_history(unique_string: str, question: str, answer: str, all_chats: list):
    """
    Append current turn to chat history and save to local JSON file.

    Args:
        unique_string: The PDF session identifier
        question: User's question (original, not rewritten)
        answer: AI's response (raw, pre-guardrails)
        all_chats: Existing full chat history list
    """
    history_path = _get_history_path(unique_string)

    try:
        # Append new turn
        all_chats.append({"user": question.strip(), "ai": answer.strip()})

        # Ensure directory exists
        os.makedirs(os.path.dirname(history_path), exist_ok=True)

        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(all_chats, f, ensure_ascii=False, indent=2)

        print(f"PDF chat history saved for {unique_string} ({len(all_chats)} turns)")

    except IOError as e:
        print(f"Error saving PDF chat history for {unique_string}: {e}")
