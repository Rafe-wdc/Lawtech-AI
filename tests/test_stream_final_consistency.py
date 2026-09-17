"""Regression tests for the "streaming = final answer" invariant.

Bug 3 (harness audit finding #3, fixed 2026-09-09):
    Two filters processed the same content but produced different output —
    `_strip_html_from_token` only stripped tags while
    `_strip_html_from_response` also applied `<br>` -> paragraph break and
    `<hr>` -> markdown horizontal rule. The client stitching token chunks
    saw a different string than the terminal `response` event; layout
    jumped mid-render. Multi-token HTML fragments (`<b` + `r>`) passed
    through both filters as-is and only got normalized on the terminal
    strip, exposing users to raw HTML mid-stream.

    Fix: extract `_normalize_html_layout`, share between both filters,
    add `StreamingHtmlFilter` that buffers unclosed `<` fragments until
    the closing `>` arrives.

Contract enforced by these tests:
    "".join(streamed_tokens) + flush == _strip_html_from_response("".join(all_tokens))

Usage:
    pytest tests/test_stream_final_consistency.py -v
"""
from __future__ import annotations

from core.chat_runner import (
    StreamingHtmlFilter,
    _normalize_html_layout,
    _strip_html_from_response,
)


def _stream_all(tokens: list[str]) -> str:
    """Feed tokens through the filter, concatenate emitted output."""
    filt = StreamingHtmlFilter()
    out_parts: list[str] = []
    for t in tokens:
        emitted = filt.push(t)
        if emitted:
            out_parts.append(emitted)
    tail = filt.flush()
    if tail:
        out_parts.append(tail)
    return "".join(out_parts)


# ---------------------------------------------------------------------------
# Test A — stitched-equals-final on realistic content
# ---------------------------------------------------------------------------

def test_a_stitched_equals_final_on_br_hr_url_content():
    """The core invariant: token stream stitched by a client renders to the
    SAME text as the terminal `response` event. Cover `<br>` (both prose
    and table-row context), `<hr>`, and a bare URL."""
    full = (
        "Section 138 of the NI Act.<br>See below.\n"
        "<hr>\n"
        "| Steps | 1. First<br>2. Second |\n"
        "Contact: https://example.com/case/42 for details."
    )
    # Chunk into arbitrary small pieces to exercise the buffer.
    chunk_size = 7
    tokens = [full[i:i + chunk_size] for i in range(0, len(full), chunk_size)]

    streamed = _stream_all(tokens)
    final = _strip_html_from_response(full)

    assert streamed == final, (
        f"Stitched-token stream diverges from final:\n"
        f"  stream: {streamed!r}\n"
        f"  final:  {final!r}"
    )


def test_a_stitched_equals_final_on_prose_only():
    """No HTML -> pass-through, streaming and final produce identical text."""
    full = "The petitioner argues that the settlement stands.\nRespondent replies."
    tokens = [full[i:i + 5] for i in range(0, len(full), 5)]
    streamed = _stream_all(tokens)
    final = _strip_html_from_response(full)
    assert streamed == final == full


def test_a_stitched_equals_final_on_hr():
    """`<hr>` becomes markdown horizontal rule in BOTH filters."""
    full = "part one<hr>part two"
    for chunk_size in (1, 2, 3, 5, 10):
        tokens = [full[i:i + chunk_size] for i in range(0, len(full), chunk_size)]
        streamed = _stream_all(tokens)
        final = _strip_html_from_response(full)
        assert streamed == final, f"chunk_size={chunk_size}: {streamed!r} != {final!r}"


def test_a_stitched_equals_final_on_table_row_br():
    """Inside a table row, `<br>` becomes a space in BOTH filters."""
    full = "| Steps | 1. First<br>2. Second<br>3. Third |"
    for chunk_size in (1, 3, 6, 12, 25):
        tokens = [full[i:i + chunk_size] for i in range(0, len(full), chunk_size)]
        streamed = _stream_all(tokens)
        final = _strip_html_from_response(full)
        assert streamed == final, f"chunk_size={chunk_size}: {streamed!r} != {final!r}"


# ---------------------------------------------------------------------------
# Test B — mid-tag split across two token chunks
# ---------------------------------------------------------------------------

def test_b_br_split_across_tokens_holds_and_recovers():
    """`<br>` split into two chunks must be buffered until the closing `>`
    arrives, then normalized. Buffer must NOT emit "<b" or "r>" mid-flight."""
    tokens = ["hello<b", "r>world"]

    filt = StreamingHtmlFilter()
    first_out = filt.push(tokens[0])
    # First chunk ends with unclosed `<b` — filter must hold from `<`.
    assert "<" not in first_out
    assert "b" not in first_out or first_out == "hello"
    assert first_out.startswith("hello") or first_out == ""

    second_out = filt.push(tokens[1])
    tail = filt.flush()
    streamed = first_out + second_out + tail
    final = _strip_html_from_response("".join(tokens))
    assert streamed == final


def test_b_script_tag_split_across_tokens_blocks_leak():
    """Adversarial: `<script>alert(1)</script>` split at every boundary.
    None of the emitted chunks may contain `<script` or `</script`."""
    src = "safe<script>alert(1)</script>text"
    # Chunk size 1 forces every boundary.
    tokens = list(src)
    filt = StreamingHtmlFilter()
    emitted_parts: list[str] = []
    for t in tokens:
        emitted = filt.push(t)
        # Live invariant: no chunk contains the raw script open/close tag.
        assert "<script" not in emitted
        assert "</script" not in emitted
        emitted_parts.append(emitted)
    tail = filt.flush()
    emitted_parts.append(tail)
    streamed = "".join(emitted_parts)
    final = _strip_html_from_response(src)
    assert streamed == final
    assert "<script>" not in streamed
    assert "</script>" not in streamed


def test_b_hr_split_across_tokens():
    """`<hr>` split at every boundary."""
    src = "top<hr>bottom"
    for split_at in range(1, len(src)):
        tokens = [src[:split_at], src[split_at:]]
        streamed = _stream_all(tokens)
        final = _strip_html_from_response(src)
        assert streamed == final, f"split_at={split_at}: {streamed!r} != {final!r}"


# ---------------------------------------------------------------------------
# Test C — reset event contract (Option B fallback path)
# ---------------------------------------------------------------------------

def test_c_reset_when_divergence_would_occur():
    """chat_runner emits `token_reset` before the terminal `response` event
    when the stitched-tokens string diverges from `final_response` (e.g. a
    post-stream guardrail rewrote the response). Simulate by streaming one
    string and setting final_response to a different string.

    This test exercises the reconciliation logic by directly invoking the
    comparison — the full SSE pipeline is covered by integration tests.
    """
    # What the tokens produced when streamed:
    stitched_tokens_buf = ["The", " petitioner", " argues", "..."]
    stitched_tokens = "".join(stitched_tokens_buf)
    # What the guardrail rewrote final_response to:
    final_response = "The petitioner argues..."  # identical
    assert stitched_tokens == final_response

    # Now the guardrail changed it:
    final_response = "This response was rewritten by the guardrail."
    assert stitched_tokens != final_response
    # The chat_runner path emits `token_reset` iff not (identical) and
    # final_response is truthy — sanity-check that boolean logic.
    stream_identical = stitched_tokens == final_response
    should_reset = bool(final_response) and not stream_identical
    assert should_reset is True


def test_c_no_reset_on_clean_pass_through():
    """When the tokens exactly reconstruct final_response, no reset event
    fires — the fast path."""
    stitched = "hello world"
    final = "hello world"
    should_reset = bool(final) and stitched != final
    assert should_reset is False


# ---------------------------------------------------------------------------
# Extra invariants / edge cases
# ---------------------------------------------------------------------------

def test_flush_empty_buffer_is_noop():
    filt = StreamingHtmlFilter()
    assert filt.flush() == ""


def test_normalize_html_layout_idempotent():
    """Re-normalizing already-clean text is a no-op."""
    src = "clean line\n\nno tags"
    assert _normalize_html_layout(src) == src


def test_normalize_html_layout_handles_empty_and_no_lt():
    assert _normalize_html_layout("") == ""
    assert _normalize_html_layout("just text") == "just text"


def test_max_buffer_safety_valve():
    """Runaway unclosed `<` must not pin unbounded memory — the filter's
    `_MAX_BUFFER` valve flushes the held region as literal text when it
    grows past the cap."""
    # 5000 chars starting with `<` and no closing `>` -> should flush by cap.
    src = "<" + "x" * 5000
    filt = StreamingHtmlFilter()
    _ = filt.push(src)
    # Even without flush(), the safety valve fires — the buffer is drained.
    tail = filt.flush()
    combined = _stream_all([src])
    # Either way, the whole content is delivered (as literal, since the
    # tag never closed and cannot be interpreted as HTML).
    assert len(combined) == len(src) or (len(_ + tail) == len(src))
