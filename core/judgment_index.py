"""The High Court judgment index the agents read, and how to read an entry.

Since 2026-11-13 the agents read `judgements_v2`. It holds:
- everything from `judgements`, de-duplicated (ingest_source "judgements_v1");
- the 2025-26 Bombay High Court loads ("mirror_tar", "open_data",
  "portal_scrape"): 321,694 entries for 2025 and 116,442 for 2026, against
  25,115 and 2,640 in `judgements`;
- the same judgments with correct dates. `judgements` stores the day of the
  month as the year for about 5% of Bombay entries ("26 November 2010" as
  2026-11-10), which is why every "2026" entry there was an old judgment.
- Odisha spelled correctly ("odisha high court"; `judgements` has "odisa").

The two indexes name fields differently. In v2, the text is `full_text`
(was `page_content`), the case number is `case_no` (was `case_number`),
party names of the new Bombay entries are one string (not a list), and
every entry carries `pdf_links` / `s3_bucket` / `s3_key`. The helpers below
read either layout, so `JUDGMENTS_INDEX=judgements` switches back with no
code change.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from core.settings import ES_INDICES, S3_BUCKET

INDEX = ES_INDICES["judgments"]
LEGACY = INDEX == "judgements"

# The field that holds the judgment's text, for queries and highlights.
TEXT_FIELD = "page_content" if LEGACY else "full_text"
CASE_NO_FIELD = "case_number" if LEGACY else "case_no"
ODISHA_COURT = "odisa high court" if LEGACY else "odisha high court"

# Fields an agent needs to show an entry and link its PDF (no text).
LINK_FIELDS = ["court_name", "source", "pdf_links", "s3_bucket", "s3_key"]


def text(src: dict) -> str:
    return src.get("full_text") or src.get("page_content") or ""


def case_number(src: dict) -> str:
    return str(src.get("case_no") or src.get("case_number") or "")


def names(value) -> list[str]:
    """Party names as a list: v2's new entries store one string."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if v]


def judges(src: dict) -> list[str]:
    return names(src.get("bench_judge_names") or src.get("judge_names") or src.get("judgment_by"))


_PUBLIC_BUCKETS: dict[str, bool] = {}


def _bucket_is_public(host: str, sample_url: str) -> bool:
    """One anonymous request per bucket, remembered for the process: can a
    reader open its files? The `lawttorney` bucket is public; `bhc-pdfs`
    (new Bombay PDFs) was private on 2026-11-13, so links into it would show
    "Access Denied". (Checking per file cost 81 requests, 13 s, for one list.)"""
    if host.split(".", 1)[0] == S3_BUCKET:
        return True
    if host not in _PUBLIC_BUCKETS:
        try:
            import urllib.request
            req = urllib.request.Request(sample_url, headers={"Range": "bytes=0-0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                _PUBLIC_BUCKETS[host] = resp.status in (200, 206)
        except Exception:
            _PUBLIC_BUCKETS[host] = False
    return _PUBLIC_BUCKETS[host]


def pdf_url(src: dict, title: str = "", check_exists: bool = True) -> str | None:
    """A PDF link a reader can open, or None.

    v2 entries carry the link (`pdf_links`). Links into the public bucket
    are confirmed to exist like before (`check_exists=False` skips that for
    bulk reads such as a judge's 20,000 judgments); links into a bucket
    readers cannot open are left out. An old-index entry has no link field,
    so it is built from the file name as before."""
    links = [ln.get("url") for ln in (src.get("pdf_links") or [])
             if isinstance(ln, dict) and ln.get("url")]
    if links:
        url = links[0]
        if not _bucket_is_public(urlparse(url).netloc, url):
            return None
        bucket, key = src.get("s3_bucket"), src.get("s3_key")
        if check_exists and bucket == S3_BUCKET and key:
            from tools.shared.storage_tools import _s3_key_exists
            if not _s3_key_exists(bucket, key):
                return None
        return url
    court, source = src.get("court_name"), src.get("source")
    if court and source:
        from tools.shared.storage_tools import generate_s3_link
        return generate_s3_link.invoke({"court": str(court), "file_name": str(source),
                                        "title": title})
    return None
