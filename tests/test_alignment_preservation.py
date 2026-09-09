"""Alignment must survive every post-processing pass.

Background (2026-09-09): an advocate reported that nested bullets rendered
as one flat list and that em-dashes still appeared in judgment summaries.
Root causes were three unanchored whitespace collapsers on the response
path (core.url_filter, core.fabricated_provenance, and via the latter
agents.drafting.validate_draft) plus an em-dash fix that lived on the
drafting path only. These tests pin the contract for every pass:

  * leading indentation is never changed (nested lists keep nesting)
  * line breaks are never removed (a dash at end-of-line cannot glue lines)
  * a stray <br> becomes a paragraph break, never a space or a lone "\\n"
  * em/en dashes are normalised on EVERY task type, not just Drafting

Pure-function tests: no LLM, no network.
"""
from __future__ import annotations

import asyncio

import pytest

EM, EN = "—", "–"

NESTED = (
    "1. **Relief:** the Court upheld the decision, keeping\n"
    "   all contentions open.\n"
    "2. The Court clarified that:\n"
    "   - easementary rights\n"
    "     - vehicular access\n"
)


def _indent(text: str) -> list[int]:
    return [len(l) - len(l.lstrip()) for l in text.splitlines() if l.strip()]


# --------------------------------------------------------------- url_filter
class TestUrlFilterKeepsLayout:
    def test_leading_indentation_preserved(self):
        from core.url_filter import sanitize_prose
        assert _indent(sanitize_prose(NESTED)) == _indent(NESTED)

    def test_mid_sentence_gap_still_closed(self):
        from core.url_filter import sanitize_prose
        # No label word ("See:", "Source:") before the URL - those are
        # stripped together with the URL by the labelled-wrapper pass.
        out = sanitize_prose("cited in https://example.com/x during argument.")
        assert out == "cited in during argument."

    def test_streaming_filter_preserves_indentation(self):
        from core.url_filter import StreamingUrlFilter
        f = StreamingUrlFilter()
        out = "".join(f.push(NESTED[i:i + 3]) for i in range(0, len(NESTED), 3))
        out += f.flush()
        assert _indent(out) == _indent(NESTED)


# --------------------------------------------------- fabricated_provenance
class TestProvenanceStripKeepsLayout:
    def test_leading_indentation_preserved(self):
        from core.fabricated_provenance import strip_fabricated_provenance
        out, _ = strip_fabricated_provenance(NESTED)
        assert _indent(out) == _indent(NESTED)

    def test_punctuation_tidy_does_not_cross_lines(self):
        from core.fabricated_provenance import strip_fabricated_provenance
        src = "First line ends here\n, second line starts with a comma\n"
        out, _ = strip_fabricated_provenance(src)
        assert out.count("\n") == src.count("\n")


# ------------------------------------------------------------ validate_draft
class TestValidateDraftKeepsLayout:
    @staticmethod
    def _run(text: str) -> str:
        from agents.drafting import validate_draft
        r = validate_draft(text)
        return r[0] if isinstance(r, tuple) else r

    def test_nested_list_indentation_preserved(self):
        assert _indent(self._run(NESTED)) == _indent(NESTED)

    def test_br_becomes_paragraph_break(self):
        out = self._run("NAME: A<br>SIGNATURE<br/>DEPONENT")
        assert out.splitlines()[0::2][:3] == ["NAME: A", "SIGNATURE", "DEPONENT"]
        assert "\n\n" in out

    def test_line_initial_year_is_escaped(self):
        out = self._run("12. Arrested on 12 March\n2023. In custody since.\n")
        assert "\n2023\\. In custody" in out

    def test_genuine_paragraph_numbers_untouched(self):
        src = "12. First averment.\n\n13. Second averment.\n"
        assert self._run(src) == src

    def test_em_dash_at_line_end_does_not_glue_lines(self):
        src = f"held that the claim {EM}\nis civil in nature.\n"
        out = self._run(src)
        assert out.count("\n") == src.count("\n")
        assert EM not in out


# ------------------------------------------------------- output guardrail
class TestGuardrailNormalisesEveryTask:
    @staticmethod
    def _run(text: str, task: str) -> str:
        from agents.guardrail import guardrail_output_node
        return asyncio.run(
            guardrail_output_node({"final_response": text, "task": task})
        )["final_response"]

    @pytest.mark.parametrize("task", ["Judgment", "Legislation", "Scenario",
                                      "Document", "Drafting", "Other"])
    def test_em_and_en_dashes_removed(self, task):
        src = (f"The claim{EM}concerning an easement{EM}is civil. "
               f"It was held {EN} rightly {EN} that Section 11 applies.")
        out = self._run(src, task)
        assert EM not in out and EN not in out
        assert "claim-concerning an easement-is civil" in out
        assert "held, rightly, that" in out

    def test_dash_at_line_end_keeps_the_line_break(self):
        src = f"line one {EM}\nline two\n"
        out = self._run(src, "Judgment")
        assert "line one" in out.splitlines()[0]
        assert "line two" in out.splitlines()[1]

    def test_nested_indentation_survives_whole_pipeline(self):
        out = self._run(NESTED, "Judgment")
        assert _indent(out) == _indent(NESTED)


# ------------------------------------------------------------- chat_runner
def test_terminal_strip_turns_br_into_paragraph_break():
    from core.chat_runner import _strip_html_from_response
    assert _strip_html_from_response("A<br>B<br/>C") == "A\n\nB\n\nC"


TABLE_ROW = "| **Penalty** | up to 7 years | Tiered:<br>General: 3 years<br>Aggravated: 5 years |"


def test_terminal_strip_keeps_table_rows_on_one_line():
    # The table prompt allows <br> between bullets inside a cell. A newline
    # there would split the row and break the whole table (IPC 420 / BNS 318
    # comparison, 2026-09-09).
    from core.chat_runner import _strip_html_from_response
    out = _strip_html_from_response(TABLE_ROW)
    assert "\n" not in out and "<br" not in out
    assert out.count("|") == TABLE_ROW.count("|")


def test_validate_draft_keeps_table_rows_on_one_line():
    from agents.drafting import validate_draft
    r = validate_draft("| Head A | Head B |\n|---|---|\n" + TABLE_ROW + "\n")
    out = r[0] if isinstance(r, tuple) else r
    assert "<br" not in out
    assert sum(1 for l in out.splitlines() if l.startswith("|")) == 3
