"""Filling a draft's blanks from one form (core/draft_fields.py): the fields in
every draft response and event, and POST /pyapi/artifact/{id}/fill."""

from __future__ import annotations

import asyncio

import pytest

from core.draft_blocks import (
    Artifact, ParaBlock, RowBlock, Section, SignatureBlock, SubItem, TableBlock, to_markdown,
)
from core.draft_fields import clean_value, field_key, fields, fill
from core.draft_to_blocks import markdown_to_artifact

BAIL = """**IN THE COURT OF SESSIONS AT [District]**

**BAIL APPLICATION NO. [BAIL APPLICATION NO.] OF 2026**

**IN THE MATTER OF:**

[NAME OF APPLICANT]
Son of [Father's Name]
Aged about [Age] years ...Applicant

Versus

**STATE OF [STATE]** ...Respondent

## BRIEF FACTS

1. The Applicant was arrested by the police of [Police Station], District [DISTRICT], on [Date of Arrest].

2. The Applicant is the son of [FATHER’S NAME] and has no criminal antecedents.

## PRAYER

3. It is prayed that the Applicant be released on bail.

Place: [District]
Date: [Date]
"""


def _bail() -> Artifact:
    return markdown_to_artifact(BAIL, artifact_id="art_fill")


def test_one_blank_spelled_differently_is_one_field():
    fs = {f["key"]: f for f in fields(_bail())}
    assert list(fs)[:4] == ["district", "bail application no", "name of applicant", "fathers name"]
    assert fs["district"]["count"] == 3 and fs["district"]["label"] == "District"
    assert [p["section_id"] for p in fs["district"]["places"]] == ["s1", "s2", "s3"]
    assert fs["district"]["places"][0]["context"] == "IN THE COURT OF SESSIONS AT [District]"
    assert fs["fathers name"]["count"] == 2 and fs["fathers name"]["label"] == "Father's Name"
    assert fs["bail application no"]["label"] == "BAIL APPLICATION NO."   # only spelling
    assert field_key(" FIR No. ") == field_key("[fir no]".strip("[]")) == "fir no"
    assert {k: f["kind"] for k, f in fs.items() if f["kind"] != "text"} == {
        "age": "number", "date of arrest": "date", "date": "date"}


def test_each_value_fills_every_place_in_the_case_of_the_words_around_it():
    new, filled, unknown = fill(_bail(), {
        "district": "Pune", "Father's Name": "Ramesh Kumar",
        "name of applicant": "Rahul Sharma", "STATE": "Maharashtra"})
    md = to_markdown(new)
    assert "IN THE COURT OF SESSIONS AT PUNE" in md            # cause title: capitals
    assert "STATE OF MAHARASHTRA" in md
    assert "RAHUL SHARMA" in md                                 # alone on its line: label's case
    assert "Son of Ramesh Kumar" in md and "the son of Ramesh Kumar and" in md
    assert "District Pune, on [Date of Arrest]" in md           # inside a sentence: as typed
    assert "Place: Pune" in md
    assert filled == ["district", "name of applicant", "fathers name", "state"] and unknown == []
    left = {f["key"] for f in fields(new)}
    assert left == {"bail application no", "age", "police station", "date of arrest", "date"}


def test_unknown_and_empty_values_and_the_input_draft():
    a = _bail()
    before = a.model_dump()
    new, filled, unknown = fill(a, {"age": "  ", "Name of Accused": "X", "date": "1 May 2026"})
    assert filled == ["date"] and unknown == ["name of accused"]
    assert "Date: 1 May 2026" in to_markdown(new)
    assert a.model_dump() == before                             # the input is not changed
    assert fill(a, {})[1] == []


def test_a_value_stays_one_line_and_cannot_add_a_blank():
    assert clean_value("Flat 12, Tower B\nBaner Road\r\nPune") == "Flat 12, Tower B, Baner Road, Pune"
    assert clean_value("[[Name]]") == "[Name]"
    assert clean_value(42) == "42"
    assert len(clean_value("x" * 2000)) == 500
    new, _, _ = fill(_bail(), {"district": "[[Pune]]"})
    assert [f["key"] for f in fields(new)].count("pune") == 0


def test_blanks_in_rows_signatures_tables_and_sub_items():
    a = Artifact(artifact_id="art_x", title="T", doc_type="x", sections=[Section(
        id="s1", heading="All", blocks=[
            RowBlock(id="b1", left=["[[Name of Complainant]]"], right=["...Complainant"]),
            ParaBlock(id="b2", text="Facts.", sub=[SubItem(marker="(a)", text="Paid to [[Name of Complainant]].")]),
            TableBlock(id="b3", header=["Party", "Address"], rows=[["[[Name of Complainant]]", "[[Address]]"]]),
            SignatureBlock(id="b4", lines=["[[Name of Complainant]]", "Complainant"]),
        ])])
    assert fields(a)[0]["count"] == 4
    new, filled, _ = fill(a, {"name of complainant": "Asha Rao", "address": "Pune"})
    b = new.sections[0].blocks
    assert b[0].left == ["Asha Rao"]                          # alone, label not in capitals
    assert b[1].sub[0].text == "Paid to Asha Rao."
    assert b[2].rows == [["Asha Rao", "Pune"]]
    assert b[3].lines[0] == "Asha Rao"
    assert fields(new) == []


def test_regional_language_blanks():
    md = "**सत्र न्यायालय, [ज़िला]**\n\n1. आवेदक [आवेदक का नाम] को [ज़िला] पुलिस ने गिरफ्तार किया।"
    a = markdown_to_artifact(md, artifact_id="art_hi", language="hi")
    fs = fields(a)
    assert [f["count"] for f in fs] == [2, 1]
    new, filled, _ = fill(a, {fs[0]["key"]: "पुणे", "आवेदक का नाम": "राहुल"})
    md2 = to_markdown(new)
    assert "सत्र न्यायालय, पुणे" in md2 and "आवेदक राहुल को पुणे पुलिस" in md2


# --- endpoints ----------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import core.gateway as gw
    from core import artifact_store as mod
    from core import chat_store as cs
    monkeypatch.setattr(cs, "chat_store", cs._SqliteChatHistoryStore(str(tmp_path / "t.db")))
    s = mod._Store()
    monkeypatch.setattr(mod, "artifact_store", s)
    asyncio.run(s.save(_bail(), thread_id="t1", source=mod.SOURCE_DRAFT))
    gw.app.dependency_overrides[gw.require_user_key] = lambda: None
    try:
        yield TestClient(gw.app)
    finally:
        gw.app.dependency_overrides.pop(gw.require_user_key, None)


def test_the_draft_object_carries_its_fields_and_post_fill_returns_it(client):
    r = client.get("/pyapi/artifact/art_fill").json()
    assert r["version"] == 1 and r["blanks"] == 12 and len(r["fields"]) == 9
    assert Artifact.model_validate({k: v for k, v in r.items() if k not in ("fields", "blanks")})

    r = client.post("/pyapi/artifact/art_fill/fill", json={
        "base_version": 1, "values": {"district": "Pune", "fathers name": "Ramesh Kumar", "nope": "x"}})
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 2 and body["filled"] == ["district", "fathers name"]
    assert body["unknown"] == ["nope"]
    assert [s["id"] for s in body["sections"]] == ["s1", "s2", "s3"]      # the whole draft
    assert "district" not in {f["key"] for f in body["fields"]} and body["blanks"] == 7
    assert "IN THE COURT OF SESSIONS AT PUNE" in body["sections"][0]["blocks"][0]["text"]

    vs = client.get("/pyapi/artifact/art_fill/versions").json()["versions"]
    assert vs[-1]["source"] == "user" and vs[-1]["change_summary"] == "Filled blanks: District, Father's Name"
    assert client.get("/pyapi/artifact/art_fill?version=1").json()["blanks"] == 12

    # the typed save is unchanged apart from carrying the fields
    sec = next(x for x in body["sections"] if x["id"] == "s3")
    sec["blocks"][-1]["text"] = "Date: 1 May 2026"
    typed = client.put("/pyapi/artifact/art_fill", json={"base_version": 2, "sections": [sec]}).json()
    assert set(typed) == {"artifact_id", "version", "sections", "fields", "blanks"}
    assert typed["version"] == 3 and "date" not in {f["key"] for f in typed["fields"]}
    assert client.put("/pyapi/artifact/art_fill", json={"base_version": 3}).status_code == 422

    restored = client.post("/pyapi/artifact/art_fill/restore", json={"version": 1}).json()
    assert restored["version"] == 4 and restored["blanks"] == 12

    # a draft object sent back whole still exports
    docx = client.post("/pyapi/export", json={"artifact": restored, "format": "docx"})
    assert docx.status_code == 200 and "wordprocessingml" in docx.headers["content-type"]

    stale = client.post("/pyapi/artifact/art_fill/fill", json={"base_version": 1, "values": {"age": "34"}})
    assert stale.status_code == 409 and stale.json()["latest_version"] == 4
    same = client.post("/pyapi/artifact/art_fill/fill", json={"base_version": 4, "values": {"nope": "x"}})
    assert same.json()["unchanged"] is True and same.json()["version"] == 4
    assert client.post("/pyapi/artifact/art_fill/fill", json={"base_version": 4, "values": {}}).status_code == 422
    assert client.post("/pyapi/artifact/art_none/fill",
                       json={"base_version": 1, "values": {"a": "b"}}).status_code == 404
    assert client.get("/pyapi/artifact/art_none").status_code == 404


def test_artifact_done_carries_the_fields_next_to_the_draft(client, monkeypatch):
    from core import artifact_followup as af
    monkeypatch.setenv("DRAFT_ARTIFACT_EVENTS", "1")
    ev = asyncio.run(af.record_draft("9b1c-thread", BAIL))
    assert ev["type"] == "artifact_done" and ev["blanks"] == 12 and len(ev["fields"]) == 9
    Artifact.model_validate(ev["artifact"])                     # the draft itself is unchanged
