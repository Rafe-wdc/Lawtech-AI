"""Party names written differently in a draft and in our judgment index.

The general rules behind core/party_names.py, each with a pair seen in the
Supreme Court index (2026-11-13 evaluation), and the look-alikes that must
stay apart.
"""
from __future__ import annotations

import pytest

from core.party_names import match_parties, match_side, petitioner_ok, respondent_ok


def _pet(cited: str, index: str) -> bool:
    return petitioner_ok(match_side(cited, index))


def _resp(cited: str, index: str) -> bool:
    return respondent_ok(match_side(cited, index))


@pytest.mark.parametrize("cited, index", [
    # abbreviations written with a full stop, also glued to the next word
    ("Additional Commissioner, Tribal Development", "ADDL.COMNR.,TRIBAL DEV.THANE ."),
    ("Rajasthan State Road Transport Corporation", "RAJASTHAN STATE ROAD TRPT. CORPN."),
    ("West Bengal Electricity Regulatory Commission", "WEST BENGAL ELECT. REGULT. COMMISSION"),
    ("Delhi Subordinate Services Selection Board", "DELHI SUBORDINATE SER.SEL.BOARD"),
    # contractions without a full stop
    ("Commr", "COMMISSIONER OF CUSTOMS(IMPORTS) RAIGAD"),
    ("Mohammad Yusuf", "MOHD. YUSUF"),
    # acronyms in the draft, full names in the index
    ("ONGC", "OIL & NATURAL GAS CORPN.LTD."),
    ("Kerala SRTC", "KERALA STATE ROAD TRANSPORT CORPORATION"),
    ("CIT", "COMMISSIONER OF INCOME TAX,BOMBAY CITY-III"),
    ("CCE", "COMMNR. OF CENTRAL EXCISE, SURAT-I"),
    # initials in the index, full names in the draft
    ("State of Uttar Pradesh", "STATE OF U.P."),
    ("State of Andhra Pradesh", "STATE OF A.P."),
    ("Union of India", "U O I"),
    ("BSES", "B.S.E.S. LTD."),
    # the same letters joined or split differently
    ("Bhaiya Lal", "BHAIYALAL"),
    ("Hemraj Multanchand", "HEM RAJ MULTAN CHAND"),
    ("Shantilal", "SHANTI LAL"),
    # singular / plural, spelling and OCR variants of long names
    ("M/s. Pankaj Jain Agencies", "M/S PANKAJ JAIN AGENCY"),
    ("Babu Verghese", "BABU VARGHESE"),
    ("Vankamamidi Venkata Subba Rao", "VANKAMAMIDL VENKATA SUBBA RAO"),
    # "State of X" and "Government of X" are the same party
    ("State of U.P.", "GOVT.OF U.P.& ORS."),
    # a person's initials may be missing or split on the other side
    ("A.R. Antulay", "A. R. ANTULAY"),
    ("Major S. K. Kale", "S.K. KALE"),
])
def test_the_same_party_written_differently(cited, index):
    assert _pet(cited, index) and _resp(cited, index)


@pytest.mark.parametrize("cited, index", [
    ("State of M.P.", "STATE OF UTTAR PRADESH"),               # initials are a state
    ("State of H.P.", "STATE OF MADHYA PRADESH"),
    ("State of U.P.", "KERALA STATE ROAD TRANSPORT CORPORATION"),
    ("Meerut Development Authority", "THE DELHI DEVELOPMENT AUTHORITY ORS."),
    ("Ram Prakash Sharma", "RAM PRAKASH GUPTA"),               # a surname always counts
    ("U.P. State Road Transport Corporation", "RAJASTHAN STATE ROAD TRANSPORT CORPORATION"),
    ("State of T.N.", "STATE TRANSPORT APPELLATE TRIBUNAL"),
    ("Mohan Kumar", "MAH. KUMAR"),                             # "Mah." cannot be read from "Mohan"
])
def test_look_alikes_stay_apart(cited, index):
    assert not (_pet(cited, index) and _resp(cited, index))


def test_one_misspelt_word_is_tolerated_only_in_an_otherwise_exact_respondent():
    assert _resp("Kathi Kalu Gohad & Ors.", "KATHI KALU OGHAD AND OTHERS")
    assert not _resp("Kathi Gohad", "KATHI OGHAD")          # one exact word is not enough
    assert not _resp("Meerut Development Authority", "DELHI DEVELOPMENT AUTHORITY")


def test_petitioner_needs_every_identifying_word():
    assert _pet("Durga", "DURGA SHANKAR MEHTA")              # index may say more
    assert not _pet("Durga Prasad", "DURGA SHANKAR MEHTA")
    assert not _pet("Silpi Industries", "SILPA TEXTILES")


def test_closer_names_score_higher():
    exact = match_parties("Rampur Urban Co-operative Bank", "State of Maharashtra",
                          "RAMPUR URBAN CO-OPERATIVE BANK LTD.", "THE STATE OF MAHARASHTRA")
    other = match_parties("Rampur Urban Co-operative Bank", "State of Maharashtra",
                          "SITAPUR URBAN CO-OPERATIVE BANK LTD. THE LIQUIDATOR RAMPUR URBAN CO-OPERATIVE BANK",
                          "THE STATE OF MAHARASHTRA")
    assert exact is not None and other is not None and exact > other
