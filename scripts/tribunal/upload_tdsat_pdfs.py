r"""Copy the scraped TDSAT PDFs to the Lawttorney S3 bucket.

Each document in tdsat_docs.jsonl names its S3 key (s3_key, under
tribunal/tdsat/). The app only shows PDF links that point at this bucket, so
this must run before (or together with) the index upload. Files already in
the bucket with the same size are skipped, so a re-run is cheap.

Needs AWS credentials with s3:PutObject on the bucket, via the usual boto3
chain (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars or an AWS profile).
The bucket's existing objects are publicly readable through the bucket
policy, so no per-object ACL is set.

Usage:
    python scripts/tribunal/upload_tdsat_pdfs.py --data D:\scraping_websites\data\tdsat
    python scripts/tribunal/upload_tdsat_pdfs.py --data ... --check   # only report what is missing
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help=r"folder with tdsat_docs.jsonl and pdfs\ ")
    ap.add_argument("--check", action="store_true", help="only check which PDFs are publicly reachable")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    data = Path(args.data)
    docs = [json.loads(line) for line in open(data / "tdsat_docs.jsonl", encoding="utf-8") if line.strip()]
    print(f"{len(docs)} documents")

    if args.check:
        def reachable(d):
            # Retry: a burst of parallel lookups can fail DNS resolution locally.
            for attempt in range(4):
                try:
                    return requests.head(d["pdf_links"][0]["url"], timeout=30).status_code == 200
                except requests.RequestException:
                    time.sleep(2 ** attempt)
            return False
        with ThreadPoolExecutor(args.workers) as pool:
            ok = list(pool.map(reachable, docs))
        missing = [d["s3_key"] for d, good in zip(docs, ok) if not good]
        print(f"publicly reachable: {len(docs) - len(missing)}, missing: {len(missing)}")
        for k in missing[:10]:
            print("  missing", k)
        return 1 if missing else 0

    import boto3
    from botocore.exceptions import ClientError

    s3 = boto3.client("s3", region_name=docs[0]["pdf_links"][0]["url"].split(".s3.")[1].split(".")[0])

    def upload(d) -> str:
        local = data / "pdfs" / Path(d["s3_key"]).name
        try:
            head = s3.head_object(Bucket=d["s3_bucket"], Key=d["s3_key"])
            if head["ContentLength"] == local.stat().st_size:
                return "skipped"
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
                raise
        s3.upload_file(str(local), d["s3_bucket"], d["s3_key"],
                       ExtraArgs={"ContentType": "application/pdf"})
        return "uploaded"

    counts: dict[str, int] = {}
    failed: list[tuple[str, str]] = []
    with ThreadPoolExecutor(args.workers) as pool:
        futures = [(d, pool.submit(upload, d)) for d in docs]
        for i, (d, fut) in enumerate(futures, 1):
            try:
                r = fut.result()
                counts[r] = counts.get(r, 0) + 1
            except Exception as e:
                failed.append((d["s3_key"], str(e)[:200]))
            if i % 100 == 0 or i == len(futures):
                print(f"  {i}/{len(futures)} {counts} failed={len(failed)}")
    for k, e in failed[:10]:
        print(f"  FAILED {k}: {e}")
    print("now run with --check to confirm the links open")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
