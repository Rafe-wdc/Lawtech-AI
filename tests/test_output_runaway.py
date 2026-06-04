"""Regression tests for LLM markdown-table runaway protection.

Bug being prevented:
    On the prompt "How to deal with a Criminal Case?" the orchestrator
    synthesis emitted a markdown table separator row containing 124,860
    consecutive dashes (the LLM got stuck "aligning columns" mid-stream).
    That single line was 88% of a 140,555-char response. The existing
    sanitize_markdown short-circuits when input is >100k chars, so the
    runaway flowed through to the user (thread
    73a59cc4-fbfa-4d7b-b493-948a974c1496 on the test server, 2026-06-04).

Two-layer defense:
    1. _collapse_runaway_runs in core/sanitize.py collapses any run of
       100+ identical "padding" chars (-, _, =, space, *) down to 8.
    2. The guardrail output node now calls sanitize_output BEFORE
       sanitize_markdown, so the 100k short-circuit never trips.

Pure-Python tests -- no server / no LLM calls.

Usage:
    pytest tests/test_output_runaway.py -v
"""
from __future__ import annotations

import re

import pytest

from core.sanitize import (
    sanitize_output,
    _collapse_runaway_runs,
    _RUNAWAY_MIN_RUN,
    _RUNAWAY_KEEP,
    _fix_malformed_tables,
)


# ---------------------------------------------------------------------------
# _collapse_runaway_runs
# ---------------------------------------------------------------------------

class TestCollapseRunawayRuns:
    def test_short_runs_untouched(self):
        text = "section --- divider --- next"
        assert _collapse_runaway_runs(text) == text

    def test_dash_runaway_collapsed(self):
        bad = "| Aspect | A | B |\n| :----- | :" + "-" * 124000 + " | :--- |\nrest"
        cleaned = _collapse_runaway_runs(bad)
        # Confirm the runaway is GONE
        assert "-" * 100 not in cleaned, "Runaway dash sequence survived"
        # Confirm the rest of the text is preserved
        assert "| Aspect | A | B |" in cleaned
        assert "rest" in cleaned
        # Confirm the size shrank dramatically
        assert len(cleaned) < 500, f"output still {len(cleaned)} chars"

    def test_space_runaway_collapsed(self):
        bad = "| Cell 1 |" + " " * 200 + "| Cell 2 |"
        cleaned = _collapse_runaway_runs(bad)
        assert " " * 100 not in cleaned

    def test_asterisk_runaway_collapsed(self):
        bad = "Heading\n" + "*" * 500 + "\nNext"
        cleaned = _collapse_runaway_runs(bad)
        assert "*" * 100 not in cleaned

    def test_underscore_runaway_collapsed(self):
        bad = "title:" + "_" * 250 + ":end"
        cleaned = _collapse_runaway_runs(bad)
        assert "_" * 100 not in cleaned

    def test_alphabetic_chars_never_collapsed(self):
        """The pad-char set should not include letters / digits.
        A run of 200 'a's must be left alone even though it's repetitive."""
        bad = "weird " + "a" * 200 + " text"
        cleaned = _collapse_runaway_runs(bad)
        assert "a" * 200 in cleaned

    def test_threshold_just_below(self):
        """A run exactly one short of the threshold is preserved as-is."""
        bad = "x " + "-" * (_RUNAWAY_MIN_RUN - 1) + " x"
        cleaned = _collapse_runaway_runs(bad)
        assert ("-" * (_RUNAWAY_MIN_RUN - 1)) in cleaned

    def test_threshold_just_above(self):
        bad = "x " + "-" * _RUNAWAY_MIN_RUN + " x"
        cleaned = _collapse_runaway_runs(bad)
        assert ("-" * _RUNAWAY_KEEP) in cleaned
        assert ("-" * _RUNAWAY_MIN_RUN) not in cleaned

    def test_multiple_runs_collapsed(self):
        bad = "a " + "-" * 300 + " mid " + "=" * 300 + " end"
        cleaned = _collapse_runaway_runs(bad)
        assert "-" * 100 not in cleaned
        assert "=" * 100 not in cleaned
        assert "mid" in cleaned and "end" in cleaned

    def test_idempotent(self):
        bad = "| A |" + "-" * 1000 + "| B |"
        once = _collapse_runaway_runs(bad)
        twice = _collapse_runaway_runs(once)
        assert once == twice

    def test_empty_string(self):
        assert _collapse_runaway_runs("") == ""


# ---------------------------------------------------------------------------
# sanitize_output -- end-to-end via the public entry point
# ---------------------------------------------------------------------------

class TestSanitizeOutputEndToEnd:
    def test_criminal_case_response_shape_repaired(self):
        """The actual shape that triggered the incident: a table header,
        then a 125k-dash separator row, then unrelated content below."""
        header = "| Aspect | CrPC | BNSS |"
        runaway_separator = "| :----- | :" + "-" * 124000 + " | :--- |"
        after = "\nSCI judgment content follows here."
        bad = "intro\n" + header + "\n" + runaway_separator + after

        cleaned = sanitize_output(bad)

        # 1. Total length now sane (was ~125k, should be <1500)
        assert len(cleaned) < 1500, f"cleaned still {len(cleaned)} chars"
        # 2. No line is anywhere close to the runaway length
        for line in cleaned.split("\n"):
            assert len(line) < 500, f"line too long: {line[:80]}"
        # 3. The downstream content is preserved
        assert "SCI judgment" in cleaned
        # 4. The table header is preserved
        assert "Aspect" in cleaned

    def test_normal_response_passes_through(self):
        clean = (
            "## Heading\n\n"
            "Some prose paragraph here.\n\n"
            "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
            "More prose.\n"
        )
        out = sanitize_output(clean)
        # Allow for minor whitespace normalisation but content identical
        assert "Some prose paragraph" in out
        assert "| A | B |" in out
        assert "More prose." in out


# ---------------------------------------------------------------------------
# _fix_malformed_tables -- existing pass, still relevant for shape A
# ---------------------------------------------------------------------------

class TestFixMalformedTables:
    def test_overlong_pure_separator_rewritten(self):
        """If _collapse_runaway_runs didn't shrink it (or the run was
        shorter than threshold) _fix_malformed_tables is the next line."""
        bad = "| --- | --- | --- " + "| --- " * 30 + "|"
        out = _fix_malformed_tables(bad)
        # Result should be a sane 3-10 column separator
        col_count = out.count("---")
        assert 3 <= col_count <= 10


# ---------------------------------------------------------------------------
# Guardrail wiring -- ensure sanitize_output is actually imported + called
# ---------------------------------------------------------------------------

class TestGuardrailWiring:
    def test_guardrail_imports_sanitize_output(self):
        """If someone removes the import, the wiring is broken."""
        import agents.guardrail as g
        assert hasattr(g, "sanitize_output"), (
            "guardrail.py must import sanitize_output (see incident "
            "73a59cc4 -- runaway table separator bypassed sanitize_markdown)"
        )

    def test_guardrail_output_node_calls_sanitize_output(self):
        """Inspect the source of guardrail_output_node to make sure it
        actually calls sanitize_output. A future refactor must keep this."""
        import inspect
        import agents.guardrail as g
        src = inspect.getsource(g.guardrail_output_node)
        assert "sanitize_output(" in src, (
            "guardrail_output_node must call sanitize_output before "
            "sanitize_markdown -- the order matters (see comment in source)."
        )


# ---------------------------------------------------------------------------
# Defense in depth -- TABLE_FORMATTING_RULES is in the prompts
# ---------------------------------------------------------------------------

class TestPromptDefenses:
    def test_synthesis_prompt_has_table_rules(self):
        from config.prompts import SYNTHESIS_PROMPT
        assert "no whitespace padding" in SYNTHESIS_PROMPT.lower()

    def test_synthesis_table_prompt_has_table_rules(self):
        from config.prompts import SYNTHESIS_TABLE_PROMPT
        assert "no whitespace padding" in SYNTHESIS_TABLE_PROMPT.lower()
