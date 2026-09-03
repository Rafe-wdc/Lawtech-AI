"""Tests for the two deterministic drafting gates (CLAUDE.md invariant 2 exception).

Both guard failures that were measured slipping past the self-refine critic:

  1. Off-target language — 4 of 42 regional drafts came back in English.
  2. Section-plan adherence — the writer returned fluent text that ignored
     the section list it was given (Odia: dropped Prayer and Verification,
     invented a statute-dump section).
"""
from __future__ import annotations

import pytest

from agents.drafting import _missing_planned_sections, _normalise_heading, _Section
from core.language import (
    OFF_TARGET_SCRIPT_THRESHOLD, is_off_target_language, output_script_ratio,
)


def sec(sid: str, heading: str) -> _Section:
    return _Section(id=sid, heading=heading, summary="")


# --- off-target language gate ---------------------------------------------

def test_english_is_never_off_target():
    assert not is_off_target_language("In the Hon'ble Sessions Court", "en")
    assert output_script_ratio("anything at all", "en") == 1.0


def test_wholly_english_draft_in_a_regional_request_is_caught():
    """The exact kn_2 / or_3 failure: not one character of the target script."""
    draft = ("## CAUSE TITLE\nIN THE COURT OF THE HON'BLE MAGISTRATE\n"
             "The applicant most respectfully submits as under.")
    assert output_script_ratio(draft, "kn") == 0.0
    assert is_off_target_language(draft, "kn")
    assert is_off_target_language(draft, "or")


def test_correct_regional_draft_passes():
    """Gujarati with English statutory citations — legitimate, must pass."""
    draft = ("## માનનીય સેશન્સ કોર્ટ સમક્ષ\n"
             "### કેસના તથ્યો\nઅરજદાર વિરુદ્ધ ફરિયાદ દાખલ કરવામાં આવી છે "
             "જે Section 316(2) of the Bharatiya Nyaya Sanhita, 2023 હેઠળ છે.\n"
             "### જામીન માટેના કારણો\nઅરજદાર નિર્દોષ છે અને ખોટી રીતે સંડોવાયેલ છે.\n"
             "### પ્રાર્થના\nઅરજદારને જામીન પર મુક્ત કરવા વિનંતી છે.\n"
             "### ચકાસણી\nઉપરોક્ત વિગતો સાચી છે.")
    assert output_script_ratio(draft, "gu") >= OFF_TARGET_SCRIPT_THRESHOLD
    assert not is_off_target_language(draft, "gu")


def test_the_odia_statute_dump_is_caught():
    """Half Odia, half pasted English statute text — the or_1 failure."""
    odia = "ଜାମିନ ଆବେଦନ ପାଇଁ ଆଧାର ଏବଂ ମାମଲାର ତଥ୍ୟ ବିଶ୍ଳେଷଣ କରାଯାଇଛି "
    english = ("Whoever by deceiving any person fraudulently or dishonestly "
               "induces the person so deceived to deliver any property shall be "
               "punished with imprisonment of either description for a term ")
    draft = odia + english * 3
    assert is_off_target_language(draft, "or")


def test_empty_and_unknown_language_do_not_trip_the_gate():
    assert not is_off_target_language("", "gu")
    assert not is_off_target_language("some text", "")
    assert not is_off_target_language("some text", "zz")


# --- section-plan adherence ------------------------------------------------

def test_heading_normalisation_is_script_agnostic():
    assert _normalise_heading("## Prayer") == "prayer"
    assert _normalise_heading("### **Prayer** :") == "prayer"
    assert _normalise_heading("## ପ୍ରାର୍ଥନା") == "ପ୍ରାର୍ଥନା"


def test_all_sections_present_reports_nothing_missing():
    sections = [sec("facts", "Facts of the Case"), sec("prayer", "Prayer")]
    draft = "## Facts of the Case\ntext\n\n## Prayer\ntext"
    assert _missing_planned_sections(draft, sections) == []


def test_dropped_sections_are_detected():
    """The Odia failure: Prayer and Verification planned, never written."""
    sections = [
        sec("cause_title", "ମାମଲାର ଶୀର୍ଷକ"),
        sec("grounds", "ଜାମିନ ପାଇଁ ଆଧାର"),
        sec("prayer", "ପ୍ରାର୍ଥନା"),
        sec("verification", "ସତ୍ୟାପନ"),
    ]
    draft = ("## ନିୟମିତ ଜାମିନ ଆବେଦନ ସମ୍ବନ୍ଧରେ ଏକ ବିଶ୍ଳେଷଣ\n"
             "### ଜାମିନ ପାଇଁ ଆଧାର\ntext")
    missing = _missing_planned_sections(draft, sections)
    ids = {s.id for s in missing}
    assert "prayer" in ids and "verification" in ids
    assert "grounds" not in ids


def test_lengthened_headings_still_count_as_present():
    """The writer may adapt a heading; that is allowed. Dropping is not."""
    sections = [sec("prayer", "Prayer")]
    draft = "## Prayer for Grant of Bail\ntext"
    assert _missing_planned_sections(draft, sections) == []


def test_shortened_headings_still_count_as_present():
    sections = [sec("prayer", "Prayer for Grant of Regular Bail")]
    draft = "## Prayer\ntext"
    assert _missing_planned_sections(draft, sections) == []


def test_subheading_levels_count_as_emitted():
    """gu renders its sections at ### — that is mis-levelled, not missing."""
    sections = [sec("facts", "કેસના તથ્યો"), sec("prayer", "પ્રાર્થના")]
    draft = "## Cause title\n### કેસના તથ્યો\ntext\n### પ્રાર્થના\ntext"
    assert _missing_planned_sections(draft, sections) == []


def test_empty_inputs_are_safe():
    assert _missing_planned_sections("", [sec("a", "A")]) == []
    assert _missing_planned_sections("## A", []) == []


# --- mandatory vs conditional sections -------------------------------------
#
# Half of all planned-vs-emitted gaps were conditional sections the writer was
# RIGHT to drop (no co-accused -> no parity section). Repairing those would
# re-insert irrelevant sections and cost ~25s per call to do it.

from agents.drafting import _is_mandatory_section


@pytest.mark.parametrize("sid,heading", [
    ("cause_title", "In the Court of the Sessions Judge"),
    ("facts", "Facts of the Case"),
    ("facts_of_the_case", "ମାମଲାର ତଥ୍ୟ"),
    ("grounds", "Grounds for Bail"),
    ("grounds_for_bail", "ಜಾಮೀನು ನೀಡಲು ಆಧಾರಗಳು"),
    ("prayer", "ପ୍ରାର୍ଥନା"),
    ("verification", "ଚକାସଣୀ"),
])
def test_mandatory_sections_are_repaired(sid, heading):
    assert _is_mandatory_section(sec(sid, heading))


@pytest.mark.parametrize("sid,heading", [
    ("parity", "Parity with Co-Accused"),
    ("parity_grounds", "ସମାନ କେସର ଆଧାରରେ ଜାମିନ"),
    ("medical_family_grounds", "Medical / Family Grounds"),
    ("family", "ପାରିବାରିକ ଆଧାର"),
    ("undertakings", "Undertakings Offered"),
    ("advocate_details", "Advocate for Applicant"),
    ("list_of_documents", "List of Documents"),
])
def test_conditional_sections_are_never_repaired(sid, heading):
    """Dropping these when the facts are silent is CORRECT behaviour."""
    assert not _is_mandatory_section(sec(sid, heading))


def test_conditional_hint_wins_over_mandatory_substring():
    """'medical_family_grounds' contains 'ground' — it must still read as
    conditional, or the repair re-inserts sections the writer rightly cut."""
    assert not _is_mandatory_section(sec("medical_family_grounds", "Medical Grounds"))
    assert not _is_mandatory_section(sec("parity_grounds", "Parity Grounds"))


def test_the_real_defect_is_still_caught():
    """bn_2: a filing that lost its Verification. That must be repaired."""
    sections = [
        sec("cause_title", "মামলার শিরোনাম"),
        sec("facts", "মামলার ঘটনা"),
        sec("grounds", "জামিনের কারণসমূহ"),
        sec("undertakings", "প্রস্তাবিত অঙ্গীকারসমূহ"),
        sec("prayer", "প্রার্থনা"),
        sec("verification", "যাচাইকরণ"),
    ]
    draft = ("## মামলার শিরোনাম\nx\n## জামিনের কারণসমূহ\nx\n"
             "## প্রস্তাবিত অঙ্গীকারসমূহ\nx\n## প্রার্থনা\nx")
    gone = _missing_planned_sections(draft, sections)
    mandatory_gone = [s.id for s in gone if _is_mandatory_section(s)]
    assert "verification" in mandatory_gone
    assert "facts" in mandatory_gone
    # ...and the conditional one is NOT queued for repair
    assert "undertakings" not in mandatory_gone
