from pydantic import BaseModel,Field
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.documents import Document

# output schema

class Relevance_Result(BaseModel):
    isrelevant: str = Field(..., description="Yes / No")

def check_relevance(user_intent: str, retrieved_chunks: list[Document],task:str) -> dict:
    """
    Checks which draft among multiple retrieved is most relevant to the user's intent.
 
    Args:
        user_intent (str): The user's query/intent.
        retrieved_chunks (list[str]): List of retrieved draft chunks.
 
    Returns:
        dict: {
            'isrelevant': str,
        }
    """
    # Drafts are enumerated here
    numbered_chunks = "\n".join([f"{i+1}. {chunk}" for i, chunk in enumerate(retrieved_chunks)])
 
    validation_rule ={
        'Drafting': "Verify the document is a proper legal format/template matching the query requirements.",
        'Legislation': "Verify that this chunk contains the official or near-identical wording of a legislation/act, with correct section numbering, and that at least one section matches the user's requested topic or section number.",
        'Constitution': "Verify that this chunk contains content from a constitution, including properly cited articles or principles, and that at least one article matches the user's request.",
        'Newacts': """Verify that this chunk contains the official or near-identical text of one of the following specific acts, with correct section numbering, and that at least one section matches the user's request:
              BNS ? The Bharatiya Nyaya Sanhita, 2023
              BNSS ? The Bharatiya Nagarik Suraksha Sanhita, 2023
              BSA ? The Bharatiya Sakshya Adhiniyam, 2023
              CrPC ? The Code of Criminal Procedure, 1973
              IPC ? The Indian Penal Code, 1860; IEA
              The Indian Evidence Act, 1872.
              """,
        'Judgment': "Verify that this chunk contains text from a court judgment. Since judgments are very large documents, look for ANY of the following indicators: case title, party names, case number, court name, judge name(s), citation, legal issues, facts mentioned, arguments, legal principles discussed, precedents cited, reasoning, or final order/decision. Even if the chunk contains only a small portion of relevant content matching the user's query topic, mark it as relevant. Be lenient and favor relevance when there is any reasonable connection to the user's intent.",
        'Maxim': "Verify that this chunk explains a legal maxim or doctrine, including its Latin term, meaning, and legal interpretation."
    }
 
    prompt = f"""
    You are a legal AI system responsible for evaluating the relevance of retrieved legal text to the user's intent.
 
    User Intent:
    {user_intent}
 
    Retrieved Chunks (numbered):
    {numbered_chunks}
 
    Validation Rules (category-specific checks):
    {validation_rule.get(task, "Verify that the retrieved content is relevant to the user's query and provides useful legal information.")}
 
    Instruction for Thinking:
        - Read the user intent carefully and understand what category it belongs to.
        - Read each retrieved chunk in order.
        - Apply the relevant validation rule to check if the chunk meets the category requirements.
        - A chunk is considered relevant if it satisfies both the user's request and the applicable validation rule.
        - The final answer should be "Yes" if at least one chunk is relevant, otherwise "No".
    Do not reveal reasoning.
 
    Answer format:
    Final Answer: Yes / No
    """
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro" ,temperature=0.3).with_structured_output(Relevance_Result)
   
    completion = llm.invoke(prompt)
 
    return completion