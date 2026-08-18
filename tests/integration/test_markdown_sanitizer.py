"""Tests for sanitize_markdown — table normalization in particular.

Covers the LLM output patterns that broke the prod IPC/BNS comparison
response: bare `---` after header row, literal newlines inside cells,
redundant duplicate separator rows. Also regression-checks the
existing behaviours (missing separator, pipe-containing prose,
fenced code blocks).
"""
import sys
import textwrap
from pathlib import Path

# Allow running from repo root: `python tests/integration/test_markdown_sanitizer.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.inline.markdown import sanitize_markdown  # noqa: E402
from core.chat_runner import _strip_html_from_response  # noqa: E402


def _dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


# ---------- table normalization ----------

def test_bare_rule_after_header_promoted_to_separator():
    """LLM emits `---` (HR) between header and data rows. Should be
    rewritten as a proper `|---|---|...|` matching header cell count."""
    src = _dedent(
        """
        | A | B | C |
        ---
        | 1 | 2 | 3 |
        """
    )
    out = sanitize_markdown(src)
    assert "|---|---|---|" in out, f"missing promoted separator, got:\n{out}"
    assert "\n---\n" not in out, f"bare rule not removed, got:\n{out}"
    assert out.count("|---|---|---|") == 1, f"unexpected separator count:\n{out}"


def test_missing_separator_inserted_after_header():
    """Header followed directly by a data row: insert a separator between."""
    src = _dedent(
        """
        | A | B |
        | 1 | 2 |
        """
    )
    out = sanitize_markdown(src)
    assert "|---|---|" in out
    lines = [l for l in out.strip().split("\n") if l.strip()]
    assert lines[0] == "| A | B |"
    assert lines[1] == "|---|---|"
    assert lines[2] == "| 1 | 2 |"


def test_wellformed_table_untouched():
    """Existing correct table must not gain extra separators."""
    src = _dedent(
        """
        | A | B |
        |---|---|
        | 1 | 2 |
        | 3 | 4 |
        """
    )
    out = sanitize_markdown(src)
    assert out.count("|---|---|") == 1, f"separator duplicated:\n{out}"


def test_redundant_separator_inside_body_dropped():
    """LLM emits a second `|---|---|` inside the table body (e.g. after
    we've already promoted a bare rule). Body separator should be removed."""
    src = _dedent(
        """
        | A | B |
        ---
        | 1 | 2 |
        |---|---|
        | 3 | 4 |
        """
    )
    out = sanitize_markdown(src)
    assert out.count("|---|---|") == 1, f"body separator not dropped:\n{out}"


def test_multiline_cell_folded_with_br():
    """Numbered list content on continuation lines inside a cell must
    fold back into the parent row via <br>."""
    src = _dedent(
        """
        | Key | Value |
        |---|---|
        | Steps | Do this:
        1. First
        2. Second
        3. Third |
        | Other | plain |
        """
    )
    out = sanitize_markdown(src)
    # Continuation lines must be gone; the row must be single-line with <br>.
    lines = [l for l in out.strip().split("\n") if l.strip()]
    steps_row = next(l for l in lines if l.startswith("| Steps"))
    assert "<br>1. First" in steps_row, f"first item not folded:\n{steps_row}"
    assert "<br>2. Second" in steps_row
    assert "<br>3. Third" in steps_row
    assert steps_row.rstrip().endswith("|"), f"row not closed:\n{steps_row}"


def test_incident_response_shape():
    """The exact failure shape from the prod IPC/BNS comparison:
    header row, bare `---`, first data row, real separator (positioned
    wrong), then rows with multi-line cells."""
    src = _dedent(
        """
        | Aspect | IPC 299 | IPC 300 | BNS 100 | BNS 101 |
        ---
        | Primary | short | short | short | short |
        |---|---|---|---|---|
        | Conditions | Causing death with:
        1. Intention.
        2. Knowledge. | Murder if:
        1. Intent.
        2. Bodily injury. | mirrors 299 | mirrors 300 |
        """
    )
    out = sanitize_markdown(src)
    assert out.count("|---|---|---|---|---|") == 1, \
        f"expected exactly one separator, got:\n{out}"
    # bare --- must be gone
    for line in out.split("\n"):
        assert line.strip() != "---", f"bare rule survived: {out}"
    # multi-line cell folded
    conditions_row = next(
        l for l in out.split("\n") if l.startswith("| Conditions")
    )
    assert "<br>1. Intention." in conditions_row
    assert "<br>1. Intent." in conditions_row
    assert conditions_row.rstrip().endswith("|")


# ---------- regression cases ----------

def test_pipe_prose_not_treated_as_table():
    """`Either | this | or | that.` is prose, not a table."""
    src = "This is a note: use Either | this | or | that.\n\nBody continues."
    out = sanitize_markdown(src)
    # No separator inserted
    assert "|---|" not in out, f"prose promoted to table:\n{out}"


def test_fenced_code_block_untouched():
    """Pipe content inside a fenced code block must not be table-ified."""
    src = _dedent(
        """
        Regular text.

        ```
        | header | in | code |
        | should | stay | raw |
        ```

        More text.
        """
    )
    out = sanitize_markdown(src)
    # No separator inserted inside the code block
    assert "|---|" not in out


def test_unclosed_fence_gets_closed():
    """Unclosed ``` fence: append a closing fence at EOF."""
    src = "```\nsome code\nno close"
    out = sanitize_markdown(src)
    assert out.count("```") == 2, f"fence not closed:\n{out}"


def test_unclosed_bold_gets_closed():
    """Unclosed `**` bold: append `**` at end of line."""
    src = "Some **unclosed bold"
    out = sanitize_markdown(src)
    assert out.rstrip().endswith("**"), f"bold not closed:\n{out}"


def test_heading_missing_space_fixed():
    """`##Heading` -> `## Heading`."""
    src = "##Heading Text\n"
    out = sanitize_markdown(src)
    assert "## Heading Text" in out


def test_empty_input_returns_empty():
    assert sanitize_markdown("") == ""
    assert sanitize_markdown(None) is None


# ---------- _strip_html_from_response (chat_runner) ----------

def test_strip_preserves_br():
    """<br> must survive the strip — it's needed inside table cells."""
    src = "line1<br>line2"
    out = _strip_html_from_response(src)
    assert "<br>" in out, f"<br> stripped: {out}"


def test_strip_preserves_hr():
    """<hr> must survive the strip."""
    src = "top<hr>bottom"
    out = _strip_html_from_response(src)
    assert "<hr>" in out


def test_strip_removes_script_tag():
    """Actual XSS vector must still be stripped."""
    src = "safe<script>alert(1)</script>text"
    out = _strip_html_from_response(src)
    assert "<script>" not in out
    assert "</script>" not in out
    assert "safe" in out and "text" in out


def test_strip_removes_arbitrary_tags():
    """Any tag we don't explicitly allow gets stripped."""
    src = "<div><span>hello</span></div><iframe src=x></iframe>"
    out = _strip_html_from_response(src)
    for tag in ("<div>", "</div>", "<span>", "</span>", "<iframe", "</iframe>"):
        assert tag not in out
    assert "hello" in out


def test_strip_end_to_end_with_sanitizer():
    """Sanitizer folds cell content with <br>; strip must NOT undo it."""
    src = _dedent(
        """
        | A | B |
        |---|---|
        | Steps | Do:
        1. First
        2. Second |
        """
    )
    sanitized = sanitize_markdown(src)
    stripped = _strip_html_from_response(sanitized)
    # <br> must still be in the pipeline output — otherwise the table breaks
    assert "<br>" in stripped, \
        f"<br> lost between sanitize_markdown and _strip_html_from_response:\n{stripped}"
    # And there must be no literal `\n1. First` inside the row
    steps_row = next(l for l in stripped.split("\n") if l.startswith("| Steps"))
    assert steps_row.rstrip().endswith("|")


def test_very_large_input_short_circuits():
    """Inputs over 100k chars skip line-by-line processing to avoid
    blocking the event loop (existing behaviour)."""
    src = "a" * 100_001
    out = sanitize_markdown(src)
    # Should still return, not hang. Trailing newline appended.
    assert out.endswith("\n")


if __name__ == "__main__":
    import sys
    ns = {k: v for k, v in globals().items() if k.startswith("test_")}
    fails = 0
    for name, fn in ns.items():
        try:
            fn()
            print(f"OK   {name}")
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            fails += 1
        except Exception as e:
            print(f"ERR  {name}: {type(e).__name__}: {e}")
            fails += 1
    print(f"\n{len(ns) - fails}/{len(ns)} passed")
    sys.exit(1 if fails else 0)
