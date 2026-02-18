from typing import List, Optional
from langchain_core.documents import Document
from langchain_core.output_parsers import BaseOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain.retrievers.multi_query import MultiQueryRetriever
from langchain.retrievers.ensemble import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from . import vectordbs



class LineListOutputParser(BaseOutputParser[List[str]]):
    """
    Output parser for a list of lines.
    """
    def parse(self, text: str) -> List[str]:
        lines = text.strip().split("\n")
        return list(filter(None, lines))  # Remove empty lines

output_parser = LineListOutputParser()
QUERY_PROMPT = PromptTemplate(
    input_variables=["question"],
    template="""You are an AI language model assistant. Your task is to generate five
    different versions of the given user question to retrieve relevant documents from a vector
    database. By generating multiple perspectives on the user question, your goal is to help
    the user overcome some of the limitations of the distance-based similarity search.
    Provide these alternative questions separated by newlines.
    Original question: {question}""",
)
llm = ChatOpenAI(temperature=0)
llm_chain = QUERY_PROMPT | llm | output_parser

class BaseRetriever:
    def __init__(self):
        pass

    def retrieve_documents(self, task: str, query: str) -> List[Document]:

        vectordb = vectordbs[task]

        # Step 2: Try MultiQueryRetriever
        try:
            base_retriever = vectordb.as_retriever(search_kwargs={"k": 10})
            multi_retriever = MultiQueryRetriever(
                retriever=base_retriever,
                llm_chain=llm_chain,
                parser_key="lines"
            )
            print("🧠 MultiQueryRetriever initialized")
            unique_docs = multi_retriever.invoke(query)
            if unique_docs:
                print(f"✅ MultiQueryRetriever returned {len(unique_docs)} docs")
            else:
                raise ValueError("MultiQueryRetriever returned no documents.")
        except Exception as e:
            print(f"⚠️ MultiQueryRetriever failed: {e}")
            print("🔁 Switching to BM25 + Chroma fallback...")
            fallback_retriever = vectordb.as_retriever(search_kwargs={"k": 10})
            unique_docs = fallback_retriever.invoke(query)
            if not unique_docs:
                print("❌ No fallback docs either.")
                return []

        # Step 3: Determine most common source
        source_counts = {}
        for doc in unique_docs:
            source = doc.metadata.get("source")
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1

        if not source_counts:
            print("❌ No valid sources in retrieved docs.")
            return []

        top_source = max(source_counts, key=source_counts.get)
        print(f"🏆 Most frequent source: {top_source}")

        # Step 4: Get all documents from top source
        source_file = vectordb.get(where={"source": top_source})
        source_file_documents = []
        for content, metadata in zip(source_file["documents"], source_file["metadatas"]):
            doc = Document(
                page_content=content,
                metadata={
                    'source': metadata.get('source'),
                    'row': metadata.get('row') if 'row' in metadata else None
                }
            )
            source_file_documents.append(doc)

        # Step 5: Create BM25 + Chroma ensemble retriever
        bm25_doc_retriever = BM25Retriever.from_documents(source_file_documents)
        bm25_doc_retriever.k = 4

        chroma = vectordb.as_retriever(
            search_type="mmr",
            search_kwargs={"filter": {"source": top_source}, "k": 4}
        )

        ensemble = EnsembleRetriever(
            retrievers=[bm25_doc_retriever, chroma],
            weights=[0.5, 0.5]
        )
        print("🚀 EnsembleRetriever (BM25 + Chroma) running...")
        hybrid_search_results = ensemble.invoke(query)
        print(f"✅ Hybrid search returned {len(hybrid_search_results)} relevant documents")

        return hybrid_search_results
