"""Second probe — does the drafting index hold ANY office-application
(non-court letter to admin authority) templates?

Strategy: BM25 on phrases that ONLY appear in office letters:
  - "Public Information Officer" / "Tahsildar" / "Branch Manager"
  - "RTI Act, 2005"
  - "income certificate"
  - "leave of absence"
  - "No Objection Certificate"
  - "Subject:" + "Sir, Madam" combo

Then check the BODY head of every hit — if it really is an office letter,
it'll start with "To," and not with "IN THE COURT OF".
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.clients import get_es_client
from core.settings import ES_INDICES

PROBES = [
    '"Public Information Officer"',
    '"Right to Information"',
    '"income certificate" Tahsildar',
    '"caste certificate" SDM',
    '"leave of absence" employer',
    '"No Objection Certificate" society',
    '"Branch Manager" loan',
    '"ration card" application',
    '"Sir / Madam" subject',
    '"To, The Public Information Officer"',
    '"To the Tahsildar"',
    '"domicile certificate"',
    '"transfer certificate" school',
    '"NOC from society"',
    '"birth certificate" municipal',
]


def is_office_letter(body: str) -> bool:
    """Heuristic: starts with To,/Date and no court header."""
    head = body[:1500].lower()
    has_to_block = bool(re.search(r"^\s*to,?\s*$", body[:1500], re.MULTILINE))
    has_court = "in the court of" in head
    has_matter = "in the matter of" in head
    has_versus = bool(re.search(r"\bversus\b", head))
    return has_to_block and not (has_court or has_matter or has_versus)


def main() -> None:
    es = get_es_client()
    index = ES_INDICES["drafting"]

    # Also fetch overall count
    cnt = es.count(index=index, body={"query": {"match_all": {}}})
    print(f"Total docs in '{index}' index: {cnt['count']}")
    print()

    seen: set[str] = set()
    matches: list[dict] = []
    candidates: list[dict] = []

    for q in PROBES:
        body = {
            "size": 5,
            "query": {"query_string": {"query": q, "default_field": "page_content"}},
            "_source": ["source", "page_content"],
        }
        try:
            resp = es.search(index=index, body=body)
        except Exception as e:
            print(f"[{q!r}] ERROR: {e}", file=sys.stderr)
            continue
        hits = resp["hits"]["hits"]
        print(f"[{q}] -> {len(hits)} hits")
        for hit in hits:
            src = hit["_source"].get("source", "?")
            text = hit["_source"].get("page_content", "")
            if src not in seen:
                seen.add(src)
                head = text[:300].replace("\n", " ")
                is_letter = is_office_letter(text)
                rec = {
                    "query": q,
                    "source": src,
                    "is_office_letter": is_letter,
                    "head": head,
                }
                candidates.append(rec)
                if is_letter:
                    matches.append(rec)
            print(f"    {'LETTER' if is_office_letter(text) else 'COURT?':<7} {src}")

    print()
    print(f"=== {len(matches)} confirmed office-letter templates out of {len(candidates)} unique hits ===")
    out_path = _ROOT / "_probe_office_apps.json"
    out_path.write_text(json.dumps({
        "total_index_docs": cnt["count"],
        "office_letter_matches": matches,
        "all_candidates": candidates,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
