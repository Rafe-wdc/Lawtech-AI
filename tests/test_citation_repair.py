"""A partly-right Supreme Court citation is corrected in the draft.

Lawyer report (2026-11-06): a reply cited "Chandra Shashi v. Muni Lal Goyal,
(1998) 2 SCC 393" for the "stream of justice" principle. The only Supreme
Court judgment by that petitioner is Chandra Shashi v. Anil Kumar Verma,
decided 14-11-1994 — the respondent and the reporter citation were invented.

The ES client is replaced by a fake; extraction, the uniqueness and context
guards, the write-back and the rendering are exercised for real. The drafting
query change (`_topical_query`) is covered at the bottom.
"""

from __future__ import annotations

import asyncio

import core.judgments_cited as jc
from core.judgments_cited import (
    HEADING, NOT_VERIFIED, CitedCase, FoundJudgment, _context_words,
    _pretty_party, _repair_sci, _write_back, append_judgments_cited,
    extract_cited_cases,
)

LAWYER = (
    "The conduct of the Respondent constitutes an egregious abuse of the process of the Court "
    "and an attempt to pollute the stream of administration of justice. In *Chandra Shashi v. "
    "Muni Lal Goyal*, (1998) 2 SCC 393, the Supreme Court held that the stream of justice must "
    "be kept pure and unsullied, and anyone who attempts to pollute it by filing false affidavits "
    "or fabricating evidence commits a serious wrong against the judicial system itself. As held "
    "in Chandra Shashi v. Muni Lal Goyal, perjury erodes public confidence."
)
PDF = "https://lawttorney.s3.ap-south-1.amazonaws.com/sc_pdfs/11377.pdf"
VERIFIED = FoundJudgment("Supreme Court of India", "14-11-1994", PDF,
                         petitioner="Chandra Shashi", respondent="Anil Kumar Verma")


class FakeES:
    """Answers the three queries the module sends: exact party lookup (no
    hit), petitioner-only lookup (`by_petitioner`), and the context rank
    query (`ranked_ids`)."""

    def __init__(self, by_petitioner, ranked_ids):
        self.by_petitioner = by_petitioner
        self.ranked_ids = ranked_ids
        self.queries = []

    def search(self, index, body, request_timeout=None):
        self.queries.append(body)
        q = body["query"]
        if "match" in q and "full_text" in q["match"]:          # context rank
            return {"hits": {"hits": [{"_id": i} for i in self.ranked_ids]}}
        if "bool" in q and "must" in q["bool"]:                   # exact lookup
            return {"hits": {"hits": []}}
        return {"hits": {"hits": [                                # petitioner-only
            {"_id": i, "_source": {"parties": p, "judgment_date": d,
                                   "pdf_links": [{"url": PDF}]}}
            for i, p, d in self.by_petitioner]}}


def _lawyer_case():
    return extract_cited_cases(LAWYER)[0]


# ---------------------------------------------------------------------------
# Extraction keeps what the write-back needs
# ---------------------------------------------------------------------------

def test_extraction_keeps_the_exact_span_and_the_citing_context():
    case = _lawyer_case()
    assert (case.petitioner, case.respondent, case.citation) == (
        "Chandra Shashi", "Muni Lal Goyal", "(1998) 2 SCC 393")
    assert case.raw_name == "Chandra Shashi v. Muni Lal Goyal"
    assert case.raw_tail == "*, (1998) 2 SCC 393"
    assert "stream of justice must be kept pure" in case.context
    assert "pollute the stream of administration" in case.context   # text before it too


# ---------------------------------------------------------------------------
# The repair and its guards
# ---------------------------------------------------------------------------

def test_unique_petitioner_wrong_respondent_confirmed_by_context_is_corrected():
    es = FakeES(by_petitioner=[("11377", "CHANDRA SHASHI VS ANIL KUMAR VERMA", "14-11-1994")],
                ranked_ids=["1", "2", "11377"])
    found = _repair_sci(es, _lawyer_case())
    assert found == VERIFIED
    assert found.corrected


def test_party_names_are_excluded_from_the_context_query():
    es = FakeES(by_petitioner=[("11377", "CHANDRA SHASHI VS ANIL KUMAR VERMA", "14-11-1994")],
                ranked_ids=["11377"])
    _repair_sci(es, _lawyer_case())
    rank_query = es.queries[-1]["query"]["match"]["full_text"]["query"]
    for name in ("chandra", "shashi", "muni", "goyal", "anil", "kumar", "verma"):
        assert name not in rank_query.split()
    assert "pollute" in rank_query and "affidavits" in rank_query


def test_not_corrected_when_the_judgment_is_not_a_top_hit_for_the_sentence():
    es = FakeES(by_petitioner=[("11377", "CHANDRA SHASHI VS ANIL KUMAR VERMA", "14-11-1994")],
                ranked_ids=["1", "2", "3"])
    assert _repair_sci(es, _lawyer_case()) is None


def test_not_corrected_when_the_petitioner_names_two_different_judgments():
    es = FakeES(by_petitioner=[("11377", "CHANDRA SHASHI VS ANIL KUMAR VERMA", "14-11-1994"),
                               ("99999", "CHANDRA SHASHI VS STATE OF BIHAR", "02-02-2001")],
                ranked_ids=["11377"])
    assert _repair_sci(es, _lawyer_case()) is None


def test_the_same_judgment_listed_twice_still_counts_as_one():
    es = FakeES(by_petitioner=[("11377", "CHANDRA SHASHI VS ANIL KUMAR VERMA", "14-11-1994"),
                               ("11378", "CHANDRA SHASHI VS ANIL KUMAR VERMA", "14-11-1994")],
                ranked_ids=["11377"])
    assert _repair_sci(es, _lawyer_case()) == VERIFIED


def test_single_word_petitioner_or_no_context_is_never_repaired():
    es = FakeES(by_petitioner=[("5", "DESAI VS STATE OF MAHARASHTRA", "01-01-2013")],
                ranked_ids=["5"])
    assert _repair_sci(es, CitedCase("Desai", "State of Gujarat", context=LAWYER)) is None
    assert _repair_sci(es, CitedCase("Chandra Shashi", "Muni Lal Goyal", context="")) is None


def test_a_citation_that_already_matches_is_left_to_the_ordinary_lookup():
    es = FakeES(by_petitioner=[("11377", "CHANDRA SHASHI VS ANIL KUMAR VERMA", "14-11-1994")],
                ranked_ids=["11377"])
    right = CitedCase("Chandra Shashi", "Anil Kumar Verma", "(1995) 1 SCC 421", context=LAWYER)
    assert _repair_sci(es, right) is None


def test_wrong_year_with_right_parties_is_corrected_too():
    es = FakeES(by_petitioner=[("11377", "CHANDRA SHASHI VS ANIL KUMAR VERMA", "14-11-1994")],
                ranked_ids=["11377"])
    wrong_year = CitedCase("Chandra Shashi", "Anil Kumar Verma", "(2001) 4 SCC 1", context=LAWYER)
    assert _repair_sci(es, wrong_year) == VERIFIED


# ---------------------------------------------------------------------------
# Write-back and rendering
# ---------------------------------------------------------------------------

def test_write_back_replaces_every_mention_and_the_invented_reporter_citation():
    out = _write_back(LAWYER, _lawyer_case(), VERIFIED)
    assert "Muni Lal Goyal" not in out and "(1998) 2 SCC 393" not in out
    assert "In *Chandra Shashi v. Anil Kumar Verma*, decided on 14-11-1994, the Supreme Court held" in out
    assert "As held in Chandra Shashi v. Anil Kumar Verma, perjury" in out


def test_end_to_end_corrects_the_draft_and_says_so_in_the_block():
    out = asyncio.run(append_judgments_cited(LAWYER, lookup=lambda c: VERIFIED))
    body, block = out.split(HEADING)
    assert "Muni Lal Goyal" not in body
    assert "decided on 14-11-1994" in body
    assert "- *Chandra Shashi v. Anil Kumar Verma* — Supreme Court of India, 14-11-1994" in block
    assert f"[Judgment PDF]({PDF})" in block
    assert "corrected in the draft; it was cited as *Chandra Shashi v. Muni Lal Goyal*, (1998) 2 SCC 393" in block


def test_an_unrepairable_citation_still_leaves_the_body_untouched():
    out = asyncio.run(append_judgments_cited(LAWYER, lookup=lambda c: None))
    body, block = out.split(HEADING)
    assert body.rstrip(" \n-") == LAWYER
    assert NOT_VERIFIED in block


def test_an_exact_match_is_not_reported_as_a_correction():
    plain = FoundJudgment("Supreme Court of India", "14-11-1994", PDF)
    out = asyncio.run(append_judgments_cited(LAWYER, lookup=lambda c: plain))
    assert "Muni Lal Goyal" in out and "corrected" not in out


def test_pretty_party_keeps_initials_abbreviations_and_connectors():
    assert _pretty_party("CHANDRA SHASHI") == "Chandra Shashi"
    assert _pretty_party("STATE OF U.P. AND ORS.") == "State of U.P. and Ors."
    assert _pretty_party("M/S LAXMI DYECHEM") == "M/s Laxmi Dyechem"
    assert _pretty_party("B.L. HANSARIA") == "B.L. Hansaria"
    assert _pretty_party("UNION OF INDIA THR. SECRETARY") == "Union of India thr. Secretary"


def test_context_words_drop_boilerplate_names_and_short_words():
    words = _context_words("The Supreme Court held that Chandra Shashi must pollute the stream of justice",
                           exclude={"chandra", "shashi"})
    assert words == ["must", "pollute", "stream", "justice"]


# ---------------------------------------------------------------------------
# Drafting: the Supreme Court query follows the case-law ask
# ---------------------------------------------------------------------------

def test_topical_query_uses_the_case_law_ask_not_the_document_words():
    from agents.drafting import _topical_query
    q = ("Draft a reply on behalf of the defendant to the plaintiff's application for temporary "
         "injunction under Order 39 Rules 1 and 2 CPC before the Civil Judge Senior Division, Pune. "
         "Cite Supreme Court case laws on false affidavits and polluting the stream of justice.")
    assert _topical_query(q) == "false affidavits polluting stream justice"


def test_topical_query_adds_the_special_act_but_not_a_general_code():
    from agents.drafting import _topical_query
    ni = ("Draft a legal notice under Section 138 of the Negotiable Instruments Act on behalf of "
          "Meera Traders for dishonour of cheque, with supporting judgments regarding deemed service "
          "of statutory notice.")
    assert _topical_query(ni) == "deemed service statutory notice Section 138 Negotiable Instruments Act"
    bail = ("Draft a regular bail application for my client in FIR No. 118/2026 under Section 318(4) "
            "BNS. Cite recent Supreme Court precedents on bail where the chargesheet is filed and the "
            "accused has been in custody for a long period.")
    assert _topical_query(bail) == "bail where chargesheet filed accused has custody long period"


def test_topical_query_without_a_case_law_ask_is_unchanged():
    from agents.drafting import _topical_query
    q = ("Draft a civil suit for damages arising from medical negligence for Mrs. Anjali Deshmukh "
         "against XYZ Hospital and Dr. Rakesh Nair, Pune, for Rs. 25 lakhs.")
    assert _topical_query(q) == ("civil suit damages arising medical negligence Mrs. Anjali Deshmukh "
                                 "against XYZ Hospital Dr. Rakesh")
