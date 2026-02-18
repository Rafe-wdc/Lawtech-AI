# import requests
# from .summarize_chat_history import summarize_chat_history

# def background_summary_and_store(query, llm_content, thread_id, initial_summary, task):
#     try:
#         if task in ["Drafting", "Customization"]:
#             final_summary_bg = llm_content.strip()
#         else:
#             combined_text = (
#                 f"{initial_summary.strip() if initial_summary else ''}\n\n"
#                 f"User Query:\n{query.strip()}\n\nAI Response:\n{llm_content.strip()}"
#             )
#             formatted_input = [{"role": "user", "content": combined_text}]
#             final_summary_bg = summarize_chat_history(formatted_input, task=task).strip()

#         # 📬 Send POST to store summary
#         if thread_id:
#             update_url = "https://lawttorney.ai/api/users/storechatSummary"
#             payload = {
#                 "ThreadId": thread_id,
#                 "chatSummarys": final_summary_bg
#             }
#             update_response = requests.post(update_url, json=payload)

#             if update_response.status_code == 200:
#                 print("✅ Summary successfully updated in thread history.")
#             else:
#                 print(f"⚠️ Failed to update summary. Status: {update_response.status_code}")

#     except Exception as e:
#         print(f"❌ Error in background_summary_and_store: {e}")
import requests

import json
def background_store_recent_conversations(query, llm_content, thread_id, recent_chats=None):
    """
    Stores recent chat exchanges (2-3 turns) without summarizing.

    :param query: Current user query
    :param llm_content: Current AI response
    :param thread_id: Thread identifier
    :param recent_chats: List of previous exchanges (optional)
    """
    try:
        # Maintain last 2-3 conversation turns
        recent_chats = recent_chats or []
        recent_chats.append({"user": query.strip(), "ai": llm_content.strip()})
        if len(recent_chats) > 3:  # Keep only last 3 conversations
            recent_chats = recent_chats[-3:]

        if thread_id:
            update_url = "https://lawttorney.ai/api/users/storechatSummary"
            payload = {
                "ThreadId": thread_id,
                "chatSummarys": recent_chats  # send list of conversation turns
            }
            update_response = requests.post(update_url, json=payload)

            if update_response.status_code == 200:
                print("✅ Recent conversations successfully stored in thread history.")
            else:
                print(f"⚠️ Failed to store recent conversations. Status: {update_response.status_code}")

        return recent_chats  # return updated conversation log for next call

    except Exception as e:
        print(f"❌ Error in background_store_recent_conversations: {e}")
        return recent_chats
import json, requests

def background_store_recent(query, llm_content, thread_id, recent_chats=None, all_chats=None):
    try:
        # Ensure lists
        if isinstance(recent_chats, str):
            try:
                recent_chats = json.loads(recent_chats)
            except json.JSONDecodeError:
                recent_chats = []
        elif not isinstance(recent_chats, list):
            recent_chats = []

        if isinstance(all_chats, str):
            try:
                all_chats = json.loads(all_chats)
            except json.JSONDecodeError:
                all_chats = []
        elif not isinstance(all_chats, list):
            all_chats = []

        # New turn
        new_turn = {"user": query.strip(), "ai": llm_content.strip()}
        recent_chats.append(new_turn)
        all_chats.append(new_turn)

        # Keep only last 3 turns for "recent"
        if len(recent_chats) > 3:
            recent_chats = recent_chats[-3:]

        # Store **all conversations** to API
        if thread_id:
            update_url = "https://lawttorney.ai/api/users/storechatSummary"
            payload = {
                "ThreadId": thread_id,
                "chatSummarys": json.dumps(all_chats, ensure_ascii=False)
            }
            response = requests.post(update_url, json=payload)
            if response.status_code == 200:
                print("✅ All conversations stored successfully.")
            else:
                print(f"⚠️ Failed to store conversations. Status: {response.status_code}")

        # Return both for next call
        return recent_chats, all_chats

    except Exception as e:
        print(f"❌ Error in background_store_recent: {e}")
        return recent_chats or [], all_chats or []