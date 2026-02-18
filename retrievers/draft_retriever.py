from pydantic import BaseModel,Field
from langchain_core.prompts import ChatPromptTemplate

from langchain_openai import ChatOpenAI
from langchain_core.documents import Document

from dotenv import load_dotenv
from . import get_es_client

load_dotenv()

class GetSource(BaseModel):
    source: str = Field(...,description="The source file path")

def select_file_source_draft(query,files_path):
    llm = ChatOpenAI(temperature=0.3, model="gpt-4o-mini").with_structured_output(GetSource)

    prompt_template="""
    You are a legal AI assistant tasked with identifying the most relevant legal document from a list of file paths for answering the user query.
    User query: {query}
    Select the most relevant file path from the list below:
    File paths:
    {files_path}
    Return only the most relevant file path.
    """

    QUERY_PROMPT=ChatPromptTemplate.from_template(prompt_template)
    llm_chain = QUERY_PROMPT | llm
    res = llm_chain.invoke({'query':query,'files_path':files_path})
    return res.source

class DraftRetriever:
    def __init__(self):
        pass
    def retrieve_documents(self, query: str):
        try:
           es = get_es_client()
           es_query = {
                "size": 100,
                "query": {
                    "match": {
                        "page_content": query
                    }
                }
            }
           response = es.search(index="drafting", body=es_query)
           file_paths = [hit["_source"]["source"] for hit in response["hits"]["hits"]]
           print('file_paths: ',file_paths)
           source = select_file_source_draft(query, file_paths).strip()
           print('source: ',source)
           query_by_source = {
                "size": 1,
                "query": {
                    "term": {
                        "source.keyword": source
                    }
                }
            }
           source_response = es.search(index="drafting", body=query_by_source)
           # Step 4: Convert to LangChain Document objects
           langchain_docs = []
           for hit in source_response["hits"]["hits"]:
                content = hit["_source"]["page_content"]
                metadata = {"source": hit["_source"]["source"]}
                doc = Document(page_content=content, metadata=metadata)
                langchain_docs.append(doc)
           return langchain_docs

        except Exception as e:
            print(f"⚠️ DraftRetriever failed: {e}")
            return []
