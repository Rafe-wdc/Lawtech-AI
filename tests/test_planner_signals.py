"""Unit tests for the orchestrator signal validator (_validate_and_enrich_plan).

Focus: the new "veto" capability that drops Legislation when an old-act
keyword (IPC/CrPC/IEA/BNS/BNSS/BSA) forces Newacts into the plan. Prevents
the dual-routing pattern that caused "Section 125 CrPC" to fan out to both
[Legislation, Newacts] and trigger a 7-second Google web-search fallback in
the Legislation agent.

Pure-Python tests -- no server / no LLM calls.

Usage:
    pytest tests/test_planner_signals.py -v
"""
from __future__ import annotations

import logging

import pytest

from agents.orchestrator import _PLAN_SIGNALS, _validate_and_enrich_plan


_log = logging.getLogger("planner_signals_test")


def _enrich(plan: list[str], query: str) -> list[str]:
    """Test helper: run the validator against a query, return the modified plan."""
    return _validate_and_enrich_plan(list(plan), query, query, _log)


# ---------------------------------------------------------------------------
# Old-act keyword should add Newacts AND veto Legislation
# ---------------------------------------------------------------------------

class TestOldActExclusivity:
    """When the query is about IPC/CrPC/IEA/BNS/BNSS/BSA, Newacts should be
    the only legislation-style agent in the plan.
    """

    @pytest.mark.parametrize("query", [
        "Section 125 of CrPC",
        "Section 125 of Code of Criminal Procedure 1973",
        "Section 125 Cr.P.C",
        "Section 302 IPC",
        "Section 302 of Indian Penal Code",
        "Section 65B Indian Evidence Act",
        "Section 65B IEA",
        "Section 438 BNSS",
        "Section 103 BNS",
        "Section 24 Bharatiya Sakshya Adhiniyam",
        "Section 482 of the Criminal Procedure Code",
    ])
    def test_legislation_replaced_by_newacts(self, query):
        """LLM mistakenly picked Legislation -- validator should swap to Newacts."""
        result = _enrich(["Legislation"], query)
        assert "Newacts" in result, f"Newacts not added for: {query}"
        assert "Legislation" not in result, (
            f"Legislation should have been veto'd for: {query} -- got {result}"
        )

    def test_newacts_already_present_still_vetoes_legislation(self):
        """If both agents are in the plan, veto still strips Legislation."""
        result = _enrich(["Legislation", "Newacts"], "Section 302 IPC")
        assert result == ["Newacts"]

    def test_newacts_alone_unchanged(self):
        """No-op when the plan is already correct."""
        result = _enrich(["Newacts"], "Section 302 IPC")
        assert result == ["Newacts"]

    def test_legislation_only_query_unchanged(self):
        """Queries NOT about the 6 codes should leave Legislation alone."""
        result = _enrich(["Legislation"], "Section 138 of the Negotiable Instruments Act")
        assert "Legislation" in result
        assert "Newacts" not in result


# ---------------------------------------------------------------------------
# Other signals continue to work (no regression from the veto plumbing)
# ---------------------------------------------------------------------------

class TestNonVetoSignalsStillWork:
    def test_supreme_court_keyword_adds_sci(self):
        result = _enrich(["Legislation"], "What did the supreme court say about Article 21")
        assert "SCI_Judgment" in result
        # Legislation has no veto from SCI signal -- stays
        assert "Legislation" in result

    def test_case_law_keyword_adds_judgment(self):
        result = _enrich(["Legislation"], "case law on negligence in hospitals")
        assert "Judgment" in result

    def test_gst_keyword_adds_gst_judgment(self):
        result = _enrich(["Legislation"], "GST advance ruling on input tax credit")
        assert "GST_Judgment" in result

    def test_freshness_keyword_adds_scenario(self):
        result = _enrich(["Legislation"], "latest amendments to the Companies Act")
        assert "Scenario" in result


# ---------------------------------------------------------------------------
# Schema invariant: every _PLAN_SIGNALS entry is a 4-tuple now
# ---------------------------------------------------------------------------

class TestPlanSignalsSchema:
    def test_all_entries_are_4_tuples(self):
        for entry in _PLAN_SIGNALS:
            assert len(entry) == 4, (
                f"Expected (keywords, agent, max_size, also_remove); got {entry!r}"
            )
            keywords, agent, max_size, also_remove = entry
            assert isinstance(keywords, tuple) and all(isinstance(k, str) for k in keywords)
            assert isinstance(agent, str)
            assert isinstance(max_size, int)
            assert isinstance(also_remove, tuple) and all(isinstance(a, str) for a in also_remove)

    def test_newacts_signal_vetoes_legislation(self):
        """Belt-and-braces: the specific rule we just shipped is configured."""
        newacts_entries = [
            e for e in _PLAN_SIGNALS if e[1] == "Newacts"
        ]
        assert len(newacts_entries) >= 1
        # At least one Newacts entry must veto Legislation
        assert any("Legislation" in e[3] for e in newacts_entries), (
            "No Newacts signal vetoes Legislation -- the dual-routing bug will recur"
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_plan(self):
        """Validator on an empty plan should still add Newacts when triggered."""
        result = _enrich([], "Section 125 CrPC")
        assert "Newacts" in result
        assert "Legislation" not in result

    def test_plan_at_max_size_skips_add_but_still_vetoes(self):
        """When the plan is already maxed (>3 agents), the validator should
        NOT add Newacts. But the veto should still remove Legislation if
        Newacts is already present from another path."""
        plan_with_newacts_at_max = ["Newacts", "Judgment", "Legislation", "Scenario"]
        # 4 agents -- exceeds Newacts signal's max_size (3); but Newacts already
        # in plan, so the veto on Legislation should still fire.
        result = _enrich(plan_with_newacts_at_max, "Section 302 IPC")
        assert "Legislation" not in result
        assert "Newacts" in result

    def test_case_insensitive(self):
        result = _enrich(["Legislation"], "SECTION 302 IPC")
        assert "Newacts" in result
        assert "Legislation" not in result
