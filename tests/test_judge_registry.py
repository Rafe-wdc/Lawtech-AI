"""Judge registry: loading, name matching, author lines and bench filters.

The registry file is curated by a person; these tests check the shipped file
is well formed and that the matching rules credit judgments to the right
judge. Index values and author lines below are real ones seen in the
supreme_court_judgement and judgements indexes (2026-11-12).
"""

from __future__ import annotations

import pytest

from core.judge_registry import (
    LAYOUTS, Judge, JudgeSource, RegistryError, bench_filter, date_range_filter,
    detect_author, load_registry, normalize_name, parse_registry,
)
from core.settings import ES_INDICES


def _judge(jid, name, aliases=(), courts=("Supreme Court of India",), sources=None):
    return {
        "judge_id": jid, "name": name, "courts": list(courts), "status": "sitting",
        "aliases": list(aliases),
        "sources": sources or [{"index": "sci_judgments", "bench_phrases": [name.upper()]}],
    }


# ---------------------------------------------------------------------------
# The shipped registry
# ---------------------------------------------------------------------------

def test_shipped_registry_loads_and_is_consistent():
    reg = load_registry()
    assert len(reg.judges) >= 7
    for j in reg.judges:
        for s in j.sources:
            assert s.index_key in ES_INDICES and s.index_key in LAYOUTS
            if s.index_key == "judgments":
                # court names are exact index values, which are lower case
                assert all(c == c.lower() for c in s.court_names)
        # a judge's own name resolves to that judge
        assert reg.resolve(j.name).judge is j


def test_shipped_registry_keeps_namesakes_out():
    reg = load_registry()
    values = {v for j in reg.judges for s in j.sources for v in s.bench_values}
    # other people with similar names, seen next to the right values in the index
    assert not values & {"R. MURALIDHAR", "Mr.MV Muralidharan", "DR. M.R. SHAH", "J.M. Pardiwala"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad, msg", [
    ({"judge_id": "Bad Id"}, "judge_id"),
    ({"status": "acting"}, "status"),
    ({"sources": [{"index": "gst_judgments", "bench_phrases": ["X"]}]}, "unknown index"),
    ({"sources": [{"index": "sci_judgments"}]}, "bench_phrases"),
    ({"sources": [{"index": "judgments", "bench_values": ["X Y"]}]}, "court_names"),
    ({"sources": [{"index": "sci_judgments", "bench_phrases": ["X Y"],
                   "author_values": ["HON'BLE MR. JUSTICE X Y"]}]}, "unknown source fields"),
    ({"sources": []}, "no sources"),
])
def test_invalid_entries_are_rejected(bad, msg):
    entry = {**_judge("sc-x-y", "X Y"), **bad}
    with pytest.raises(RegistryError, match=msg):
        parse_registry({"judges": [entry]})


def test_duplicate_ids_are_rejected():
    with pytest.raises(RegistryError, match="duplicate"):
        parse_registry({"judges": [_judge("sc-a", "A B"), _judge("sc-a", "C D")]})


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, norm", [
    ("HON'BLE MR. JUSTICE M.R. SHAH", "M R SHAH"),
    ("J.B. PARDIWALA, J.:", "J B PARDIWALA"),
    ("Dr. S. Muralidhar, CJ.", "S MURALIDHAR"),
    ("Kurian Joseph, Ag.C.J.", "KURIAN JOSEPH"),
    ("KURIAN JOSEPH, JJ", "KURIAN JOSEPH"),
    ("HON’BLE DR. JUSTICE S.MURALIDHAR", "S MURALIDHAR"),
    ("HON'BLE MRS. JUSTICE B.V. NAGARATHNA", "B V NAGARATHNA"),
    ("SANJIV KHANNA, CJI.", "SANJIV KHANNA"),
    ("HON'BLE THE CHIEF JUSTICE", ""),
])
def test_normalize_name(raw, norm):
    assert normalize_name(raw) == norm


@pytest.fixture
def reg():
    return parse_registry({"judges": [
        _judge("sc-mr-shah", "M.R. Shah", ["Mukeshkumar Rasikbhai Shah"]),
        _judge("sc-jb-pardiwala", "J.B. Pardiwala"),
        _judge("sc-vikram-nath", "Vikram Nath"),
        _judge("hc-s-muralidhar", "S. Muralidhar", courts=("High Court of Delhi",),
               sources=[{"index": "judgments", "bench_values": ["S. MURALIDHAR"],
                         "court_names": ["delhi high court"]}]),
    ]})


@pytest.mark.parametrize("text, jid", [
    ("Justice M.R. Shah", "sc-mr-shah"),
    ("M R Shah", "sc-mr-shah"),
    ("Shah", "sc-mr-shah"),
    ("Mukeshkumar Shah", "sc-mr-shah"),
    ("Pardiwala", "sc-jb-pardiwala"),
    ("JB Pardiwala", "sc-jb-pardiwala"),
    ("Hon'ble Mr. Justice J. B. Pardiwala", "sc-jb-pardiwala"),
    ("vikram nath", "sc-vikram-nath"),
    ("Dr. S. Muralidhar", "hc-s-muralidhar"),
])
def test_resolve(reg, text, jid):
    r = reg.resolve(text)
    assert r.status == "resolved" and r.judge.judge_id == jid


@pytest.mark.parametrize("text", ["Justice Vikram", "K. Shah", "Sandeep Mehta", "", "Justice"])
def test_unresolved(reg, text):
    assert reg.resolve(text).status == "unresolved"


def test_ambiguous_surname_lists_candidates():
    reg = parse_registry({"judges": [
        _judge("sc-mr-shah", "M.R. Shah"),
        _judge("hc-a-shah", "A. Shah", courts=("High Court of Gujarat",),
               sources=[{"index": "judgments", "bench_values": ["A. SHAH"], "court_names": ["gujarat high court"]}]),
    ]})
    r = reg.resolve("Justice Shah")
    assert r.status == "ambiguous"
    assert {j.judge_id for j in r.candidates} == {"sc-mr-shah", "hc-a-shah"}
    # an initial or a court narrows it
    assert reg.resolve("A Shah").judge.judge_id == "hc-a-shah"
    assert reg.resolve("Shah", court_hint="supreme court").judge.judge_id == "sc-mr-shah"


# ---------------------------------------------------------------------------
# Author lines
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, author", [
    ("JUDGMENT\nDigitally signed by\nVISHAL ANAND\n\nPage 1 of 67 \n \n \nJ.B. PARDIWALA, J.:  \nFor the convenience",
     "J.B. PARDIWALA"),
    ("J U D G M E N T \n \nK. VINOD CHANDRAN, J. \n \nLeave granted.", "K. VINOD CHANDRAN"),
    ("J U D G M E N T \nMehta, J.\n1. Leave granted.", "Mehta"),
    ("ORDER\n\nSANJIV KHANNA, CJI.\n\nThe appeal", "SANJIV KHANNA"),
    ("JUDGMENT\nDEVAN RAMACHANDRAN, J.\nThe petitioner", "DEVAN RAMACHANDRAN"),
    # real texts: every space a U+00A0, and an author line without the comma
    ("O\xa0R\xa0D\xa0E\xa0R\nM.R.\xa0SHAH,\xa0J.\n1.\nFeeling", "M.R. SHAH"),
    ("J U D G M E N T\nSANJAY KAROL J. \nProsecution Case", "SANJAY KAROL"),
    # a party above the heading looks like an author line; the real one follows the heading
    ("PETITIONER:\nNIKSON J.\nAGED 36 YEARS\nJUDGMENT\nDEVAN RAMACHANDRAN, J.\nThe petitioner", "DEVAN RAMACHANDRAN"),
    ("JOHNSON C.J.\nAGED 44\n\n COMMON ORDER \n\nVIKRAM NATH, J.\n1.", "VIKRAM NATH"),
])
def test_detect_author(text, author):
    assert detect_author(text) == author


@pytest.mark.parametrize("text", [
    None, "",
    "The appeal is dismissed.\n\n....................J.\n(VIKRAM NATH)\n\n....................J.\n(SANDEEP MEHTA)",
    "ORDER\n\nHeard learned counsel for the parties.",
    "ORDER\nC.J.I.\nList after two weeks.",
    # Kerala HC cause title: advocates whose names end in J (live, 2026-11-12)
    "BY ADVS.\nSRI.PRAVEEN K. JOY\nSRI.E.S.SANEEJ\nGP K.J.MANURAJ\nDOMINIC.T.J\nS/O.JOSEPH",
])
def test_no_author_line(text):
    assert detect_author(text) is None


def test_matches_name_tells_the_judges_apart(reg):
    nath = reg.get("sc-vikram-nath")
    assert nath.matches_name("VIKRAM NATH")
    assert not nath.matches_name("Mehta")
    assert reg.get("sc-jb-pardiwala").matches_name("J.B. PARDIWALA")
    assert not reg.get("sc-jb-pardiwala").matches_name("K. VINOD CHANDRAN")


def test_joint_author_line_names_both(reg):
    line = detect_author("J U D G M E N T\nJ. B. PARDIWALA AND R. MAHADEVAN, JJ.\n1. The appeal")
    assert line == "J. B. PARDIWALA AND R. MAHADEVAN"
    assert reg.get("sc-jb-pardiwala").matches_name(line)
    assert not reg.get("sc-vikram-nath").matches_name(line)


def test_first_name_signature_needs_an_alias():
    # Kurian Joseph signs "KURIAN, J."; without the alias 992 of his SC
    # judgments were credited to nobody (live check, 2026-11-12).
    kurian = load_registry().get("sc-kurian-joseph")
    assert kurian.matches_name(detect_author("JUDGMENT\nKURIAN, J.:\n1. Leave granted."))
    bare = parse_registry({"judges": [_judge("sc-kurian-joseph", "Kurian Joseph")]}).get("sc-kurian-joseph")
    assert not bare.matches_name("KURIAN")


# ---------------------------------------------------------------------------
# Query filters
# ---------------------------------------------------------------------------

def test_sc_bench_filter_tolerates_run_together_names():
    q = bench_filter(JudgeSource(index_key="sci_judgments", bench_phrases=("KURIAN JOSEPH",)))
    should = q["bool"]["should"]
    assert {"match_phrase": {"bench": "KURIAN JOSEPH"}} in should
    span = next(c["span_near"] for c in should if "span_near" in c)
    first, last = span["clauses"][0], span["clauses"][-1]
    # "R.M. LODHAMADAN B. LOKURKURIAN JOSEPH": first word fused to the previous name
    assert first["span_multi"]["match"]["wildcard"]["bench"]["value"] == "*kurian"
    assert last["span_multi"]["match"]["wildcard"]["bench"]["value"] == "joseph*"
    assert span["slop"] == 0 and span["in_order"]


def test_sc_bench_filter_keeps_initials_and_middle_words():
    q = bench_filter(JudgeSource(index_key="sci_judgments", bench_phrases=("ABHAY S. OKA",)))
    span = next(c["span_near"] for c in q["bool"]["should"] if "span_near" in c)
    assert span["clauses"][0]["span_multi"]["match"]["wildcard"]["bench"]["value"] == "*abhay"
    assert span["clauses"][1] == {"span_term": {"bench": "s"}}


def test_single_word_phrase_gets_no_wildcards():
    q = bench_filter(JudgeSource(index_key="sci_judgments", bench_phrases=("PARDIWALA",)))
    assert q["bool"]["should"] == [{"match_phrase": {"bench": "PARDIWALA"}}]


def test_hc_bench_filter_uses_exact_values_and_court():
    src = JudgeSource(index_key="judgments", bench_values=("S. MURALIDHAR", "Dr. S. Muralidhar"),
                      court_names=("delhi high court", "odisa high court"))
    assert bench_filter(src) == {"bool": {"filter": [
        {"terms": {"bench_judge_names.keyword": ["S. MURALIDHAR", "Dr. S. Muralidhar"]}},
        {"terms": {"court_name.keyword": ["delhi high court", "odisa high court"]}},
    ]}}


def test_date_range_uses_each_index_format():
    assert date_range_filter(LAYOUTS["sci_judgments"], 2019, 2023) == {"range": {"judgment_date": {
        "format": "dd-MM-yyyy", "gte": "01-01-2019", "lte": "31-12-2023"}}}
    assert date_range_filter(LAYOUTS["judgments"], 2019, None) == {"range": {"judgment_date": {
        "format": "yyyy-MM-dd", "gte": "2019-01-01"}}}
    assert date_range_filter(LAYOUTS["judgments"], None, None) is None


def test_judge_is_hashable_for_caching(reg):
    assert isinstance(reg.get("sc-mr-shah"), Judge)
    hash(reg.get("sc-mr-shah"))
