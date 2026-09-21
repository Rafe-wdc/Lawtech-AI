"""Paragraph numbers that go backwards between sections are continued.

Advocate screenshot 2026-09-21: a written statement's Preliminary
Objections ran 1-11 and the Para-wise Reply opened at 9.
"""

import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

from agents.drafting import _renumber_backward_paragraphs, validate_draft


def _numbers(text):
    return [l.split(".")[0] for l in text.splitlines() if l[:1].isdigit()]


WRITTEN_STATEMENT = "\n".join(
    ["## PRELIMINARY OBJECTIONS", ""]
    + [f"{n}. Objection {n}." for n in range(1, 12)]
    + ["", "## PARA-WISE REPLY ON MERITS", ""]
    + [f"{n}. With respect to paragraph {n - 8} of the Plaint, denied." for n in range(9, 18)]
)


def test_para_wise_reply_continues_after_objections():
    out, changed = _renumber_backward_paragraphs(WRITTEN_STATEMENT)
    assert _numbers(out) == [str(n) for n in range(1, 21)]
    assert changed == 9
    # the body text, including references to the Plaint, is untouched
    assert "12. With respect to paragraph 1 of the Plaint, denied." in out


def test_restart_at_one_is_left_alone():
    text = "1. Fact.\n2. Fact.\n3. Fact.\n\n## AFFIDAVIT\n\n1. I say.\n2. I say."
    out, changed = _renumber_backward_paragraphs(text)
    assert (out, changed) == (text, 0)


def test_correct_numbering_is_unchanged():
    text = "\n".join(f"{n}. Para {n}." for n in range(1, 25))
    assert _renumber_backward_paragraphs(text) == (text, 0)


def test_later_sections_keep_the_same_shift():
    text = "\n".join(
        [f"{n}. A{n}" for n in range(1, 6)]      # 1-5
        + [f"{n}. B{n}" for n in range(4, 7)]    # restarts at 4 -> 6-8
        + [f"{n}. C{n}" for n in range(7, 9)]    # writer's own 7-8 -> 9-10
    )
    out, _ = _renumber_backward_paragraphs(text)
    assert _numbers(out) == [str(n) for n in range(1, 11)]


def test_indented_sub_points_and_stranded_years_are_ignored():
    text = (
        "1. Facts.\n2. The events:\n   1. first\n   2. second\n3. More.\n"
        "4. Arrested on 12 March\n2023. The accused was released.\n"
        "5. Last."
    )
    out, changed = _renumber_backward_paragraphs(text)
    assert (out, changed) == (text, 0)


def test_stranded_three_digit_amount_does_not_shift_later_paragraphs():
    text = "1. A.\n2. Paid Rs.\n500. more later.\n3. B.\n4. C."
    out, changed = _renumber_backward_paragraphs(text)
    assert (out, changed) == (text, 0)


def test_validate_draft_renumbers_and_warns():
    out, warnings = validate_draft(WRITTEN_STATEMENT)
    assert _numbers(out) == [str(n) for n in range(1, 21)]
    assert any("Renumbered 9 paragraph" in w for w in warnings)


def test_validate_draft_can_skip_renumbering_for_review_and_redraft():
    out, warnings = validate_draft(WRITTEN_STATEMENT, renumber_paragraphs=False)
    assert _numbers(out)[11] == "9"
    assert not any("Renumbered" in w for w in warnings)


def test_idempotent():
    once, _ = _renumber_backward_paragraphs(WRITTEN_STATEMENT)
    assert _renumber_backward_paragraphs(once) == (once, 0)
