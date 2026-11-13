"""Judgments Cited lookup: the court a citation names, the index query, and
the choice between entries that both match (fake index, no network)."""
from __future__ import annotations

import pytest

from core import judgments_cited as jc
from core.judgments_cited import CitedCase


@pytest.mark.parametrize("citation, expected", [
    ("(2007) 11 SCC 243", ("sc", None)),
    ("2023 SCC OnLine SC 326", ("sc", None)),
    ("AIR 1969 SC 128", ("sc", None)),
    ("[1962] 3 SCR 10", ("sc", None)),
    ("2019 INSC 1200", ("sc", None)),
    ("1980 SCC OnLine Del 336", ("hc", "delhi high court")),
    ("AIR 1999 Bom 12", ("hc", "bombay high court")),
    ("2010 (6) Mh.L.J. 401", ("hc", "bombay high court")),
    ("2015 SCC OnLine Ori 77", ("hc", "odisa high court")),     # the index's spelling
    ("", ("", None)),
    ("I (2019) CPJ 12 (NC)", ("", None)),
])
def test_the_court_a_citation_names(citation, expected):
    assert jc._cited_court(citation) == expected


class _FakeES:
    def __init__(self, hits):
        self.hits, self.bodies = hits, []

    def search(self, index, body, request_timeout=None):
        self.bodies.append(body)
        return {"hits": {"hits": [{"_id": str(i), "_source": s} for i, s in enumerate(self.hits)]}}


def test_initials_are_not_compulsory_in_the_index_query():
    bodies = jc._candidate_bodies(CitedCase("A.R. Antulay", "R.S. Nayak"), "parties", "parties", [])
    musts = [b["query"]["bool"]["must"][0]["match"]["parties"]["query"] for b in bodies]
    assert musts == ["antulay", "nayak"]


def test_a_supreme_court_citation_never_takes_a_high_court_judgment():
    es = _FakeES([
        {"petitioner_names": ["Shantilal"], "respondent_names": ["State of Rajasthan"],
         "court_name": "rajasthan high court", "year": 2008, "source": "x.pdf"},
        {"petitioner_names": ["SHANTI LAL"], "respondent_names": ["STATE OF RAJASTHAN"],
         "court_name": "supreme court", "year": 2007, "source": "y.pdf"},
    ])
    found = jc._lookup_hc(es, CitedCase("Shantilal", "State of Rajasthan", "(2007) 11 SCC 243"))
    assert found is not None and found.court == "Supreme Court of India"


def test_a_high_court_citation_never_takes_a_supreme_court_judgment():
    es = _FakeES([{"parties": "PRITAM SINGH VS STATE OF PUNJAB", "judgment_date": "01-01-1980"}])
    assert jc._lookup_sci(es, CitedCase("Pritam Singh", "State of Punjab", "1980 SCC OnLine Del 336")) is None
    assert es.bodies == []                    # not even asked


def test_the_nearest_year_wins_over_a_later_order_in_the_same_case():
    es = _FakeES([
        {"parties": "MUKESH VS STATE FOR NCT OF DELHI", "judgment_date": "09-07-2018"},
        {"parties": "MUKESH VS STATE FOR NCT OF DELHI", "judgment_date": "05-05-2017"},
    ])
    found = jc._lookup_sci(es, CitedCase("Mukesh", "State (NCT of Delhi)", "(2017) 6 SCC 1"))
    assert found is not None and found.date == "05-05-2017"


def test_abbreviated_index_names_are_found():
    es = _FakeES([{"parties": "KUMARI MADHURI PATIL VS ADDL.COMNR.,TRIBAL DEV.THANE .",
                   "judgment_date": "02-09-1994"}])
    found = jc._lookup_sci(es, CitedCase("Kumari Madhuri Patil", "Additional Commissioner, Tribal Development"))
    assert found is not None and found.date == "02-09-1994"
