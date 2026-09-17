"""Probe ES/OpenSearch to see whether IPC 299/300 and BNS 100/101 are
actually indexed and what a targeted term filter returns for each.
"""

import json
from core.clients import get_es_client
from tools.shared.elasticsearch_tools import ES_INDICES, ACTS_PATHS

INDEX = ES_INDICES["newacts"]
es = get_es_client()

QUERIES = [
    ("IPC",  "The Indian Penal Code, 1860",                    ["299", "300"]),
    ("BNS",  "The Bharatiya Nyaya Sanhita, 2023",              ["100", "101"]),
    ("BNSS", "The Bharatiya Nagarik Suraksha Sanhita, 2023",   ["299", "300"]),
]

print(f"index = {INDEX}\n")

for label, act, secs in QUERIES:
    src_path = ACTS_PATHS.get(act, "<unknown>")
    for sec in secs:
        body = {
            "size": 3,
            "query": {
                "bool": {
                    "must": [
                        {"term": {"section_number.keyword": sec}},
                        {"term": {"source.keyword": src_path}},
                    ]
                }
            },
            "_source": ["section_number", "source", "page_content"],
        }
        res = es.search(index=INDEX, body=body)
        hits = res["hits"]["hits"]
        print(f"--- {label} §{sec}  (act={act})")
        print(f"    source_path filter = {src_path}")
        print(f"    total_hits = {res['hits']['total']}")
        for h in hits:
            src = h["_source"]
            snippet = (src.get("page_content") or "")[:180].replace("\n", " ")
            print(f"    -> section={src.get('section_number')} | source={src.get('source')}")
            print(f"       content: {snippet}...")
        if not hits:
            # Try without source filter to see if the section exists under a
            # different source label
            body2 = {
                "size": 5,
                "query": {"term": {"section_number.keyword": sec}},
                "_source": ["section_number", "source"],
            }
            res2 = es.search(index=INDEX, body=body2)
            print(f"    (no source-filter match; wildcard section={sec} hits across all sources:)")
            for h in res2["hits"]["hits"]:
                print(f"       - {h['_source'].get('source')}  §{h['_source'].get('section_number')}")
        print()
