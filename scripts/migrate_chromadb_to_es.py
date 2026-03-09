"""Migrate Constitution and Legal Maxim data from ChromaDB to Elasticsearch.

Creates two new ES indices:
- constitution: 501 articles from the Constitution of India
- legal_maxims: 138 legal maxims with Latin terms and meanings

Usage:
    python scripts/migrate_chromadb_to_es.py
"""

import sys
import os
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import chromadb
from elasticsearch import Elasticsearch, helpers

# --- Config ---
ES_URL = os.getenv("ELASTICSEARCH_URL", "http://139.84.219.174:9200")
CHROMA_CONSTITUTION_DIR = str(PROJECT_ROOT / "Routing db" / "constitution db")
CHROMA_MAXIM_DIR = str(PROJECT_ROOT / "Routing db" / "legal maximdb")

# --- Index Mappings ---

CONSTITUTION_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "legal_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "stop"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "page_content": {"type": "text", "analyzer": "legal_analyzer"},
            "source": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "row": {"type": "integer"},
            "part_number": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "part_name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "article_number": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "article_name": {"type": "text"},
        }
    },
}

MAXIM_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "legal_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "stop"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "page_content": {"type": "text", "analyzer": "legal_analyzer"},
            "source": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "row": {"type": "integer"},
            "maxim_name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
        }
    },
}


def parse_constitution_fields(content: str) -> dict:
    """Extract structured fields from constitution document content."""
    fields = {}
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("part number:"):
            fields["part_number"] = line.split(":", 1)[1].strip()
        elif line.startswith("part name:"):
            fields["part_name"] = line.split(":", 1)[1].strip()
        elif line.startswith("article number:"):
            fields["article_number"] = line.split(":", 1)[1].strip()
        elif line.startswith("article name:"):
            fields["article_name"] = line.split(":", 1)[1].strip()
    return fields


def parse_maxim_fields(content: str) -> dict:
    """Extract the maxim name from content."""
    fields = {}
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("Legal Maxims:"):
            fields["maxim_name"] = line.split(":", 1)[1].strip()
            break
    return fields


def extract_chromadb_docs(persist_dir: str) -> list[dict]:
    """Extract all documents from a ChromaDB collection."""
    client = chromadb.PersistentClient(path=persist_dir)
    collections = client.list_collections()
    if not collections:
        print(f"  No collections found in {persist_dir}")
        return []

    col = collections[0]
    print(f"  Collection: {col.name}, Count: {col.count()}")

    result = col.get(include=["documents", "metadatas"])
    docs = []
    for doc, meta in zip(result["documents"], result["metadatas"]):
        docs.append({"page_content": doc, **meta})

    return docs


def create_index_if_needed(es: Elasticsearch, index_name: str, mapping: dict) -> None:
    """Create an ES index with the given mapping, deleting existing if present."""
    if es.indices.exists(index=index_name):
        resp = input(f"  Index '{index_name}' already exists. Delete and recreate? [y/N] ")
        if resp.lower() != "y":
            print(f"  Skipping {index_name}")
            return
        es.indices.delete(index=index_name)
        print(f"  Deleted existing index '{index_name}'")

    es.indices.create(index=index_name, body=mapping)
    print(f"  Created index '{index_name}'")


def bulk_index(es: Elasticsearch, index_name: str, docs: list[dict]) -> None:
    """Bulk index documents into ES."""
    actions = [
        {"_index": index_name, "_source": doc}
        for doc in docs
    ]
    success, errors = helpers.bulk(es, actions, raise_on_error=False)
    print(f"  Indexed {success} documents, {len(errors)} errors")
    if errors:
        for err in errors[:3]:
            print(f"    Error: {err}")


def migrate_constitution(es: Elasticsearch) -> None:
    """Migrate Constitution ChromaDB to ES."""
    print("\n=== Migrating Constitution ===")
    raw_docs = extract_chromadb_docs(CHROMA_CONSTITUTION_DIR)
    if not raw_docs:
        return

    # Parse structured fields from content
    es_docs = []
    for doc in raw_docs:
        parsed = parse_constitution_fields(doc["page_content"])
        es_docs.append({
            "page_content": doc["page_content"],
            "source": doc.get("source", ""),
            "row": doc.get("row", 0),
            **parsed,
        })

    print(f"  Parsed {len(es_docs)} documents")
    print(f"  Sample fields: {list(es_docs[0].keys())}")

    create_index_if_needed(es, "constitution", CONSTITUTION_MAPPING)
    bulk_index(es, "constitution", es_docs)


def migrate_maxims(es: Elasticsearch) -> None:
    """Migrate Legal Maxim ChromaDB to ES."""
    print("\n=== Migrating Legal Maxims ===")
    raw_docs = extract_chromadb_docs(CHROMA_MAXIM_DIR)
    if not raw_docs:
        return

    # Parse structured fields from content
    es_docs = []
    for doc in raw_docs:
        parsed = parse_maxim_fields(doc["page_content"])
        es_docs.append({
            "page_content": doc["page_content"],
            "source": doc.get("source", ""),
            "row": doc.get("row", 0),
            **parsed,
        })

    print(f"  Parsed {len(es_docs)} documents")
    print(f"  Sample fields: {list(es_docs[0].keys())}")

    create_index_if_needed(es, "legal_maxims", MAXIM_MAPPING)
    bulk_index(es, "legal_maxims", es_docs)


def verify(es: Elasticsearch) -> None:
    """Verify migration by counting docs and sampling."""
    print("\n=== Verification ===")
    for index in ["constitution", "legal_maxims"]:
        if not es.indices.exists(index=index):
            print(f"  {index}: NOT FOUND")
            continue
        count = es.count(index=index)["count"]
        print(f"  {index}: {count} documents")

        # Sample search
        sample = es.search(index=index, body={"query": {"match_all": {}}, "size": 1})
        if sample["hits"]["hits"]:
            src = sample["hits"]["hits"][0]["_source"]
            print(f"    Sample: {list(src.keys())}")
            print(f"    Content preview: {src['page_content'][:120]}...")


def main():
    print(f"ES URL: {ES_URL}")
    es = Elasticsearch(ES_URL, request_timeout=30, max_retries=3, retry_on_timeout=True)

    if not es.ping():
        print("ERROR: Cannot connect to Elasticsearch!")
        sys.exit(1)
    print("Connected to Elasticsearch")

    migrate_constitution(es)
    migrate_maxims(es)
    verify(es)

    print("\nDone!")


if __name__ == "__main__":
    main()
