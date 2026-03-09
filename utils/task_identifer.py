from pydantic import BaseModel,Field
from typing import Dict, List, Optional, Literal
#from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import ChatOpenAI

import logging

logger = logging.getLogger(__name__)

from langchain_core.prompts import PromptTemplate


template_task = """You are an expert AI assistant specialized in Indian legal domain analysis and task classification.
INSTRUCTIONS: Analyze the user query and chat summary (Optional) then perform the following steps sequentially:
Identify the PRIMARY legal task from the query. Choose EXACTLY ONE task from the list below:

    **Newacts** → for any of the acts if it is present in the given list.
                a) The Bharatiya Nyaya Sanhita (BNS)
                b) The Bharatiya Nagrik Suraksha Sanhita (BNSS)
                c) The Bharatiya Sakshya Adhiniyam (BSA)
                d) Indian Penal Code (IPC), 1860 
                e) The Criminal Procedure Code (CrPC), 1973
                f) The Indian Evidence Act(IEA), 1872 respectively.

    **Drafting** → Legal document creation, format templates, agreements, contracts, petitions, applications.Draft should be short and to the point.No need to explain Scenario.But with Long context or Scenario Exist then move to Scenario task. If Query is for scenario based draft queries else move Scenario task.
    Keywords: draft, format, suit, template,Criminal Complaint, agreement, contract, petition, application, legal document, Notice, deed, affidavit, will, lease, sale deed, power of attorney, memorandum of understanding (MOU), legal notice
    Negative Keywords: scenario, situation, problem, remedy, consequence, liable, legal advice


    **Legislation** → for retrieving laws or acts (central and state).

    **Constitution** → Constitutional provisions, fundamental rights/duties, directive principles, constitutional bodies
    Keywords: article, fundamental rights, directive principles, constitutional, amendment, supreme court, high court

    **Scenario** → Situational legal query with less context or detailed context given, Scenario based Draft/Notice/petition/application/suit, Real-life legal situation analysis, case studies, legal advice, consequences, remedies
    Keywords: what should I do, legal advice, situation, problem, remedy, consequence, liable

    **Judgment** → Case law, court decisions, precedents, rulings, case citations
    Keywords: case, judgment, court decision, precedent, ruling, Supreme Court case, High Court case

    **Maxim** → Legal principles, Latin phrases, legal doctrines, legal sayings
    Keywords: maxim, principle, doctrine, Latin phrase, legal saying
    Examples: "audi alteram partem", "res ipsa loquitur"

    **Legal_Concepts** → For queries asking about explanations, definitions, or clarifications of legal terms, instruments, or concepts that do not fit into any of the predefined categories.
    Keywords: definition, meaning, explain, what is, legal term, concept, instrument, legal principle, legal mechanism


    **Non_legal** → Queries clearly NOT related to legal matters at all: songs, movies, recipes, personal advice, general knowledge, entertainment, sports, etc.
    Keywords: non-legal, songs, movies, personal advice, general knowledge, recipes, entertainment, sports

    **Other** → Legal-adjacent queries that don't fit neatly into other categories but are still related to law or legal systems.

User Query: {USER_QUERY}
Chat Summary (Optional): {chat_history}
Output:
"""

class IdentifyTaskSchema(BaseModel):
    task: Literal[
        "Drafting",
        "Judgment",
        "Legislation",
        "Constitution",
        "Scenario",
        "Maxim",
        "Newacts",
        "Legal_Concepts",
        "Non_legal",
        "Other"
    ] = Field(..., description="Task type must be one of: Drafting, Judgment, Legislation, Constitution, Scenario, Maxim, Newacts, Legal_Concepts, Non_legal, Other")

def identify_task(query: str, chatSummary: str) -> str:
    prompt_template_task = PromptTemplate.from_template(template_task)
    llm = ChatOpenAI(model="gpt-4o", temperature=0.3)
    prompt_task = prompt_template_task.format(USER_QUERY=query, chat_history=chatSummary)

    logger.info("Identifying task with prompt Started...")
    result = llm.with_structured_output(IdentifyTaskSchema).invoke(prompt_task)
    logger.info("Task identified: %s", result)
    final = result.task
    return final