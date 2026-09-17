"""Wide probe: pull up to 100 distinct 'application' templates from the
drafting OpenSearch index and classify each by BODY FINGERPRINT (not by
source-path keywords, which was misleading in the earlier probe).

Why a structural classifier?
  Source paths like ".../An Application for Succession Certificate ..." are
  ambiguous — "certificate" can mean an office-letter (income certificate to
  Tahsildar) OR a court application (succession certificate before District
  Judge). Only the BODY tells you which: an office letter starts with
  "To,\n<Officer>\nSubject: ...\nSir / Madam"; a court application starts with
  "IN THE COURT OF ..." or "BEFORE THE HON'BLE ...".

We bucket each template into one of:
  - court_filing            (IN THE COURT OF / IN THE MATTER OF / Versus)
  - tribunal_appellate      (BEFORE THE HON'BLE ... Vs.)
  - police_complaint        (To, The SHO/Inspector + body sections)
  - notice_or_letter        (To, <addressee> + Subject: + Sir / Madam, NO court)
  - agreement_deed          (BETWEEN ... AND ..., no addressee/court)
  - affidavit               (I, <name>, do hereby solemnly affirm)
  - unknown                 (none of the above clearly)

Output -> _probe_100_apps.json
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

TARGET_COUNT = 100


def classify_by_body(body: str) -> tuple[str, dict]:
    """Return (type, signals)."""
    head = body[:2500]
    low = head.lower()
    signals = {
        "court_of": bool(re.search(r"in the court of", low)),
        "before_honble": bool(re.search(r"before the hon[' ’]?ble", low)),
        "matter_of": bool(re.search(r"in the matter of", low)),
        "versus": bool(re.search(r"\bversus\b|\bvs\.?\b", low)),
        "petitioner_plaintiff": bool(re.search(r"petitioner|plaintiff|complainant|appellant|applicant", low)),
        "to_block": bool(re.search(r"^\s*to,?\s*$", head, re.MULTILINE)) or bool(re.search(r"^\s*to,?\s+the\b", head, re.MULTILINE | re.IGNORECASE)),
        "subject_line": bool(re.search(r"\bsubject\s*:\s*", low)),
        "sir_madam": bool(re.search(r"\bsir\b\s*/?\s*\bmadam\b|respected\s+sir", low)),
        "between_and": bool(re.search(r"\bbetween\b[\s\S]{0,200}\band\b", low)),
        "agreement_made": bool(re.search(r"this (agreement|deed|mou|memorandum|will)", low)),
        "solemnly_affirm": bool(re.search(r"solemnly (affirm|swear|declare)|do hereby state on oath", low)),
        "sho_inspector": bool(re.search(r"station house officer|police inspector|s\.?h\.?o\.?|the inspector,? police", low)),
        "prayer": bool(re.search(r"\bprayer\b|prays? (this|that|for)|relief sought", low)),
        "verification": bool(re.search(r"\bverification\b", low)),
        "respectfully_showeth": bool(re.search(r"respectfully\s+showeth|most respectfully showeth", low)),
    }

    # Decide type
    if signals["before_honble"] and signals["versus"]:
        return "tribunal_appellate", signals
    if signals["court_of"] or signals["matter_of"] or signals["versus"]:
        return "court_filing", signals
    if signals["to_block"] and signals["sho_inspector"]:
        return "police_complaint", signals
    if signals["to_block"] and (signals["subject_line"] or signals["sir_madam"]):
        return "notice_or_letter", signals
    if signals["between_and"] or signals["agreement_made"]:
        return "agreement_deed", signals
    if signals["solemnly_affirm"]:
        return "affidavit", signals
    return "unknown", signals


def headers(body: str, limit: int = 10) -> list[str]:
    out = []
    for line in body.splitlines():
        s = line.strip()
        if not s or len(s) > 90:
            continue
        if re.match(r"^(\d+\.|\([a-z0-9ivx]+\)|[A-Z][A-Z\s/]{4,})", s):
            out.append(s)
            if len(out) >= limit:
                break
    return out


def main() -> None:
    es = get_es_client()
    index = ES_INDICES["drafting"]

    # Single broad query — every doc with the word "application" anywhere.
    # size=200 because some sources have multiple chunks; we dedupe after.
    body = {
        "size": 300,
        "query": {"match": {"page_content": "application"}},
        "_source": ["source", "page_content"],
    }
    resp = es.search(index=index, body=body)
    hits = resp["hits"]["hits"]
    print(f"Raw BM25 hits for 'application': {len(hits)}")

    by_source: dict[str, str] = {}  # keep first/best chunk per source
    for h in hits:
        src = h["_source"].get("source", "?")
        if src not in by_source:
            by_source[src] = h["_source"].get("page_content", "")
        if len(by_source) >= TARGET_COUNT:
            break
    print(f"Unique sources: {len(by_source)}")
    print()

    classified: list[dict] = []
    for src, text in by_source.items():
        typ, signals = classify_by_body(text)
        classified.append({
            "source": src,
            "type": typ,
            "length": len(text),
            "signals": signals,
            "header_lines": headers(text),
            "head_preview": text[:400].replace("\n", "\\n"),
        })

    classified.sort(key=lambda r: (r["type"], r["source"]))
    type_counts = Counter(r["type"] for r in classified)

    # Sub-stats per type: how many carry Prayer / Verification / Affidavit
    by_type_signals: dict[str, dict[str, int]] = {}
    for typ in type_counts:
        rows = [r for r in classified if r["type"] == typ]
        by_type_signals[typ] = {
            "count": len(rows),
            "has_prayer": sum(1 for r in rows if r["signals"]["prayer"]),
            "has_verification": sum(1 for r in rows if r["signals"]["verification"]),
            "has_respectfully_showeth": sum(1 for r in rows if r["signals"]["respectfully_showeth"]),
        }

    out = {
        "total_classified": len(classified),
        "type_counts": dict(type_counts),
        "by_type_signals": by_type_signals,
        "templates": classified,
    }

    out_path = _ROOT / "_probe_100_apps.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"wrote {out_path}")
    print()
    print(f'{"TYPE":<22} {"N":>3}   {"PRAYER":>6} {"VERIF":>5} {"SHOWETH":>7}')
    for typ, stats in by_type_signals.items():
        print(f'{typ:<22} {stats["count"]:>3}   {stats["has_prayer"]:>6} {stats["has_verification"]:>5} {stats["has_respectfully_showeth"]:>7}')


if __name__ == "__main__":
    main()
