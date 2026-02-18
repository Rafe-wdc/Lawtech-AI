from pydantic import BaseModel,Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.documents import Document

from dotenv import load_dotenv
from typing import Optional, List
from . import get_es_client

load_dotenv()
 
# NOTE: These source paths must match the 'source' field in the ES "newacts_v1" index.
# If retrieval returns empty results, verify these paths against actual ES data with:
#   es.search(index="newacts_v1", body={"size":1, "query":{"match_all":{}}})["hits"]["hits"][0]["_source"]["source"]
acts_paths = {
    "The Bharatiya Nyaya Sanhita, 2023" : "Bharitya New Acts/The Bharatiya Nyaya Sanhita,2023.csv",
    "The Bharatiya Nagarik Suraksha Sanhita, 2023" : "Bharitya New Acts/The Bharatiya Nagarik Suraksha Sanhita, 2023.csv",
    "The Bharatiya Sakshya Adhiniyam, 2023" : "Bharitya New Acts/The Bharatiya Sakshya Adhiniyam, 2023.csv",
    "The Code of Criminal Procedure 1973" : "Bharitya New Acts/Code of Criminal Procedure 1974.csv",
    "The Indian Penal Code, 1860" : "Bharitya New Acts/Indian Penal Code 1860.csv",
    "Indian Evidence Act 1872" : "Bharitya New Acts/Indian Evidence Act 1872.csv"
}
class QueryMetadata(BaseModel):
    section_number: Optional[List[str]] = Field(None, description="List of section numbers, normalized per rules.")
    act_name: Optional[str] = Field(None, description="Exact act name from allowed mapping.")
    hybrid_search: Optional[bool] = Field(False, description="Whether to use semantic + keyword hybrid search.")
 
prompt_template = """
You are a legal AI assistant specialized in analyzing user queries to extract act metadata.
 
---
 
## Task
From the user query, extract:
1. **section_number** — list of section numbers, normalized (per rules) or null.
2. **act_name** — exact name from allowed mapping or null.
3. **hybrid_search** — boolean (per rules).
 
---
 
## Rules for `section_number`
- Always return an array of strings, or null if none found.
- If number contains parentheses (e.g., `20(1)`, `20(A)`, `20(1)(a)`), remove parentheses and contents → `20`.
- Keep formats like `20A` or `20-B` unchanged.
- If multiple sections are mentioned, list them in the array.
- If none found → null.
 
---
 
## Rules for `act_name`
- Match **case-insensitively**.
- Allow **minor typos** in acronyms (Levenshtein distance ≤ 1) if unambiguous.
  - Examples: `BNNS` → BNSS, `BAS` → BSA, `IEA` → IEA.
- Only match acts from the allowed mapping — do **not** guess unrelated acts.
- If no confident match → null.
 
---
 
## Rules for `hybrid_search` (priority order)
1. **true** if:
   - Query is vague/conceptual (e.g., “law about theft”), OR
   - Query asks for provisions which users looking for.
2. **false** if query contains both:
   - Exact section number(s) (per rules), AND
   - Act name/acronym from allowed mapping (clear identification, no ambiguity).
3. **false** if query contains only act name/acronym (clear identification).
4. Else → **true**.
 
---
 
## Allowed mapping (case-insensitive + fuzzy)
- BNS  → The Bharatiya Nyaya Sanhita, 2023  
- BNSS → The Bharatiya Nagarik Suraksha Sanhita, 2023  
- BSA  → The Bharatiya Sakshya Adhiniyam, 2023  
- CrPC → The Code of Criminal Procedure 1973  
- IPC  → The Indian Penal Code, 1860  
- IEA  → Indian Evidence Act 1872  
 
---
 
## Thinking Constraint
Privately:
1. Extract and normalize section_number list.
2. Identify act_name from allowed mapping (case-insensitive, typo-tolerant).
3. Decide hybrid_search flag using priority rules.
Do not reveal reasoning.
 
---
 
User query: {query}
 
---
 
Return **only** valid JSON in this exact format:
{{
  "section_number": ["<string>", ...] or null,
  "act_name": "<string or null>",
  "hybrid_search": true/false
}}
 
---
 
### Example 1
Query: Section 20(1) of bnss  
Output:
{{
  "section_number": ["20"],
  "act_name": "The Bharatiya Nagarik Suraksha Sanhita, 2023",
  "hybrid_search": false
}}
 
### Example 2
Query: bNs  
Output:
{{
  "section_number": null,
  "act_name": "The Bharatiya Nyaya Sanhita, 2023",
  "hybrid_search": false
}}
 
### Example 3
Query: Section 5 of BNNS  
Output:
{{
  "section_number": ["5"],
  "act_name": "The Bharatiya Nagarik Suraksha Sanhita, 2023",
  "hybrid_search": false
}}
"""
def select_file_path_metadata(query):
    llm = ChatOpenAI(temperature=0.3, model="gpt-4o").with_structured_output(QueryMetadata)
    QUERY_PROMPT=ChatPromptTemplate.from_template(prompt_template)
    llm_chain = QUERY_PROMPT | llm
    res = llm_chain.invoke({'query':query})
    return res

from . import instructor_embeddings as embedding_model
 
def build_query(metadata: QueryMetadata, query_text=None):
    filters = []
 
    # Act name filter
    if metadata.act_name:
        filters.append({
            "term": {
                "source": acts_paths[metadata.act_name]
            }
        })
 
    # Section number filter (exact match, keyword type)
    if metadata.section_number:
        filters.append({
            "terms": {
                "section_number": metadata.section_number
            }
        })
 
    # Hybrid search case (BM25 + vector search)
    if metadata.hybrid_search and query_text:
        query_vector = embedding_model.embed_query(query_text)
        return {
            "size": 20,
            "query": {
                "script_score": {
                    "query": {
                        "bool": {
                            "should": [
                                {"match": {"page_content": query_text}},
                                {"term": {"source": acts_paths.get(metadata.act_name, "")}}
                                if metadata.act_name else {}
                            ],
                            "filter": filters
                        }
                    },
                    "script": {
                        "source": """
                            double bm25 = _score;
                            double vector_score = cosineSimilarity(params.query_vector, 'embedding');
                            return bm25 + (100 * vector_score);  // weight vector higher
                        """,
                        "params": {
                            "query_vector": query_vector
                        }
                    }
                }
            },
            "sort": [
                {"_score": {"order": "desc"}},   # combined relevance
                {"section_number": {"order": "asc"}}
            ]
        }
 
    # Non-hybrid search case
    return {
        "query": {
            "bool": {
                "must": [],
                "filter": filters
            }
        },
        "size": 10,
        "sort": [
            {"section_number": {"order": "asc"}}
        ],
    }
 
 
class NewactsRetriever:
    def __init__(self):
        pass
 
    def retrieve_documents(self, query):
        try:
            query_metadata = select_file_path_metadata(query)
            print(query_metadata)
            es = get_es_client()
            es_query = build_query(
                metadata=query_metadata,
                query_text=query,
            )
            print(es_query)
            source_response = es.search(index="newacts_v1", body=es_query)
            langchain_docs = []
            for hit in source_response["hits"]["hits"]:
                content = hit["_source"]["page_content"]
                metadata = {"source": hit["_source"]["source"]}
                doc = Document(page_content=content, metadata=metadata)
                langchain_docs.append(doc)
            return langchain_docs
        except Exception as e:
            print(f"Error occurred while retrieving documents: {e}")
            return []
 