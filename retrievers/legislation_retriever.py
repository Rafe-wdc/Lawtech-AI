from pydantic import BaseModel,Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.documents import Document

from collections import Counter
from dotenv import load_dotenv
from typing import Optional
import logging

logger = logging.getLogger(__name__)

from . import get_es_client

load_dotenv()
 
 
class QueryMetadata(BaseModel):
    match_phrase: Optional[str] = Field(None, description="Give 'match_phrase' clause")
    sub_part: Optional[str] = Field(None, description="Give any sub part of act with name")
 
 
def match_phrase_extraction(query,text,title):
    llm = ChatOpenAI(temperature=0.3, model="gpt-4o-mini").with_structured_output(QueryMetadata)
    prompt_template = """
    # Role
    You are a legal document search expert skilled in structured data extraction and Elasticsearch query formatting.
 
    # Objective
    Given:
    - An act title,
    - A legal provision context (which contains a provision type like Rule, Section, Clause, etc.),
    - A user query (which may contain a different provision type or number),
 
    You must:
    - Use the **provision type** (e.g., Rule, Section) **from the context**.
    - Use the **provision number** (e.g., 1, 2, etc.) **from the user query**.
    - Combine them with the act title to create a normalized Elasticsearch `match_phrase` value.
    - Also output the `subpart` (e.g., "Rule 1") using the correct provision type and user query number.
 
    # Constraints
    - Do NOT summarize, extract, or interpret the rule content.
    - Output only a JSON object with the fields:
    - `"match_phrase"`: A full legal reference phrase (e.g., "Rule 1 of Maharashtra Rent Control Rules, 2017")
    - `"subpart"`: A short phrase like "Rule 1"
    - Do NOT use the provision type from the user query if it differs from context.
    - Always wrap values in double quotes.
    - No explanations or commentary.
 
    # Input
 
    Title:
    "{title}"
 
    Context:
    {text}
 
    User Query:
    "{query}"
 
    # Output (JSON only)
    {{
    "match_phrase": "<use context type + user number + title>",
    "subpart": "<use context type + user number>"
    }}
    """
    QUERY_PROMPT=ChatPromptTemplate.from_template(prompt_template)
    llm_chain = QUERY_PROMPT | llm
    res = llm_chain.invoke({'query':query,'text':text, 'title':title})
    return res
 
 
def build_query(metadata: QueryMetadata, file_path:str, title:str):
    must_clauses = []
    filters = [
        {
            "term": {
                "source.keyword": file_path
            }
        }
    ]
    # Build must clause based on available data
    if metadata.match_phrase:
        must_clauses.append({
            "match_phrase": {
                "page_content": metadata.match_phrase
            }
        })
    elif title:
        must_clauses.append({
            "match_phrase": {
                "page_content": title
            }
        })
    query = {
        "query": {
            "bool": {
                "must": must_clauses,
                "filter": filters
            }
        },
        "size": 10
    }
    return query

import re
from collections import Counter
from elasticsearch import Elasticsearch, helpers
from langchain_core.documents import Document
import time
# Define all possible section types
section_types = [
    'section', 'rule', 'rules', 'order', 'regulation', 'scheme', 'procedure',
    'policy', 'article', 'condition', 'statute', 'ordinance', 'standing',
    'rule and regulation', 'bye-law', 'bye-laws', 'bye law', 'bye laws',
    'by-law', 'plan', 'niyam', 'adhiniyam', 'code', 'notification',
    'instruction', 'manual', 'licence', 'roster', 'process', 'tariff',
    'regulatory', 'schedule', 'function', 'byelaw', 'byelaws', 'direction',
    'guideline', 'criteria', 'law', 'clause', 'board standing order'
]


def extract_section_info_from_query(query):
    """Extract section type, number, and act name from natural language query"""
 
    # Convert to lowercase for processing
    query_lower = query.lower().strip()
 
    # Create regex pattern for all section types (longest first to avoid partial matches)
    section_pattern = '|'.join(re.escape(st) for st in sorted(section_types, key=len, reverse=True))
 
    # Generalized pattern: finds any section_type followed by number/identifier and act name
    # This covers: "Section X of Act", "explain rule Y of Act", "Act name section Z", etc.
 
    # Pattern: ({section_type}) + (number/identifier) + (act/law reference)
    # More flexible number matching: digits, roman numerals, or alphanumeric combinations
    number_pattern = r'(\d+[a-z]*|\b[ivx]+\b|[a-z]\d*|\d+[a-z]\d*)'
 
    # Find all combinations of section_type + number in the query
    section_matches = re.findall(rf'({section_pattern})\s+{number_pattern}', query_lower)
 
    if section_matches:
        # Take the first match found
        section_type, section_number = section_matches[0]
 
        # Now find act/law name - look for common legal document indicators
        act_indicators = r'(?:act|rule|law|code|regulation|ordinance|statute|scheme|policy|manual|notification)'
 
        # Try to extract act name using various patterns
        act_patterns = [
            # "of the Act Name" or "of Act Name"
            rf'of\s+(?:the\s+)?(.+?{act_indicators}[^,]*)',
            # "Act Name section/rule"
            rf'(.+?{act_indicators}[^,]*?).*?{section_type}',
            # Just find any act-like name in the query
            rf'([\w\s]+{act_indicators}[\w\s]*)'
        ]
 
        act_name = None
        for pattern in act_patterns:
            match = re.search(pattern, query_lower)
            if match:
                act_name = match.group(1).strip()
                # Clean up common prefixes/suffixes
                act_name = re.sub(r'^\s*(of\s+(?:the\s+)?|the\s+)', '', act_name)
                act_name = re.sub(r'\s*,\s*\d{4}.*', '', act_name)
                break
 
        return {
            'section_type': section_type,
            'section_number': section_number,
            'act_name': act_name or ''
        }
 
    return None
 
def create_enhanced_search_queries(original_query, parsed_info):
    """Create multiple search query variations - simplified and generalized"""
 
    queries = [original_query]  # Always include original query
 
    if parsed_info:
        section_type = parsed_info['section_type']
        section_number = parsed_info['section_number']
        act_name = parsed_info['act_name']
 
        # Generate common variations systematically
        base_variations = [
            f"{section_type} {section_number} of {act_name}",
            f"{section_type} {section_number} of the {act_name}",
            f"{section_type.title()} {section_number} of {act_name.title()}",
            f"{section_type.title()} {section_number} of the {act_name.title()}"
        ]
 
        queries.extend(base_variations)
 
        # Handle rule/rules variation specifically since it's common
        if section_type == 'rule':
            queries.append(f"rules {section_number} of {act_name}")
        elif section_type == 'rules':
            queries.append(f"rule {section_number} of {act_name}")
 
    # Remove duplicates while preserving order
    seen = set()
    unique_queries = []
    for q in queries:
        q_clean = re.sub(r'\s+', ' ', q.strip().lower())
        if q_clean not in seen:
            seen.add(q_clean)
            unique_queries.append(q)
 
    return unique_queries
 
def improved_legislation_search(es, index_name, user_query):
    """Improved search function with better query parsing and multiple search strategies"""
 
    # print(f"Original Query: {user_query}")
 
    # Step 1: Parse the query to extract section info
    parsed_info = extract_section_info_from_query(user_query)
    # print(f"Parsed Info: {parsed_info}")
 
    # Step 2: Create multiple query variations
    search_queries = create_enhanced_search_queries(user_query, parsed_info)
    # print(f"Search Queries: {search_queries[:3]}...")  # Show first 3
 
    # Step 3: Search with multiple queries and combine results
    all_hits = []
    sources_counter = Counter()
 
    for query_text in search_queries:
        query = {
            "size": 20,
            "query": {
                "bool": {
                    "should": [
                        {
                            "match": {
                                "page_content": {
                                    "query": query_text,
                                    "boost": 2.0 if query_text == user_query else 1.0
                                }
                            }
                        },
                        {
                            "match_phrase": {
                                "page_content": {
                                    "query": query_text,
                                    "boost": 1.5
                                }
                            }
                        }
                    ]
                }
            }
        }
 
        try:
            response = es.search(index=index_name, body=query)
            current_hits = response["hits"]["hits"]
 
            # Collect sources for frequency analysis
            for hit in current_hits:
                sources_counter[hit["_source"]["source"]] += hit["_score"]
 
            all_hits.extend(current_hits)
 
            # If we get good results early, break
            if len(current_hits) > 5 and any(hit["_score"] > 5.0 for hit in current_hits):
                break
 
        except Exception as e:
            logger.error("Search failed for query '%s': %s", query_text, e)
            continue
 
    # Step 4: Find the most relevant source
    if not sources_counter:
        logger.warning("No results found")
        return []
 
    most_common_source = sources_counter.most_common(1)[0][0]
    logger.info("Most Common Source: %s", most_common_source)
 
    # Step 5: Enhanced search within the most common source
    best_query = search_queries[0]  # Use the best query variation
 
    # If we have parsed info, create a more targeted query
    if parsed_info:
        must_clauses = []
        should_clauses = []
 
        # Must match the source
        must_clauses.append({
            "term": {
                "source.keyword": most_common_source
            }
        })
 
        # Should match various forms of the section
        for query_text in search_queries[:5]:  # Use top 5 variations
            should_clauses.extend([
                {
                    "match_phrase": {
                        "page_content": {
                            "query": query_text,
                            "boost": 3.0
                        }
                    }
                },
                {
                    "match": {
                        "page_content": {
                            "query": query_text,
                            "boost": 1.5
                        }
                    }
                }
            ])
 
        # Add specific section number matching with case variations
        section_type = parsed_info['section_type']
        section_number = parsed_info['section_number']
 
        # Create case variations for better matching
        section_variations = [
            f"{section_type} {section_number}",
            f"{section_type.title()} {section_number}",
            f"{section_type.upper()} {section_number}"
        ]
 
        for variation in section_variations:
            should_clauses.append({
                "match_phrase": {
                    "page_content": {
                        "query": variation,
                        "boost": 4.0
                    }
                }
            })
 
        query_by_source = {
            "query": {
                "bool": {
                    "must": must_clauses,
                    "should": should_clauses,
                    "minimum_should_match": 1
                }
            },
            "size": 5,
            "sort": [
                {"_score": {"order": "desc"}}
            ]
        }
    else:
        # Fallback to original approach
        query_by_source = {
            "query": {
                "bool": {
                    "must": [
                        {
                            "match": {
                                "page_content": best_query
                            }
                        }
                    ],
                    "filter": [
                        {
                            "term": {
                                "source.keyword": most_common_source
                            }
                        }
                    ]
                }
            },
            "size": 5
        }
 
    source_response = es.search(index=index_name, body=query_by_source)
 
    return source_response["hits"]["hits"]
 
# Your existing workflow with improvements
class LegislationRetriever:
    def __init__(self):
        pass
    def retrieve_documents(self, query):
        try:
            es = get_es_client()
            index_name = "legislation"
            """Complete search workflow with the enhanced approach"""
        
        
            # Get search results
            hits = improved_legislation_search(es, index_name, query)
        
            if not hits:
                logger.warning("No results found")
                return []
        
            # Step 4: Convert to LangChain Document objects (your existing code)
            langchain_docs = []
            for hit in hits:
                content = hit["_source"]["page_content"]
                metadata = {"source": hit["_source"]["source"]}
                doc = Document(page_content=content, metadata=metadata)
                langchain_docs.append(doc)
            logger.debug("Retrieved docs: %s", langchain_docs)
            return langchain_docs
        except Exception as e:
            logger.error("Error during retrieval: %s", e)
            return []
    
 