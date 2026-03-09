from pydantic import BaseModel,Field
from langchain_core.prompts import ChatPromptTemplate

from langchain_openai import ChatOpenAI
from langchain_core.documents import Document
from dotenv import load_dotenv
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)

from . import get_es_client

load_dotenv()

_es = None

def _get_es():
    global _es
    if _es is None:
        _es = get_es_client()
    return _es
 
 
class CaseMetadata(BaseModel):
    petitioner_names: Optional[List[str]] = Field(None, description="List of petitioners in the case")
    respondent_names: Optional[List[str]] = Field(None, description="List of respondents in the case")
    case_number: Optional[str] = Field(None, description="Normalized digits-only case number (e.g., '311950', '232023')")
    year: Optional[int] = Field(None, description="The 4-digit year of the case")
 
prompt_template = """
You are an expert legal text parser.
The user may ask a query in natural language containing a case title and number.
Your task is to extract structured details.
 
Return output strictly in JSON with the following keys:
- petitioner_names: list of petitioners
- respondent_names: list of respondents
- case_number: normalized digits only (e.g., "31 of 1950" → "311950", "23/2034" → "232034", "AB/34/2021" → "342021")
- year: the 4-digit year (always extract the year from the case number or case title)
 
Rules:
1. The party before "Versus", "Vs", or "v." is petitioner.
2. The party after "Versus", "Vs", or "v." is respondent(s).
3. Keep names containing '&' (like "Ved Prakash & Ors.") intact — do NOT split them.
4. Extract all digits from the case number phrase and concatenate them in order → this is case_number.
5. Year is always the last 4-digit number present.
6. If something is missing, leave the value as an empty string or empty list.
User query:
{query}
 
Output only valid JSON, no explanations.
"""
 
def query_metadata_llm(query):
    llm = ChatOpenAI(temperature=0.3, model="gpt-4o").with_structured_output(CaseMetadata)
    QUERY_PROMPT=ChatPromptTemplate.from_template(prompt_template)
    llm_chain = QUERY_PROMPT | llm
    res = llm_chain.invoke({'query':query})
    return res
 
 
def build_query(metadata, query_text=None, size=1):
    """
    Build Elasticsearch query for case metadata search using multi-tiered approach with filters:
   
    Tiers:
        1. Exact keyword match
        2. Fuzzy match
        3. Autocomplete match
        4. Page content fallback
        5. Fallback multi_match (full query text)
   
    Filters:
        - case_number
        - year
    """
    should_clauses = []
    filters = []
 
    petitioner_names = metadata.petitioner_names or []
    respondent_names = metadata.respondent_names or []
 
    # ---------------------------
    # TIER 1: Exact Keyword Match
    # ---------------------------
    if petitioner_names and respondent_names:
        for petitioner in petitioner_names:
            for respondent in respondent_names:
                should_clauses.append({
                    "bool": {
                        "must": [
                            {"term": {"petitioner_names.keyword": {"value": petitioner, "boost": 200}}},
                            {"term": {"respondent_names.keyword": {"value": respondent, "boost": 200}}}
                        ]
                    }
                })
 
    # ---------------------------
    # TIER 2: Strict Fuzzy Matching
    # ---------------------------
    if petitioner_names and respondent_names:
        for petitioner in petitioner_names:
            for respondent in respondent_names:
                should_clauses.append({
                    "bool": {
                        "must": [
                            {"match": {"petitioner_names": {"query": petitioner, "fuzziness": "AUTO", "boost": 15}}},
                            {"match": {"respondent_names": {"query": respondent, "fuzziness": "AUTO", "boost": 15}}}
                        ]
                    }
                })
 
    # ---------------------------
    # TIER 3: Autocomplete
    # ---------------------------
    if petitioner_names and respondent_names:
        for petitioner in petitioner_names:
            for respondent in respondent_names:
                should_clauses.append({
                    "bool": {
                        "must": [
                            {"match": {"petitioner_names.autocomplete": {"query": petitioner, "boost": 10}}},
                            {"match": {"respondent_names.autocomplete": {"query": respondent, "boost": 10}}}
                        ]
                    }
                })
 
    # ---------------------------
    # TIER 4: Page Content Fallback
    # ---------------------------
    if petitioner_names and respondent_names:
        for petitioner in petitioner_names:
            for respondent in respondent_names:
                should_clauses.append({
                    "bool": {
                        "must": [
                            {"match_phrase": {"page_content": {"query": petitioner, "boost": 5}}},
                            {"match_phrase": {"page_content": {"query": respondent, "boost": 5}}}
                        ]
                    }
                })
 
    # ---------------------------
    # TIER 5: Fallback multi_match
    # ---------------------------
    if query_text:
        should_clauses.append({
            "match": {
                "page_content": {"query": query_text, "boost": 8}
            }
        })
 
    # ---------------------------
    # Filters
    # ---------------------------
    if metadata.case_number:
        filters.append({"term": {"case_number": metadata.case_number}})
    if metadata.year:
        filters.append({"term": {"year": metadata.year}})
 
    query_body = {
        "size": size,
        "query": {
            "bool": {
                "should": should_clauses or [],
                "filter": filters or [],
                "minimum_should_match": 1
            }
        }
    }
 
    return query_body
 
 
class HCJudgementRetriever:
    def __init__(self):
        pass
    def retrieve_documents(self,query: str):
        try:
            query_metadata = query_metadata_llm(query)
            logger.debug("Query metadata: %s", query_metadata)

            es_query = build_query(metadata= query_metadata,query_text= query,)
            logger.debug("ES query: %s", es_query)

            source_response = _get_es().search(index="judgements", body=es_query)
            langchain_docs = []
            for hit in source_response["hits"]["hits"]:
                content = hit["_source"]["page_content"]
                source = {"source": hit["_source"]["source"]}
                court = hit["_source"]["court_name"]
                respondent_names = hit["_source"]["respondent_names"][0] if hit["_source"].get("respondent_names") else "Unknown"
                petitioner_names = hit["_source"]["petitioner_names"][0] if hit["_source"].get("petitioner_names") else "Unknown"
                title = f"{petitioner_names} vs {respondent_names}"
                metadata = {"source": source, "source_name": title,"court":court}
                doc = Document(page_content=content, metadata=metadata)
                langchain_docs.append(doc)
            return langchain_docs
        except Exception as e:
            logger.error("HC Judgement retrieval failed: %s", e)
            return []

 