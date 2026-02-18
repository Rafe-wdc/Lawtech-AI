import requests
from langchain_core.messages import HumanMessage, AIMessage
import json
def get_chat_history_from_thread(thread_id):
    """
    Fetch chat history summary from external API and convert to Human/AI message format.
    """
    chat_history = []
    summary_text = ""

    try:
        url = f"https://lawttorney.ai/api/users/getChatSummary/{thread_id}"
        response = requests.get(url)
        print(response)

        if response.status_code == 200:
            res_json = response.json()
            print("res_json:", res_json)
            if res_json.get("status") and res_json.get("data"):
                summary_text = res_json["data"].get("chatSummary", "").strip()
    except Exception as e:
        print(f"Error fetching chat summary for thread {thread_id}: {e}")
    print("summary_text:", summary_text)

    if summary_text:
        try:
            parsed = json.loads(summary_text)  # check if it’s JSON list
            if isinstance(parsed, list):
                for turn in parsed[-5:]:  # last 5 turns for context
                    chat_history.append(HumanMessage(content=turn.get("user", "")))
                    chat_history.append(AIMessage(content=turn.get("ai", "")))
            else:
                # fallback to plain text
                chat_history.append(HumanMessage(content="Previous summary:"))
                chat_history.append(AIMessage(content=summary_text))
        except json.JSONDecodeError:
            chat_history.append(HumanMessage(content="Previous summary:"))
            chat_history.append(AIMessage(content=summary_text))
    else:
        chat_history.append(HumanMessage(content="Previous summary:"))
        chat_history.append(AIMessage(content="Fresh chat started."))

    return chat_history, summary_text