r"""Create and fill the `tribunal_judgements` OpenSearch index.

Steps (run in order; each is safe to re-run):
    create    create index tribunal_judgements_v1 + alias tribunal_judgements
    migrate   copy every tribunal document from judgements_v2
              (court_name = "tribunal") and tag it with tribunal_code /
              tribunal_name from its source path. Server-side _reindex;
              --client-side copies through this machine instead.
    tdsat     upload the documents built by build_tdsat_docs.py
    verify    counts per tribunal + a sample search

The source documents in judgements_v2 are only read, never changed. Document
ids are kept (migration) or fixed (TDSAT), so a re-run overwrites instead of
duplicating.

Needs a write-capable user; the app's read-only user cannot do this:
    $env:ES_ADMIN_USER = "..."; $env:ES_ADMIN_PASSWORD = "..."
    python scripts/tribunal/tribunal_index.py create
    python scripts/tribunal/tribunal_index.py migrate
    python scripts/tribunal/tribunal_index.py tdsat --file D:\scraping_websites\data\tdsat\tdsat_docs.jsonl
    python scripts/tribunal/tribunal_index.py verify
The endpoint comes from --url, else ES_URL in the repo .env (--env).
`verify` also works with the read-only user (ES_USER / ES_PASSWORD).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from config.tribunals import FOLDER_TO_TRIBUNAL, UNKNOWN, tribunal_for_source  # noqa: E402

INDEX = "tribunal_judgements_v1"
ALIAS = "tribunal_judgements"
SOURCE_INDEX = "judgements_v2"
SOURCE_QUERY = {"term": {"court_name": "tribunal"}}
TIMEOUT = 300


def _text_kw(**extra) -> dict:
    return {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 512}}, **extra}


# Field types follow judgements_v2 so migrated documents index unchanged.
MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 1},
    "mappings": {
        "dynamic": True,
        "properties": {
            "tribunal_code": {"type": "keyword"},
            "tribunal_name": _text_kw(),
            "court_name": {"type": "keyword"},
            "court_code": {"type": "keyword"},
            "case_no": {"type": "keyword"},
            "case_type": {"type": "keyword"},
            "petition_type": {"type": "keyword"},
            "title": _text_kw(),
            "parties": _text_kw(),
            "petitioner_names": _text_kw(),
            "respondent_names": _text_kw(),
            "advocates_petitioner": _text_kw(),
            "advocates_respondent": _text_kw(),
            "bench": {"type": "keyword", "ignore_above": 1024},
            "bench_judge_names": _text_kw(),
            "judgment_by": _text_kw(),
            "judgment_date": {"type": "date", "ignore_malformed": True},
            "year": {"type": "integer", "ignore_malformed": True},
            "citation": _text_kw(),
            "keywords": _text_kw(),
            "acts_or_sections_invoked": _text_kw(),
            "full_text": {"type": "text"},
            "description": {"type": "text"},
            "char_count": {"type": "integer"},
            "page_count": {"type": "integer"},
            "extraction_method": {"type": "keyword"},
            "text_status": {"type": "keyword"},
            "pdf_links": {"properties": {"label": {"type": "keyword"}, "url": {"type": "keyword"}}},
            "source_pdf_link": {"type": "keyword"},
            "s3_bucket": {"type": "keyword"},
            "s3_key": {"type": "keyword"},
            "source": {"type": "keyword"},
            "ingest_source": {"type": "keyword"},
            "indexed_at": {"type": "date"},
        },
    },
}

# Same rule as config.tribunals.tribunal_for_source, run inside OpenSearch.
REINDEX_SCRIPT = r"""
String s = ctx._source.source == null ? '' : ctx._source.source.replace('/', '\\');
String folder = '';
int a = s.indexOf('\\');
if (a >= 0) {
  int b = s.indexOf('\\', a + 1);
  if (b > a) { folder = s.substring(a + 1, b).trim().toLowerCase(); }
}
def t = params.folders.get(folder);
if (t == null) { t = params.unknown; }
ctx._source.tribunal_code = t[0];
ctx._source.tribunal_name = t[1];
ctx._source.court_name = 'tribunal';
"""


class Client:
    def __init__(self, url: str, user: str, pwd: str):
        self.base, self.auth = url.rstrip("/"), (user, pwd)

    def req(self, method: str, path: str, ok=(200, 201), **kw):
        r = requests.request(method, f"{self.base}/{path.lstrip('/')}", auth=self.auth,
                             timeout=kw.pop("timeout", TIMEOUT), **kw)
        if r.status_code in (401, 403):
            sys.exit(f"ERROR: HTTP {r.status_code} on {method} {path}: this user lacks "
                     f"permission. Use the write-capable (master) user.\n{r.text[:300]}")
        if ok and r.status_code not in ok:
            sys.exit(f"ERROR: HTTP {r.status_code} on {method} {path}\n{r.text[:500]}")
        return r


def cmd_create(c: Client, args) -> None:
    if c.req("HEAD", INDEX, ok=None).status_code == 200:
        print(f"index {INDEX} already exists, leaving it as is")
    else:
        c.req("PUT", INDEX, json=MAPPING)
        print(f"created index {INDEX}")
    c.req("POST", "_aliases", json={"actions": [{"add": {"index": INDEX, "alias": ALIAS}}]})
    print(f"alias {ALIAS} -> {INDEX}")


def _source_count(c: Client) -> int:
    return c.req("POST", f"{SOURCE_INDEX}/_count", json={"query": SOURCE_QUERY}).json()["count"]


def cmd_migrate(c: Client, args) -> None:
    expected = _source_count(c)
    print(f"{SOURCE_INDEX}: {expected} tribunal documents to copy")
    if args.server_side:
        _migrate_reindex(c, expected)
    else:
        _migrate_client_side(c, expected, args.batch, args.dry_run)
    if args.dry_run:
        return
    c.req("POST", f"{INDEX}/_refresh")
    got = c.req("POST", f"{INDEX}/_count", json={"query": {"bool": {"must_not": [
        {"term": {"ingest_source": "tdsat_scrape"}}]}}}).json()["count"]
    print(f"migrated documents now in {INDEX}: {got} (expected {expected})")
    if got != expected:
        print("MISMATCH - re-run migrate (safe, ids are kept)")


def _migrate_reindex(c: Client, expected: int) -> None:
    params = {"folders": {f: [t.code, t.name] for f, t in FOLDER_TO_TRIBUNAL.items()},
              "unknown": [UNKNOWN.code, UNKNOWN.name]}
    body = {
        "conflicts": "proceed",
        "source": {"index": SOURCE_INDEX, "query": SOURCE_QUERY, "size": 500},
        "dest": {"index": INDEX, "op_type": "index"},
        "script": {"lang": "painless", "source": REINDEX_SCRIPT, "params": params},
    }
    task = c.req("POST", "_reindex?wait_for_completion=false&slices=auto", json=body).json()["task"]
    print(f"reindex task {task} started")
    while True:
        t = c.req("GET", f"_tasks/{task}").json()
        st = t.get("task", {}).get("status", {})
        # With slices the parent's own counters stay 0; the slices carry them.
        parts = [s for s in (st.get("slices") or []) if s] or [st]
        done = sum(p.get("created", 0) + p.get("updated", 0) for p in parts)
        print(f"  {done}/{expected} copied, "
              f"failures so far: {len(t.get('response', {}).get('failures', []))}")
        if t.get("completed"):
            resp = t.get("response", {})
            fails = resp.get("failures", [])
            if t.get("error") or fails:
                print("reindex finished with errors:", json.dumps(t.get("error") or fails[:5])[:1500])
            break
        time.sleep(15)


def tag_document(doc: dict) -> dict:
    """A judgements_v2 tribunal document as stored in the tribunal index."""
    t = tribunal_for_source(doc.get("source", ""))
    return {**doc, "tribunal_code": t.code, "tribunal_name": t.name, "court_name": "tribunal"}


def _migrate_client_side(c: Client, expected: int, batch: int, dry_run: bool) -> None:
    """Page through the source with search_after and bulk-index each page.
    With dry_run nothing is written: only the per-tribunal tally is printed."""
    after, copied, failed, tally = None, 0, [], {}
    while True:
        body = {"size": batch, "query": SOURCE_QUERY, "sort": [{"_id": "asc"}]}
        if after:
            body["search_after"] = after
        hits = c.req("POST", f"{SOURCE_INDEX}/_search", json=body).json()["hits"]["hits"]
        if not hits:
            break
        lines = []
        for h in hits:
            doc = tag_document(h["_source"])
            tally[doc["tribunal_code"]] = tally.get(doc["tribunal_code"], 0) + 1
            lines.append(json.dumps({"index": {"_index": INDEX, "_id": h["_id"]}}))
            lines.append(json.dumps(doc, ensure_ascii=False))
        if not dry_run:
            failed += _bulk(c, lines)
        copied += len(hits)
        after = hits[-1]["sort"]
        if copied % (batch * 20) < batch or copied == expected:
            print(f"  {copied}/{expected} {'read' if dry_run else 'copied'} (failed {len(failed)})")
    print(("dry run, nothing written. " if dry_run else "") + "per tribunal:")
    for code, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>7}  {code}")
    if failed:
        print(f"{len(failed)} documents failed; re-run migrate to retry")


def _bulk(c: Client, lines: list[str]) -> list:
    r = c.req("POST", "_bulk", headers={"Content-Type": "application/x-ndjson"},
              data=("\n".join(lines) + "\n").encode("utf-8")).json()
    fails = [(i["index"].get("_id"), i["index"].get("error", {}).get("reason"))
             for i in r.get("items", []) if i["index"].get("status", 500) >= 300]
    for _id, reason in fails[:5]:
        print(f"  FAILED {_id}: {reason}")
    return fails


def cmd_tdsat(c: Client, args) -> None:
    docs = [json.loads(line) for line in open(args.file, encoding="utf-8") if line.strip()]
    print(f"{args.file}: {len(docs)} documents")
    failed = []
    for start in range(0, len(docs), args.batch):
        lines = []
        for d in docs[start:start + args.batch]:
            d = dict(d)
            _id = d.pop("_id")
            lines.append(json.dumps({"index": {"_index": INDEX, "_id": _id}}))
            lines.append(json.dumps(d, ensure_ascii=False))
        failed += _bulk(c, lines)
        print(f"  {min(start + args.batch, len(docs))}/{len(docs)} sent (failed {len(failed)})")
    c.req("POST", f"{INDEX}/_refresh")
    got = c.req("POST", f"{INDEX}/_count", json={"query": {"term": {"tribunal_code": "TDSAT"}}}).json()["count"]
    print(f"TDSAT documents now in {INDEX}: {got} (expected {len(docs)}), failed {len(failed)}")


def cmd_verify(c: Client, args) -> None:
    total = c.req("POST", f"{ALIAS}/_count").json()["count"]
    src = _source_count(c)
    r = c.req("POST", f"{ALIAS}/_search", json={"size": 0, "aggs": {
        "t": {"terms": {"field": "tribunal_code", "size": 50}},
        "no_text": {"filter": {"bool": {"must_not": [{"exists": {"field": "full_text"}}]}}},
    }}).json()
    print(f"{ALIAS}: {total} documents ({SOURCE_INDEX} has {src} tribunal documents)")
    for b in r["aggregations"]["t"]["buckets"]:
        print(f"  {b['doc_count']:>7}  {b['key']}")
    print(f"  without full_text: {r['aggregations']['no_text']['doc_count']}")
    q = args.query
    hits = c.req("POST", f"{ALIAS}/_search", json={
        "size": 5, "_source": ["tribunal_code", "title", "judgment_date", "pdf_links"],
        "query": {"multi_match": {"query": q, "fields": ["full_text", "title^2", "parties"]}},
    }).json()["hits"]["hits"]
    print(f'sample search "{q}":')
    for h in hits:
        s = h["_source"]
        url = (s.get("pdf_links") or [{}])[0].get("url", "")
        print(f"  [{s.get('tribunal_code')}] {str(s.get('title'))[:80]} ({s.get('judgment_date')}) {url[:90]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["create", "migrate", "tdsat", "verify"])
    ap.add_argument("--url", help="OpenSearch endpoint (default: ES_URL from --env)")
    ap.add_argument("--env", default=str(REPO / ".env"))
    ap.add_argument("--server-side", action="store_true",
                    help="migrate with OpenSearch _reindex + painless instead of copying through this machine")
    ap.add_argument("--dry-run", action="store_true",
                    help="migrate: read and tally only, write nothing (works with the read-only user)")
    ap.add_argument("--file", help="tdsat_docs.jsonl (step tdsat)")
    ap.add_argument("--batch", type=int, default=100, help="documents per request")
    ap.add_argument("--query", default="carriage fee broadcaster distribution platform")
    args = ap.parse_args()

    from dotenv import dotenv_values
    env = {**dotenv_values(args.env), **os.environ}
    url = args.url or env.get("ES_URL")
    if not url:
        sys.exit("ERROR: pass --url or set ES_URL in the .env")
    user, pwd = env.get("ES_ADMIN_USER"), env.get("ES_ADMIN_PASSWORD")
    if (args.step == "verify" or args.dry_run) and not (user and pwd):
        user, pwd = env.get("ES_USER"), env.get("ES_PASSWORD")
    if not (user and pwd):
        sys.exit("ERROR: set ES_ADMIN_USER and ES_ADMIN_PASSWORD (write-capable user) first.")
    if args.step == "tdsat" and not args.file:
        sys.exit("ERROR: --file is required for the tdsat step")

    c = Client(url, user, pwd)
    {"create": cmd_create, "migrate": cmd_migrate, "tdsat": cmd_tdsat, "verify": cmd_verify}[args.step](c, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
