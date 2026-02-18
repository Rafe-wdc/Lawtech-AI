from langchain_openai import ChatOpenAI
from typing import Dict, List, Optional


def summarize_chat_history(chat_history: List[Dict[str, str]], task: str = None) -> str:
    """
    Summarizes the chat history with legal references, named entities, and key details.
    If the task is 'Drafting', returns the latest draft verbatim (not summarized).

    Args:
        chat_history (list): List of dicts like {'role': 'user'/'assistant', 'content': '...'}.
        task (str, optional): The identified legal task (e.g., 'Drafting').

    Returns:
        str: Structured summary or the exact draft if task is 'Drafting'.
    """
    try:
        # If task is Drafting, return the latest assistant draft verbatim
        if task and task.lower() == "drafting":
            for msg in reversed(chat_history):
                if msg.get("role") == "assistant":
                    return msg.get("content", "")
            return "No draft found in chat history."

        # Otherwise, summarize as before
        history_text = ""
        for msg in chat_history:
            role = msg.get("role", "user").title()
            content = msg.get("content", "")
            history_text += f"{role}: {content}\n"

        prompt = f"""
        You are a legal assistant summarizer.

        Given a detailed multi-turn conversation related to legal queries, summarize the chat with a focus on legal direction, not detail.

        Include an additional section capturing drafting information if the user asks for any draft or requests edits.

        **Guidelines**:
        - Keep summary between 100 to 200 words.
        - Maintain clarity, avoid overly deep legal analysis.
        - Structure the response in 5 standard sections + optional 6th if drafting is involved.
        - If sections are not applicable, write "None mentioned".

        **Format**:
        1. **Topics Discussed**  
        - Key legal subjects or disputes discussed.

        2. **Legal References**  
        - Statutes, sections, amendments, case law.

        3. **Named Entities**  
        - Persons, courts, institutions, estates.

        4. **Dates or Timelines**  
        - Year of law enactment, amendment, or ruling.

        5. **Key Legal Insights or Advice**  
        - Core legal direction or implications.

        6. **Draft Metadata (if any)**  
        - Draft Type:  
        - Personal Info Supplied:  
        - Requested Edits:  

        Chat History:
        {history_text}
        """

        llm = ChatOpenAI(model="gpt-4o", temperature=0.2)
        # with track("Summarizing chat history"):
        response = llm.invoke(prompt)
        return response.content

    except Exception as e:
        return f"Error during summarization: {e}"

