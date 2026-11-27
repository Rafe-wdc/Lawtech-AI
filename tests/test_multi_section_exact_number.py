"""Within a known act, the multi-section search prefers the stored section number.

Lawyer test report, 2026-11-25. The SARFAESI question asked for Sections
13(4) and 14. The search found the right act, then looked each section up
only by phrases such as "section 13 of <act name as the user wrote it>". The
question said "Securities Interest" where the index says "Security
Interest", so no phrase matched, and loose word matching returned Sections
36, 26D, 37, 1 and 42 for 13, and Section 28 for 14 (seen 2026-11-27).

Every legislation document carries its number in `section_number`. Once the
act is known, a match on that field now outranks the phrase variations.
Without a known act it is not used: it would pull that section number from
every act in the index.

Measured on 116 random sections of 58 random acts, twice: when the act is
found, the asked section came first 147/156 and 153/164 times, against
117/156 and 123/164 before; no ask got worse.
"""

from __future__ import annotations

from unittest.mock import patch

import tools.shared.elasticsearch_tools as est

SARFAESI = ("drive/MyDrive/LawTech-Data/Central and State Legislations/CENTRAL/"
            "Central_Acts_renamed_csv_column/Securitisation and Reconstruction of "
            "Financial Assets and Enforcement of Security Interest Act 2002.csv")


def _run(discovered_source: str | None):
    bodies: list[dict] = []

    def fake_search(index, body):
        bodies.append(body)
        if "aggs" in body:
            buckets = [{"key": discovered_source, "doc_count": 61}] if discovered_source else []
            return {"aggregations": {"by_source": {"buckets": buckets}}, "hits": {"hits": []}}
        return {"hits": {"hits": []}}

    with patch.object(est, "_es_search", side_effect=fake_search), \
         patch.object(est, "get_es_client", return_value=object()):
        est._search_legislation_multi_section(
            "Can the bank take possession under Section 13(4) or Section 14?",
            ["13", "48b"],
            "financial assets and enforcement of securities interest act 2002",
        )
    return [b for b in bodies if "aggs" not in b and b.get("size") == 5]


def _number_clauses(body: dict) -> list[dict]:
    return [c for c in body["query"]["bool"]["should"]
            if "section_number" in str(list(c.values())[0])]


def test_known_act_adds_the_section_number_match():
    per_section = _run(SARFAESI)
    assert len(per_section) == 2
    for body, num in zip(per_section, ["13", "48b"]):
        assert body["query"]["bool"]["filter"] == [{"term": {"source.keyword": SARFAESI}}]
        clauses = _number_clauses(body)
        assert {"match": {"section_number": {"query": num, "boost": 20.0}}} in clauses
        terms = [c["terms"] for c in clauses if "terms" in c]
        # stored numbers keep their capitals ("48B"); the parser lowercases
        assert terms and set(terms[0]["section_number.keyword"]) == {num, num.upper()}


def test_unknown_act_does_not_use_the_section_number():
    per_section = _run(None)
    assert per_section
    for body in per_section:
        assert body["query"]["bool"]["filter"] == []
        assert _number_clauses(body) == []
