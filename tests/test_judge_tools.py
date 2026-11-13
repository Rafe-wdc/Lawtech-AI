"""Fetching and searching one judge's judgments (tools/shared/judge_tools.py).

The search engine is a fake that serves canned hits page by page the way
OpenSearch does with search_after on _id, and records every request body.
Hit shapes copy real documents from supreme_court_judgement and judgements.
"""

from __future__ import annotations

from core.judge_registry import parse_registry
from tools.shared import judge_tools as jt


class _FakeES:
    def __init__(self, docs_by_index: dict[str, list[dict]], scores: dict[str, float] | None = None):
        self.docs = docs_by_index
        self.scores = scores or {}
        self.bodies: list[tuple[str, dict]] = []

    def search(self, index, body, request_timeout=None):
        self.bodies.append((index, body))
        docs = sorted(self.docs.get(index, []), key=lambda d: d["_id"])
        if "search_after" in body:
            docs = [d for d in docs if d["_id"] > body["search_after"][0]]
        page = []
        for d in docs[: body["size"]]:
            src = {k: v for k, v in d["_source"].items() if k in body["_source"]}
            hit = {"_id": d["_id"], "_source": src, "sort": [d["_id"]],
                   "_score": self.scores.get(d["_id"], 1.0)}
            if "highlight" in body:
                hit["highlight"] = d.get("highlight", {})
            page.append(hit)
        return {"hits": {"hits": page}}


REG = parse_registry({"judges": [
    {"judge_id": "sc-vikram-nath", "name": "Vikram Nath", "courts": ["Supreme Court of India"],
     "status": "sitting", "aliases": [],
     "sources": [{"index": "sci_judgments", "bench_phrases": ["VIKRAM NATH"]}]},
    {"judge_id": "hc-devan-ramachandran", "name": "Devan Ramachandran", "courts": ["High Court of Kerala"],
     "status": "sitting", "aliases": [],
     "sources": [{"index": "judgments", "bench_values": ["DEVAN RAMACHANDRAN"],
                  "court_names": ["kerala high court"]}]},
]})
NATH = REG.get("sc-vikram-nath")
DEVAN = REG.get("hc-devan-ramachandran")
SC, HC = "supreme_court_judgement", "judgements"


def _sc(i, text, judgment_by="HON'BLE MR. JUSTICE VIKRAM NATH",
        bench="HON'BLE MR. JUSTICE VIKRAM NATH HON'BLE MR. JUSTICE SANDEEP MEHTA"):
    return {"_id": f"{i:05d}", "_source": {
        "parties": f"A{i} VS B{i}", "case_no": f"C.A. No.-{i}", "judgment_date": "18-12-2019",
        "bench": bench, "judgment_by": judgment_by, "page_count": 10,
        "pdf_links": [{"label": "PDF", "url": f"https://x/{i}.pdf"}], "full_text": text}}


def _hc(i, bench=("DEVAN RAMACHANDRAN",), text="Disposed of.", case="WP 1/2018", date="2018-10-03", pet="K.SASIKALA"):
    return {"_id": f"h{i:04d}", "_source": {
        "petitioner_names": [pet], "respondent_names": ["STATE OF KERALA"], "case_number": case,
        "judgment_date": date, "bench_judge_names": list(bench), "court_name": "kerala high court",
        "source": f"kerala/{i}.pdf", "page_content": text}}


WROTE = "J U D G M E N T \nVIKRAM NATH, J.\n1. Leave granted."
MEHTA_WROTE = "J U D G M E N T \nMehta, J.\n1. Leave granted."
ORDER = "ORDER\n\nHeard learned counsel. Dismissed."


def test_bench_role_pages_through_everything(monkeypatch):
    monkeypatch.setattr(jt, "PAGE_SIZE", 2)
    es = _FakeES({SC: [_sc(i, WROTE) for i in range(5)]})
    got = jt.fetch_judge_judgments(NATH, es=es)
    assert [j.db_id for j in got] == ["00000", "00001", "00002", "00003", "00004"]
    assert len(es.bodies) == 4                      # 2 + 2 + 1 + empty page
    first = es.bodies[0][1]
    assert first["sort"] == [{"_id": "asc"}] and "search_after" not in first
    assert es.bodies[1][1]["search_after"] == ["00001"]
    assert "full_text" not in first["_source"]       # metadata only unless asked


def test_sc_hit_is_normalised():
    j = jt.fetch_judge_judgments(NATH, es=_FakeES({SC: [_sc(1, WROTE)]}))[0]
    assert j.date == "2019-12-18"
    assert j.bench == ("VIKRAM NATH", "SANDEEP MEHTA") and j.bench_size == 2
    assert j.pdf_url == "https://x/1.pdf" and j.court == "Supreme Court of India"
    assert j.text is None and j.is_author is None


def test_old_run_together_bench_has_unknown_size():
    raw = "R.M. LODHAMADAN B. LOKURVIKRAM NATH"
    j = jt.fetch_judge_judgments(NATH, es=_FakeES({SC: [_sc(1, WROTE, bench=raw)]}))[0]
    assert j.bench == () and j.bench_size is None


def test_author_role_reads_the_text_not_the_field():
    # judgment_by says Vikram Nath on all three; only the first was his.
    es = _FakeES({SC: [_sc(1, WROTE), _sc(2, MEHTA_WROTE), _sc(3, ORDER, judgment_by="")]})
    got = jt.fetch_judge_judgments(NATH, role="author", es=es)
    assert [j.db_id for j in got] == ["00001"]
    assert got[0].author_source == "text" and got[0].text is None   # text dropped again
    assert "full_text" in es.bodies[0][1]["_source"]


def test_author_field_is_recorded_but_proves_nothing():
    # The field named the presiding judge, not the author, in about half of
    # Vikram Nath's recent judgments; with no author line we don't know.
    es = _FakeES({SC: [_sc(1, ORDER)]})
    assert jt.fetch_judge_judgments(NATH, role="author", es=es) == []
    got = jt.fetch_judge_judgments(NATH, include_text=True, es=_FakeES({SC: [_sc(1, ORDER)]}))[0]
    assert got.author_source == "field" and got.author == "HON'BLE MR. JUSTICE VIKRAM NATH"
    assert got.is_author is None and got.text == ORDER


def test_bench_role_with_text_marks_authorship():
    es = _FakeES({SC: [_sc(1, WROTE), _sc(2, MEHTA_WROTE), _sc(3, ORDER, judgment_by="")]})
    got = {j.db_id: j for j in jt.fetch_judge_judgments(NATH, include_text=True, es=es)}
    assert got["00001"].is_author is True
    assert got["00002"].is_author is False and got["00002"].author == "Mehta"
    assert got["00003"].is_author is None


def test_hc_sole_bench_counts_as_author_without_text():
    es = _FakeES({HC: [_hc(1), _hc(2, bench=("DEVAN RAMACHANDRAN", "P.R. RAMACHANDRA MENON"), case="WA 2/2018")]})
    got = {j.db_id: j for j in jt.fetch_judge_judgments(DEVAN, es=es)}
    assert got["h0001"].is_author is True and got["h0001"].author_source == "sole_bench"
    assert got["h0002"].is_author is None
    assert got["h0001"].title == "K.SASIKALA v. STATE OF KERALA" and got["h0001"].source == "kerala/1.pdf"
    assert got["h0001"].date == "2018-10-03"


def test_hc_sole_bench_wins_over_a_misleading_text():
    text = "PETITIONER:\nJOHNSON C.J.\nAGED 44 YEARS\nBY ADVS.\nSRI.X"   # no heading, a party looks like an author
    got = jt.fetch_judge_judgments(DEVAN, role="author", es=_FakeES({HC: [_hc(1, text=text)]}))
    assert [j.author_source for j in got] == ["sole_bench"]


def test_hc_division_bench_author_needs_the_text():
    two = ("P.R. RAMACHANDRA MENON", "DEVAN RAMACHANDRAN")
    es = _FakeES({HC: [
        _hc(1, bench=two, text="JUDGMENT\nDEVAN RAMACHANDRAN, J.\nThe appeal", case="WA 1"),
        _hc(2, bench=two, text="JUDGMENT\nP.R. RAMACHANDRA MENON, J.\nThe appeal", case="WA 2"),
    ]})
    got = jt.fetch_judge_judgments(DEVAN, role="author", es=es)
    assert [j.db_id for j in got] == ["h0001"]


def test_hc_exact_copies_are_kept_once():
    es = _FakeES({HC: [_hc(1), _hc(2), _hc(3, case="WP 9/2018")]})   # h0001 and h0002 are the same judgment
    assert [j.db_id for j in jt.fetch_judge_judgments(DEVAN, es=es)] == ["h0001", "h0003"]


def test_min_chars_drops_short_orders():
    es = _FakeES({HC: [_hc(1, text="Disposed of."), _hc(2, text="x" * 3000, case="WP 2")]})
    got = jt.fetch_judge_judgments(DEVAN, min_chars=1500, es=es)
    assert [j.db_id for j in got] == ["h0002"] and got[0].text is None


def test_filters_reach_the_query():
    es = _FakeES({HC: []})
    jt.fetch_judge_judgments(DEVAN, year_from=2019, year_to=2023, es=es)
    filters = es.bodies[0][1]["query"]["bool"]["filter"]
    assert filters[0]["bool"]["filter"][0] == {"terms": {"bench_judge_names.keyword": ["DEVAN RAMACHANDRAN"]}}
    assert filters[1] == {"range": {"judgment_date": {"format": "yyyy-MM-dd", "gte": "2019-01-01",
                                                      "lte": "2023-12-31"}}}


def test_max_docs_caps_the_scan(monkeypatch):
    monkeypatch.setattr(jt, "PAGE_SIZE", 2)
    es = _FakeES({SC: [_sc(i, WROTE) for i in range(10)]})
    assert len(jt.fetch_judge_judgments(NATH, max_docs=3, es=es)) == 3
    assert [b["size"] for _, b in es.bodies] == [2, 1]


def test_search_is_scoped_to_the_judge_and_returns_highlights():
    docs = [_sc(1, WROTE), _sc(2, WROTE)]
    docs[0]["highlight"] = {"full_text": ["custody of <em>14 months</em>"]}
    es = _FakeES({SC: docs}, scores={"00001": 3.0, "00002": 7.5})
    got = jt.search_judge_judgments(NATH, "bail custody", size=5, es=es)
    assert [j.db_id for j in got] == ["00002", "00001"]
    assert got[1].highlights == ("custody of <em>14 months</em>",)
    q = es.bodies[0][1]["query"]["bool"]
    assert q["must"] == [{"match": {"full_text": "bail custody"}}]
    assert q["filter"][0]["bool"]["should"][0] == {"match_phrase": {"bench": "VIKRAM NATH"}}
    assert "full_text" not in es.bodies[0][1]["_source"]
