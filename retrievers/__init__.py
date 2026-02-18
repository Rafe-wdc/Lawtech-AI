#from langchain_community.embeddings import HuggingFaceBgeEmbeddings
from langchain_community.embeddings import HuggingFaceEmbeddings

from langchain_community.vectorstores import Chroma
from typing import Dict, List, Optional
import chromadb
#from langchain_community.embeddings import HuggingFaceEmbeddings
import os
from elasticsearch import Elasticsearch
import time
from pprint import pprint

es_url = os.getenv("ELASTICSEARCH_URL", "http://139.84.219.174:9200")

def get_es_client(max_retries: int = 1, sleep_time: int = 5) -> Elasticsearch:
    i = 0
    while i < max_retries:
        try:
            es = Elasticsearch(es_url, request_timeout=60)
            pprint("Connected to Elasticsearch!")
            return es
        except Exception:
            pprint("Could not connect to Elasticsearch, retrying...")
            time.sleep(sleep_time)
            i += 1
    raise ConnectionError("Failed to connect to Elasticsearch after multiple attempts.")
# Use local model path instead of remote hub URL
instructor_embeddings = HuggingFaceEmbeddings(
    model_name="./models/bge-large-en-v1.5",
    model_kwargs={"device": "cpu"},
    encode_kwargs={'normalize_embeddings': True}
)



# Clear Chroma system cache
chromadb.api.client.SharedSystemClient.clear_system_cache()
def initialize_vectordbs(persist_directory_mapping: Dict[str, str]) -> Dict[str, Chroma]:
    """
    Initialize Chroma vectorstores for each directory.

    Args:
        persist_directory_mapping (dict): Mapping of names to persist directories.

    Returns:
        dict: Mapping of names to Chroma vectorstores.
    """
    vectorstores = {}
    print("Vectorstores initializing...")
    for name, persist_dir in persist_directory_mapping.items():
        vectorstores[name] = Chroma(
            persist_directory=persist_dir,
            embedding_function=instructor_embeddings)

    return vectorstores


# Set up base directory and state paths
states_path = {
    # States and Union Territories
    'AP': "./Routing db/AP", 'AR': "./Routing db/AR",
    'AS': "./Routing db/AS", 'BH': "./Routing db/BH",
    'CH': "./Routing db/CH", 'GA': "./Routing db/GOA",
    'GJ': "./Routing db/GJ", 'HR': "./Routing db/HR",
    'HP': "./Routing db/HP", 'JH': "./Routing db/JH",
    'KA': "./Routing db/KA", 'KL': "./Routing db/KL",
    'MP': "./Routing db/MP", 'MH': "./Routing db/MH",
    'MN': "./Routing db/MN", 'MG': "./Routing db/MG",
    'MZ': "./Routing db/MZ", 'NL': "./Routing db/NL",
    'OD': "./Routing db/OD", 'PJ': "./Routing db/PJ",
    'RJ': "./Routing db/RJ", 'SK': "./Routing db/SK",
    'TN': "./Routing db/TN", 'TS': "./Routing db/TS",
    'TR': "./Routing db/TR", 'UP': "./Routing db/UP",
    'UK': "./Routing db/UK", 'WB': "./Routing db/WB",
    # 'AN': "./Routing db/AN", 'CG': "./Routing db/CG",
    'DL': "./Routing db/DL",#'DH': "./Routing db/DH",
    'JK': "./Routing db/JK", 'LA': "./Routing db/LA",
    'LD': "./Routing db/LD", 'PY': "./Routing db/PY",
    'DN': "./Routing db/DN"
}
persist_directory = {
    # 'Drafting': "./Routing db/vectordb_formats_drafts",
    'Constitution': "../Routing db/constitution db",
    # 'Newacts': "../vectordb_newacts",
    # 'Judgment': "../vectordb_sc_judgement_V2",
    'Maxim': "../Routing db/legal maximdb",
    # **states_path,
    # 'Central': "./Routing db/central-db"
}
vectordbs = initialize_vectordbs(persist_directory)
print("Vector databases initialized")
