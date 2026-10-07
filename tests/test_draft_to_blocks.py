"""The drafting agent's Markdown draft, converted to layout blocks.

Fixtures reproduce the shapes the drafting agent produced on the dev box on
2026-10-06 (bail application, written statement, s.138 notice), with
synthetic parties.
"""

from __future__ import annotations

from core.draft_blocks import (
    Artifact, HeadingBlock, PageBreakBlock, ParaBlock, RowBlock, SignatureBlock,
    TableBlock, TextBlock, TitleBlock,
)
from core.draft_to_blocks import markdown_to_artifact

BAIL = """**IN THE COURT OF SESSIONS AT PUNE, MAHARASHTRA**

CRIMINAL BAIL APPLICATION NO. [Bail Application No.] OF 2026

IN THE MATTER OF:
C.R. No. 214 of 2026
Mr. Anil Joshi
Age: 34 years
Occupation: [Occupation]

.....Applicant / Accused

**vs**

1. The State of Maharashtra
(Through the learned Public Prosecutor, Sessions Court, Pune)

.....Respondents / State

APPLICATION FOR GRANT OF REGULAR BAIL UNDER SECTION 483 OF THE BHARATIYA NAGARIK SURAKSHA SANHITA, 2023 [CORRESPONDING TO ERSTWHILE SECTION 439 OF THE CODE OF CRIMINAL PROCEDURE, 1973]

MOST RESPECTFULLY SHOWETH:

## STATEMENT OF FACTS

1. The Applicant is a law-abiding citizen residing at [Applicant's Address], Pune, accused under Section 318(4) [cheating and dishonestly inducing delivery of property] of the Bharatiya Nyaya Sanhita, 2023.

2. The Applicant was arrested on 15 March 2026.

## PRAYER

3. It is therefore prayed that this Hon'ble Court may be pleased to:

   (a) release the Applicant on bail;
   (b) pass any other order.

| Date | Event |
|---|---|
| 15.03.2026 | Arrest |

Place: Pune
Date: [Date]

[Name of Advocate]
Advocate for the Applicant

### Verification

I, Mr. Anil Joshi, do hereby verify that the contents of paragraphs 1 to 3 are true.

Deponent
"""

NOTICE = """**[Advocate Name]**

Advocate, High Court

---

**Date:** 06 October 2026

To,

Kiran Shah,

[City, State, Pin Code]

**SUBJECT: STATUTORY DEMAND NOTICE UNDER SECTION 138 OF THE NEGOTIABLE INSTRUMENTS ACT, 1881**

Dear Sir,

1. That you issued cheque No. 123456 arising out of [Nature of Transaction / Source of Debt / Liability].

Yours faithfully,

[Advocate Name]
"""


def _blocks(a: Artifact):
    return [b for s in a.sections for b in s.blocks]


def test_cause_title_is_centred_and_parties_are_rows():
    b = _blocks(markdown_to_artifact(BAIL))
    titles = [x for x in b if isinstance(x, TitleBlock)]
    assert titles[0].text == "IN THE COURT OF SESSIONS AT PUNE, MAHARASHTRA" and titles[0].bold
    assert any(t.text.startswith("CRIMINAL BAIL APPLICATION NO.") for t in titles)
    assert any(t.text == "IN THE MATTER OF:" and not t.bold for t in titles)
    assert any(t.text == "Versus" for t in titles)
    rows = [x for x in b if isinstance(x, RowBlock)]
    assert rows[0].right == [".....Applicant / Accused"] and "Mr. Anil Joshi" in rows[0].left
    assert rows[1].right == [".....Respondents / State"]
    assert rows[1].left[0] == "1. The State of Maharashtra"     # numbered party, not a paragraph


def test_body_headings_paragraphs_subitems_and_table():
    a = markdown_to_artifact(BAIL)
    b = _blocks(a)
    heads = [x.text for x in b if isinstance(x, HeadingBlock)]
    assert heads[:2] == ["STATEMENT OF FACTS", "PRAYER"]
    paras = [x for x in b if isinstance(x, ParaBlock) and x.number]
    assert [p.number for p in paras] == [1, 2, 3]
    assert [s.marker for s in paras[2].sub] == ["(a)", "(b)"]
    table = next(x for x in b if isinstance(x, TableBlock))
    assert table.header == ["Date", "Event"] and table.rows == [["15.03.2026", "Arrest"]]


def test_blanks_versus_annotations():
    b = _blocks(markdown_to_artifact(BAIL))
    p1 = next(x for x in b if isinstance(x, ParaBlock) and x.number == 1)
    assert "[[Applicant's Address]]" in p1.text
    assert "[cheating and dishonestly inducing delivery of property]" in p1.text   # annotation kept
    title = next(x for x in b if isinstance(x, TitleBlock) and x.text.startswith("APPLICATION FOR GRANT"))
    assert "[CORRESPONDING TO ERSTWHILE SECTION 439" in title.text
    nb = _blocks(markdown_to_artifact(NOTICE))
    texts = " ".join(getattr(x, "text", "") for x in nb)
    assert "[[City, State, Pin Code]]" in texts
    assert "[[Nature of Transaction / Source of Debt / Liability]]" in texts


def test_signature_and_verification_page():
    a = markdown_to_artifact(BAIL)
    b = _blocks(a)
    sigs = [x for x in b if isinstance(x, SignatureBlock)]
    assert sigs[0].lines == ["[[Name of Advocate]]", "Advocate for the Applicant"]
    assert sigs[-1].lines == ["Deponent"]
    ver = next(s for s in a.sections if s.heading == "Verification")
    assert isinstance(ver.blocks[0], PageBreakBlock)
    assert any(isinstance(x, TextBlock) and x.text.startswith("Place: Pune") for x in b)


def test_titles():
    assert markdown_to_artifact(BAIL).title.startswith("Application for Grant of Regular Bail under Section 483")
    assert markdown_to_artifact(NOTICE).title.startswith("Statutory Demand Notice under Section 138")


def test_notice_letter_shape():
    b = _blocks(markdown_to_artifact(NOTICE))
    assert isinstance(b[0], TitleBlock) and b[0].text == "[[Advocate Name]]"
    texts = [x.text for x in b if isinstance(x, TextBlock)]
    assert "To," in texts and "Dear Sir," in texts
    assert any(isinstance(x, SignatureBlock) and x.lines[0] == "Yours faithfully," for x in b)


def test_output_is_valid_and_exports():
    from core.draft_docx import artifact_to_docx
    for md in (BAIL, NOTICE, "", "Just one sentence."):
        a = markdown_to_artifact(md)
        Artifact.model_validate(a.model_dump())
        if a.sections:
            assert artifact_to_docx(a).getbuffer().nbytes > 1000


def test_stream_event_is_flag_gated(monkeypatch, tmp_path):
    import asyncio
    from core import artifact_followup as af
    from core import artifact_store as store_mod
    from core import chat_store as cs
    from core.chat_runner import ChatRunnerInputs, _draft_artifact_event
    monkeypatch.setattr(cs, "chat_store", cs._SqliteChatHistoryStore(str(tmp_path / "t.db")))
    monkeypatch.setattr(af, "artifact_store", store_mod._Store())
    i = ChatRunnerInputs(agent_graph=None, query="q", thread_id="4c583f65-3e59-4764-a69d-b77b7baca1fc",
                         is_first_turn=True, endpoint_name="/pyapi/chat")
    monkeypatch.delenv("DRAFT_ARTIFACT_EVENTS", raising=False)
    assert asyncio.run(_draft_artifact_event(i, BAIL, "en")) is None
    monkeypatch.setenv("DRAFT_ARTIFACT_EVENTS", "1")
    ev = asyncio.run(_draft_artifact_event(i, BAIL, "en"))
    assert ev["type"] == "artifact_done" and ev["artifact_id"] == "art_4c583f653e59"
    assert ev["version"] == 1
    Artifact.model_validate(ev["artifact"])
    # a second draft in the same conversation is the next version of the same artifact
    assert asyncio.run(_draft_artifact_event(i, BAIL, "en"))["version"] == 2
    assert asyncio.run(_draft_artifact_event(i, "", "en")) is None
