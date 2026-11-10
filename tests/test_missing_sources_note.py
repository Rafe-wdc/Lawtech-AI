"""Synthesis is told which citation agents failed, instead of silently
dropping them and letting the model report the gap as a finding."""

from agents.orchestrator import _missing_sources_banner, _missing_sources_note


def test_note_names_the_court_and_forbids_a_no_judgments_finding():
    note = _missing_sources_note({"SCI_Judgment": "TimeoutError: ReAct agent exceeded 90 s"})
    assert "Supreme Court judgments (SCI_Judgment)" in note
    assert "TimeoutError" in note
    assert "NOT because none exist" in note
    assert "Do not write that no such judgments exist" in note
    assert "retry" in note


def test_non_citation_agents_produce_no_note_or_banner():
    assert _missing_sources_note({"Scenario": "ValueError: x"}) == ""
    assert _missing_sources_banner({"Scenario": "ValueError: x"}) == ""
    assert _missing_sources_note({}) == "" and _missing_sources_banner({}) == ""


def test_banner_is_short_user_facing_and_names_every_failed_court():
    banner = _missing_sources_banner({"SCI_Judgment": "TimeoutError", "Judgment": "ValueError"})
    assert banner.startswith("> ⚠ The Supreme Court and High Court judgment search could not be completed")
    assert banner.endswith("\n\n")
    assert "retry" in banner


def test_errors_are_clipped_and_several_agents_are_listed():
    note = _missing_sources_note({"GST_Judgment": "x" * 500, "Tribunal_Judgment": "boom"})
    assert "GST appellate judgments (GST_Judgment)" in note and "tribunal judgments (Tribunal_Judgment)" in note
    assert "x" * 121 not in note
