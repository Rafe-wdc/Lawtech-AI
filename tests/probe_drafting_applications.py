"""Ad-hoc probe: pull application templates from the drafting OpenSearch index.

Run from repo root:
    python tests/probe_drafting_applications.py

Goal: see what real "application" templates look like in our drafting corpus so
we can decide whether the outline LLM should force a Prayer section on every
draft type (current behaviour) or whether non-court applications, notices,
police complaints, etc. should follow the template's own shape (no Prayer).

Outputs JSON to stdout: list of {source, type_guess, has_prayer, has_versus,
has_to_block, sections_excerpt, preview}.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Make sure repo root is on path even when invoked from elsewhere.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.clients import get_es_client
from core.settings import ES_INDICES


# Distinct probe queries — each targets a different "application" subtype.
PROBES = [
    "application for income certificate",
    "application for caste certificate",
    "application for domicile certificate",
    "RTI application right to information",
    "application for leave of absence employer",
    "application for no objection certificate NOC",
    "application for ration card",
    "application for character certificate",
    "application for experience certificate",
    "application for marriage certificate",
    "bail application section 483 BNSS",
    "anticipatory bail application",
    "application under Order XXXIX CPC temporary injunction",
    "application under Section 156(3) CrPC",
    "application under Section 482 BNSS quashing",
    "transfer application Section 24 CPC",
    "application for grant of probate",
    "police complaint application FIR",
    "application to bank for loan",
    "application to municipal corporation",
]


def classify(source: str, body: str) -> str:
    s = (source or "").lower()
    b = (body or "").lower()
    if "bail" in s or "bail" in b[:600]:
        return "court_bail_application"
    if "rti" in s or "right to information" in b[:600]:
        return "rti_office_application"
    if "fir" in s or "police" in s or "station house officer" in b[:600]:
        return "police_complaint"
    if "injunction" in s or "order xxxix" in b[:600] or "order 39" in b[:600]:
        return "court_IA_injunction"
    if "certificate" in s and ("income" in s or "caste" in s or "domicile" in s or "character" in s):
        return "office_certificate_application"
    if "transfer" in s and "cpc" in b[:600]:
        return "court_transfer_application"
    if "probate" in s or "grant of probate" in b[:600]:
        return "court_probate_application"
    if "quash" in b[:600] or "482" in b[:600]:
        return "court_quashing_application"
    if "notice" in s:
        return "notice_or_reply"
    if "agreement" in s or "deed" in s or "lease" in s or "mou" in s:
        return "agreement_deed"
    return "unknown"


def fingerprint(body: str) -> dict:
    """Quick signals that tell us what shape the template has."""
    b = body[:3000]  # only need the top — cause-title / opening lives there
    low = b.lower()
    return {
        "has_in_the_court_of": bool(re.search(r"in the court of", low)),
        "has_in_the_matter_of": bool(re.search(r"in the matter of", low)),
        "has_versus": bool(re.search(r"\bversus\b|\bvs\.?\b", low)),
        "has_petitioner_or_plaintiff": bool(re.search(r"petitioner|plaintiff|complainant|appellant", low)),
        "has_to_addressee_block": bool(re.search(r"^\s*to,?\s*$", b, re.MULTILINE)),
        "has_subject_line": bool(re.search(r"\bsubject\s*:?", low)),
        "has_prayer_word": bool(re.search(r"\bprayer\b|prays?\b|relief sought", low)),
        "has_verification": bool(re.search(r"\bverification\b", low)),
        "has_affidavit": bool(re.search(r"\baffidavit\b", low)),
        "has_respectfully_showeth": bool(re.search(r"respectfully\s+showeth", low)),
        "has_pleased_to_pass": bool(re.search(r"pleased to pass|pleased to grant", low)),
    }


def section_headers(body: str, limit: int = 20) -> list[str]:
    """Pull short, header-ish lines (uppercase or numbered roman/arabic)."""
    headers = []
    for line in body.splitlines():
        line = line.strip()
        if not line or len(line) > 80:
            continue
        if re.match(r"^(\d+\.|\([a-z0-9ivx]+\)|[A-Z][A-Z\s/]{4,})", line):
            headers.append(line)
            if len(headers) >= limit:
                break
    return headers


def main() -> None:
    es = get_es_client()
    index = ES_INDICES["drafting"]
    seen_sources: set[str] = set()
    results: list[dict] = []

    for q in PROBES:
        body = {
            "size": 3,
            "query": {"match": {"page_content": q}},
            "_source": ["source", "page_content"],
        }
        try:
            resp = es.search(index=index, body=body)
        except Exception as e:
            print(f"[{q!r}] ERROR: {e}", file=sys.stderr)
            continue
        for hit in resp["hits"]["hits"]:
            src = hit["_source"].get("source", "?")
            if src in seen_sources:
                continue
            seen_sources.add(src)
            text = hit["_source"].get("page_content", "")
            results.append({
                "probe_query": q,
                "source": src,
                "type_guess": classify(src, text),
                "length": len(text),
                "fingerprint": fingerprint(text),
                "header_excerpt": section_headers(text, limit=15),
                "head_preview": text[:600].replace("\n", "\\n"),
            })

    # Sort by type for easier scanning
    results.sort(key=lambda r: (r["type_guess"], r["source"]))
    out = {
        "total_unique_templates": len(results),
        "by_type": {
            t: sum(1 for r in results if r["type_guess"] == t)
            for t in sorted({r["type_guess"] for r in results})
        },
        "templates": results,
    }
    out_path = _ROOT / "_probe_apps.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    # Ascii-safe summary to stdout
    print(f"wrote {out_path} -- {out['total_unique_templates']} templates")
    for t, n in out["by_type"].items():
        print(f"  {t}: {n}")


if __name__ == "__main__":
    main()
