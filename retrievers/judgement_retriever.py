import os
from langchain_core.documents import Document
from typing import List, Optional
from collections import Counter
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from .hc_judgement_retriever import HCJudgementRetriever
from . import get_es_client
class CaseMetadata(BaseModel):
    court_name: Optional[str] = Field(
        default=None,
        description="Name of the court (supreme court, [location] high court, tribunal, etc.)"
    )
    petitioner_names: Optional[List[str]] = Field(
        default_factory=list,
        description="List of petitioner names extracted from the query"
    )
    respondent_names: Optional[List[str]] = Field(
        default_factory=list,
        description="List of respondent names extracted from the query"
    )
    year: Optional[int] = Field(
        None,
        description="The 4-digit year mentioned in the query, if any"
    )
    topics: Optional[List[str]] = Field(
        default_factory=list,
        description="List of general legal topics or issues (e.g., 'murder', 'bail', 'negligence')"
    )
    acts_or_sections: Optional[List[str]] = Field(
        default_factory=list,
        description="List of legal provisions or sections mentioned (e.g., 'section 138 ni act', 'section 482 crpc')"
    )
    lexical_query: Optional[str] = Field(
        None,
        description="Normalized lexical search string constructed for Elasticsearch BM25 retrieval"
    )
    size: Optional[int] = Field(
        3,
        description=(
            "Number of results to retrieve from Elasticsearch. "
            "Defaults to 3. Set dynamically based on query specificity: "
            "1 for exact party match, 5 for section-based queries, 10 for topic-based queries."
        )
    )


prompt_template = """
You are an expert in **legal text parsing and Elasticsearch query formulation**.
 
The user may provide a natural language query that could include:
- A **case title** (with parties and/or year), or
- A **legal topic** (e.g., “case laws on murder”, “judgments under Section 420 IPC”), or
- A **court-specific request** (e.g., “Supreme Court judgments”, “Bombay High Court bail cases”).
 
Your task is to extract **structured metadata** and generate a **normalized lexical search string**
that can be used for Elasticsearch BM25 retrieval.
 
---
 
### 🎯 Expected JSON Output
Return output **strictly in valid JSON** with the following keys:
 
- court_name : Name of the court (string)
- petitioner_names: list of petitioners (strings)
- respondent_names: list of respondents (strings)
- year: 4-digit year (integer or null)
- topics: list of general legal topics (e.g., “murder”, “negligence”)
- acts_or_sections: list of legal provisions (e.g., “section 2 ipc”, “article 21 constitution”)
- lexical_query: normalized string for lexical (BM25) search
- size: integer (number of documents to retrieve from Elasticsearch)
 
---
 
### ⚖️ Rules for Extraction
 
1. **Court Name Detection**
   - Identify mentions of courts such as:
     - "supreme court"
     - "[location] high court" (e.g., "Bombay High Court", "Delhi High Court")
     - "district court", "family court", "consumer court", "tribunal", "NCLT", "NCLAT", "ITAT", "CAT", "DRT", etc.
   - Recognize abbreviations:
     - “SC” → “supreme court”
     - “HC” or “Delhi HC” → “delhi high court”
   - Normalize to lowercase and remove trailing words like “of India” or “at”.
     - Example: “Supreme Court of India” → “supreme court”
     - Example: “High Court of Delhi” → “delhi high court”
   - If no court is mentioned, output as an empty string (`""`).
 
2. The party appearing **before** “Versus”, “Vs”, or “v.” → petitioner(s).  
   The party **after** → respondent(s).
 
3. Preserve multi-word names (e.g., “Ved Prakash & Ors.”) — do **not** split them.
 
4. Normalize “v.”, “vs”, “Vs” → “versus”.
 
5. If no parties are detected:
   - Identify main **legal topic(s)** or **statutory references**.
   - Use those to populate `topics` and `acts_or_sections`.
 
6. If a legal provision is mentioned (e.g., “Section 2 of IPC”, “Order 7 Rule 11 CPC”):
   - Extract under `acts_or_sections`.
   - Normalize to lowercase, remove “of”, “under”, punctuation, and join with spaces.
     - Example: “Section 482 of CrPC” → “section 482 crpc”.
 
7. Extract general legal subjects under `topics` (e.g., “murder”, “bail”, “defamation”).
 
8. **Construct the `lexical_query`**:
   - If both petitioner and respondent exist →  
     `"{{petitioner_names}} versus {{respondent_names}} {{year}}"`.
   - If both `acts_or_sections` and `topics` exist →  
     `"{{acts_or_sections}} {{topics}}"`.
   - If only one of them exists, use it directly.
   - Include `court_name` at the start of `lexical_query` if present, **unless** it already appears in party names.
   - Lowercase, remove punctuation, and collapse multiple spaces.
 
9. **Remove filler or intent words** like “give me”, “find”, “show”, “tell”, “list”, “latest”, “recent”, “important”, “famous”, “landmark”, etc.
 
10. **Normalize `lexical_query`:**
    - Convert to lowercase, strip punctuation, and remove duplicate spaces.
 
11. **Explicit `size` detection:**
    - If the user mentions numbers (“top 5”, “show 10”, “first 20”, “only 1”), extract that integer as `size`.
    - Words like “one”, “single”, or “best” → `size = 1`.
    - Clamp to [1, 50]: if user asks for >50 → set to 50.
 
12. **Heuristic `size` determination** (only if no explicit size found):
    - Both petitioner & respondent present → `size = 1`
    - If `acts_or_sections` exist → `size = 5`
    - If only `topics` exist → `size = 10`
    - Otherwise → `size = 3` (default)
 
13. **Conflict rule:**  
    If both explicit and heuristic rules match, the **explicit user request overrides** the heuristic.
 
14. If a field cannot be found, output it as:
    - `""` for strings  
    - `[]` for lists  
    - `null` for year
 
15. **Output Format Enforcement**
    - Output only valid JSON (no markdown, no ```json``` fences).
    - Maintain field order as in Expected JSON Output.
 
---
 
### 🧠 Examples
 
**Example 1 — Court + Statutory Reference**
> "Supreme Court judgments under Section 138 of NI Act"
 
**Expected JSON output:**
{{
  "court_name": "supreme court",
  "petitioner_names": [],
  "respondent_names": [],
  "year": null,
  "topics": [],
  "acts_or_sections": ["section 138 ni act"],
  "lexical_query": "supreme court section 138 ni act",
  "size": 5
}}
 
---
 
**Example 2 — High Court + Topic**
> "Bombay High Court cases on negligence"
 
**Expected JSON output:**
{{
  "court_name": "bombay high court",
  "petitioner_names": [],
  "respondent_names": [],
  "year": null,
  "topics": ["negligence"],
  "acts_or_sections": [],
  "lexical_query": "bombay high court negligence",
  "size": 10
}}
 
---
 
**Example 3 — Case Title with Year**
> "State of Maharashtra Vs John Doe 2020"
 
**Expected JSON output:**
{{
  "court_name": "",
  "petitioner_names": ["State of Maharashtra"],
  "respondent_names": ["John Doe"],
  "year": 2020,
  "topics": [],
  "acts_or_sections": [],
  "lexical_query": "state of maharashtra versus john doe 2020",
  "size": 1
}}
 
---
 
**User query:**  
{query}
 
**Output only valid JSON.**
"""
 
 
 
 
def query_metadata_llm(query: str):
    llm = ChatOpenAI(temperature=0.3, model="gpt-4o").with_structured_output(CaseMetadata)
    QUERY_PROMPT = ChatPromptTemplate.from_template(prompt_template)
    llm_chain = QUERY_PROMPT | llm
    res = llm_chain.invoke({'query': query})
    return res
 
def build_query(metadata, query_text=None, size=1):
    """
    Build an Elasticsearch BM25 query for legal judgments using multiple tiers:
   
    Tiers:
        1. Party-based exact match (if petitioner/respondent names exist)
        2. Fuzzy match (for slightly misspelled names)
        3. Keyword/topic-based search (from 'topics')
        4. Statutory reference search (from 'acts_or_sections')
        5. Lexical fallback (from LLM-generated lexical_query)
        6. Full-text fallback (page_content)
   
    Filters:
        - year (if provided)
    """
    should_clauses = []
    filters = []
 
    # Extract metadata fields
    petitioner_names = metadata.petitioner_names or []
    respondent_names = metadata.respondent_names or []
    topics = metadata.topics or []
    acts_or_sections = metadata.acts_or_sections or []
    lexical_query = metadata.lexical_query or query_text
 
    # ---------------------------
    # TIER 1: Exact Party Match
    # ---------------------------
    if petitioner_names and respondent_names:
        for petitioner in petitioner_names:
            for respondent in respondent_names:
                should_clauses.append({
                    "bool": {
                        "must": [
                            {"match": {"petitioner_names": {"query": petitioner, "boost": 20, "minimum_should_match": "95%"}}},
                            {"match": {"respondent_names": {"query": respondent, "boost": 20, "minimum_should_match": "95%"}}}
                        ]
                    }
                })
 
    # ---------------------------
    # TIER 2: Fuzzy Party Match
    # ---------------------------
    # if petitioner_names and respondent_names:
    #     for petitioner in petitioner_names:
    #         for respondent in respondent_names:
    #             should_clauses.append({
    #                 "bool": {
    #                     "must": [
    #                         {"match": {"petitioner_names": {"query": petitioner, "fuzziness": "AUTO", "boost": 15}}},
    #                         {"match": {"respondent_names": {"query": respondent, "fuzziness": "AUTO", "boost": 15}}}
    #                     ]
    #                 }
    #             })
 
    # ---------------------------
    # TIER 3: Topics (Legal Issues)
    # ---------------------------
    for topic in topics:
        should_clauses.append({
            "match": {"keywords": {"query": topic, "boost": 15, "minimum_should_match": "95%"}}
        })
 
    # ---------------------------
    # TIER 4: Acts / Sections Invoked
    # ---------------------------
    for section in acts_or_sections:
        should_clauses.append({
            "match": {"acts_or_sections_invoked": {"query": section, "boost": 15, "minimum_should_match": "95%"}}
        })
 
    # ---------------------------
    # TIER 5: Lexical Query (LLM normalized)
    # ---------------------------
    if lexical_query:
        should_clauses.append({
            "match": {"page_content": {"query": lexical_query, "boost": 8, "minimum_should_match": "95%"}}
        })
 
    # ---------------------------
    # TIER 6: Fallback - full text
    # ---------------------------
    if query_text and not lexical_query:
        should_clauses.append({
            "match": {"page_content": {"query": query_text, "boost": 3,}}
        })
 
    # ---------------------------
    # Filters
    # ---------------------------
    if metadata.year:
        filters.append({"term": {"year": metadata.year}})
    if metadata.court_name:
        filters.append({"term": {"court_name": metadata.court_name.lower()}})
 
    # ---------------------------
    # Final Query Body
    # ---------------------------
    query_body = {
        "size": metadata.size if metadata.size else size,
        "query": {
            "bool": {
                "should": should_clauses,
                "filter": filters if filters else [],
                "minimum_should_match": 1
            }
        }
    }
 
    return query_body
 
class JudgementRetriever:
    def __init__(self):
        pass
    def retrieve_documents(self,query: str,top_k=17) -> List[Document]:
        try: 
            query_metadata = query_metadata_llm(query)
            print(query_metadata)
    
            es_query = build_query(metadata= query_metadata,query_text= query,)
            print(es_query)
            es = get_es_client()

            source_response = es.options(request_timeout=50).search(index="judgements", body=es_query)
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
            print(langchain_docs)
            return langchain_docs
        except Exception as e:
            print(f"⚠️ Hybrid search failed: {e}")
            return []