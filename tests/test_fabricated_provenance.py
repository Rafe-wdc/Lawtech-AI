"""Tests for fabricated-citation-provenance stripping.

Scoped to signals with NO legitimate use in a filed document, so flagging
them cannot produce a false positive. Deciding whether a case is REAL needs
the retrieved-source whitelist and is tracked separately — enabling that
against an incomplete whitelist would strip correct authorities, which is a
worse failure than the one being fixed.
"""
from __future__ import annotations

from datetime import date

import pytest

from core.fabricated_provenance import (
    find_future_dated_judgments, find_non_court_urls, strip_fabricated_provenance,
)

TODAY = date(2026, 9, 3)


# --- retrieval identifiers -------------------------------------------------

def test_the_observed_db_id_is_stripped():
    """The real case: an invented DB ID making a citation look verified."""
    text = ("as held in Parag Kishore Satoskar v. State of Jharkhand, "
            "Crl.A. No.-003803-003803 - 2026 (DB ID: 46429), the applicant...")
    out, warns = strip_fabricated_provenance(text, today=TODAY)
    assert "DB ID" not in out
    assert "46429" not in out
    # The citation itself survives — only the fake provenance goes.
    assert "Parag Kishore Satoskar v. State of Jharkhand" in out
    assert any("retrieval identifier" in w for w in warns)


@pytest.mark.parametrize("marker", [
    "(DB ID: 46429)", "DB ID: 45087", "[doc_id: 12]",
    "(RECORD ID 991)", "SOURCE_ID: 77",
])
def test_retrieval_id_variants(marker):
    out, warns = strip_fabricated_provenance(f"Sanjay Chandra v. CBI {marker} held", today=TODAY)
    assert "Sanjay Chandra v. CBI" in out
    assert not any(ch.isdigit() for ch in out.replace("Sanjay Chandra v. CBI", ""))


def test_real_case_numbers_are_not_touched():
    """Crl.A. numbers and reporter cites are legitimate — never strip them."""
    text = ("Sanjay Chandra v. CBI, (2012) 1 SCC 40; "
            "Crl.A. No.-003803-003803 - 2026; AIR 2012 SC 830")
    out, _ = strip_fabricated_provenance(text, today=TODAY)
    assert "(2012) 1 SCC 40" in out
    assert "Crl.A. No.-003803-003803" in out
    assert "AIR 2012 SC 830" in out


# --- placeholder markers ---------------------------------------------------

@pytest.mark.parametrize("marker", [
    "[citation needed]", "[verify]", "[TBD]", "(citation to be verified)",
    "[TODO]", "(to be confirmed)",
])
def test_placeholder_markers_are_stripped(marker):
    out, warns = strip_fabricated_provenance(f"The principle is settled {marker}.", today=TODAY)
    assert marker.strip("[]()").lower() not in out.lower()
    assert any("placeholder" in w for w in warns)


# --- non-court URLs --------------------------------------------------------

def test_court_and_government_urls_survive():
    text = ("See https://api.sci.gov.in/supremecourt/2023/7943/judgement.pdf and "
            "https://indiacode.nic.in/handle/123456789/2263")
    assert find_non_court_urls(text) == []
    out, warns = strip_fabricated_provenance(text, today=TODAY)
    assert "api.sci.gov.in" in out and "indiacode.nic.in" in out


def test_non_court_urls_are_stripped():
    text = "As discussed at https://randomlawblog.example.com/bail-tips the test is..."
    bad = find_non_court_urls(text)
    assert len(bad) == 1
    out, warns = strip_fabricated_provenance(text, today=TODAY)
    assert "randomlawblog" not in out
    assert any("non-court" in w for w in warns)


# --- future-dated judgments ------------------------------------------------

def test_future_dated_judgment_is_reported_not_deleted():
    """The observed case was 'decided on 12-08-2026' — nine days ahead.

    Reported rather than stripped: deleting only the date would leave a
    fabricated authority looking clean, which is the opposite of the goal.
    """
    text = ("Parag Kishore Satoskar v. State of Jharkhand, "
            "decided on 12-09-2026, held that...")
    found = find_future_dated_judgments(text, today=TODAY)
    assert len(found) == 1
    out, warns = strip_fabricated_provenance(text, today=TODAY)
    assert "12-09-2026" in out, "the date must remain visible for the advocate"
    assert any(w.startswith("UNVERIFIABLE CITATION") for w in warns)


@pytest.mark.parametrize("datestr", ["12-09-2026", "2026-09-12", "12/09/2026"])
def test_future_date_formats(datestr):
    assert find_future_dated_judgments(f"decided on {datestr}", today=TODAY)


def test_past_dated_judgments_are_fine():
    text = ("Sanjay Chandra v. CBI, decided on 23-11-2011; "
            "Dataram Singh v. State of U.P., dated 06-02-2018")
    assert find_future_dated_judgments(text, today=TODAY) == []
    _, warns = strip_fabricated_provenance(text, today=TODAY)
    assert not any(w.startswith("UNVERIFIABLE") for w in warns)


# --- safety ----------------------------------------------------------------

def test_clean_draft_is_untouched():
    text = ("## GROUNDS FOR BAIL\n\n1. That the applicant is innocent.\n"
            "2. As held in Sanjay Chandra v. CBI, (2012) 1 SCC 40, bail is the rule.\n")
    out, warns = strip_fabricated_provenance(text, today=TODAY)
    assert out == text
    assert warns == []


def test_empty_and_none_are_safe():
    assert strip_fabricated_provenance("", today=TODAY) == ("", [])
    assert strip_fabricated_provenance(None, today=TODAY) == (None, [])


def test_regional_script_is_preserved():
    text = ("## ଜାମିନ ପାଇଁ ଆଧାର\nଆବେଦନକାରୀ ନିର୍ଦ୍ଦୋଷ ଅଟନ୍ତି (DB ID: 46429)।\n")
    out, _ = strip_fabricated_provenance(text, today=TODAY)
    assert "DB ID" not in out
    assert "ଆବେଦନକାରୀ ନିର୍ଦ୍ଦୋଷ ଅଟନ୍ତି" in out
