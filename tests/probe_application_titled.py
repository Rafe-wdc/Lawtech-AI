"""Targeted probe: pull every drafting template whose SOURCE NAME contains
the word "Application" (capitalized as a title word, like
"An Application For ...", "Application Under Section ...", etc.).

Goal: give the user a clean list of every Application-titled template the
drafting index actually holds, sorted, plus structural fingerprint, so we
can see at a glance which subtypes are present / absent.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.clients import get_es_client
from core.settings import ES_INDICES


def is_court_filing(body: str) -> bool:
    head = body[:2000].lower()
    return bool(
        re.search(r"in the court of", head)
        or re.search(r"in the matter of", head)
        or re.search(r"before the hon[' ’]?ble", head)
        or re.search(r"\bversus\b|\bvs\.?\b", head)
    )


def is_office_letter(body: str) -> bool:
    head = body[:2000]
    low = head.lower()
    has_to = bool(re.search(r"^\s*to,?\s+the\b", head, re.MULTILINE | re.IGNORECASE)) \
             or bool(re.search(r"^\s*to,?\s*$", head, re.MULTILINE))
    has_subj_or_sir = "subject" in low or "sir / madam" in low or "respected sir" in low
    no_court = "in the court of" not in low and "in the matter of" not in low \
               and "versus" not in low and "before the hon" not in low
    return has_to and has_subj_or_sir and no_court


def main() -> None:
    es = get_es_client()
    index = ES_INDICES["drafting"]

    # Pull a wide net of source paths. We use match_all + paginate by sort,
    # but for 497 docs a single size=500 is fine.
    body = {
        "size": 500,
        "query": {"match_all": {}},
        "_source": ["source", "page_content"],
    }
    resp = es.search(index=index, body=body)
    hits = resp["hits"]["hits"]
    print(f"Total hits scanned: {len(hits)}")

    # Dedupe by source; keep one chunk per source.
    by_src: dict[str, str] = {}
    for h in hits:
        s = h["_source"].get("source", "?")
        if s not in by_src:
            by_src[s] = h["_source"].get("page_content", "")

    # Filter to source names containing the word "Application" (case-sensitive
    # against the filename to preserve "Application" the legal-doc-type word,
    # not "applications" used as a verb in body text).
    matches = []
    for src, txt in by_src.items():
        filename = src.rsplit("/", 1)[-1]
        if re.search(r"\bApplication\b", filename, re.IGNORECASE):
            matches.append((src, txt))

    matches.sort(key=lambda kv: kv[0].lower())

    rows = []
    for src, txt in matches:
        court = is_court_filing(txt)
        letter = is_office_letter(txt)
        shape = "COURT" if court else ("LETTER" if letter else "OTHER")
        rows.append({
            "source": src,
            "shape": shape,
            "filename": src.rsplit("/", 1)[-1],
            "first_200": txt[:200].replace("\n", " ").strip(),
        })

    by_shape = Counter(r["shape"] for r in rows)

    out_path = _ROOT / "_probe_application_titled.json"
    out_path.write_text(json.dumps({
        "total_unique_sources_in_index": len(by_src),
        "matched_application_titled": len(rows),
        "by_shape": dict(by_shape),
        "rows": rows,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Total Application-titled templates: {len(rows)}")
    print(f"  COURT  : {by_shape.get('COURT', 0)}")
    print(f"  LETTER : {by_shape.get('LETTER', 0)}")
    print(f"  OTHER  : {by_shape.get('OTHER', 0)}")
    print()
    print("--- LETTER-shaped (office applications) ---")
    for r in rows:
        if r["shape"] == "LETTER":
            print(f"  {r['filename']}")
    print()
    print("--- OTHER-shaped ---")
    for r in rows:
        if r["shape"] == "OTHER":
            print(f"  {r['filename']}")
    print()
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
