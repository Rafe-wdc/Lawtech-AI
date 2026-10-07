"""Changing part of a draft: versions, typed edits, one-section edits, and
deciding whether a chat follow-up is a partial edit or a whole-draft request.

The model is a stand-in here so the suite runs offline. The one-section edit
was also run against the real model on a real bail application (2026-10-07):
the request was routed to the grounds section, one paragraph was added, every
other paragraph came back word for word, and 4 of 7 sections were untouched.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from core import artifact_edit as ae
from core.artifact_edit import (
    EditError, apply_instruction, apply_user_sections, changed_sections, edit_section,
    is_whole_draft_request, renumber, route_instruction,
)
from core.draft_blocks import Artifact, ParaBlock
from core.draft_to_blocks import (
    artifact_to_markdown, markdown_to_artifact, markdown_to_section, section_to_markdown,
)
from tests.test_draft_to_blocks import BAIL, NOTICE


def _art() -> Artifact:
    return markdown_to_artifact(BAIL, artifact_id="art_test")


def _numbers(a: Artifact) -> list[int]:
    return [b.number for s in a.sections for b in s.blocks
            if isinstance(b, ParaBlock) and not b.label]


class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeLLM:
    """Returns fn(prompt) as the model's text."""

    def __init__(self, fn):
        self.fn, self.calls = fn, 0

    def invoke(self, messages):
        self.calls += 1
        prompt = messages[0].content if isinstance(messages, list) else messages
        return _Resp(self.fn(prompt))


def _section_from_prompt(prompt: str) -> str:
    return prompt.split("<<<SECTION\n", 1)[1].split("\nSECTION>>>", 1)[0]


# --- text round trip ---------------------------------------------------------

@pytest.mark.parametrize("md", [BAIL, NOTICE])
def test_every_section_survives_a_round_trip_through_text(md):
    a = markdown_to_artifact(md)
    for i, sec in enumerate(a.sections):
        back = markdown_to_section(section_to_markdown(sec, cause_title=i == 0), sec,
                                   cause_title=i == 0)
        assert back.model_dump() == sec.model_dump(), sec.heading


def test_clean_markdown_has_no_internal_markers():
    md = artifact_to_markdown(_art())
    assert ">> " not in md and "[page break]" not in md and " || " not in md
    assert "Advocate for the Applicant" in md and "## STATEMENT OF FACTS" in md


# --- numbering -----------------------------------------------------------------

def test_renumber_leaves_a_correct_draft_alone():
    a = _art()
    before = _numbers(a)
    assert _numbers(renumber(a)) == before == [1, 2, 3]


def test_renumber_continues_across_sections_and_keeps_a_restart():
    md = ("## FACTS\n\n1. One.\n\n2. Two.\n\n## GROUNDS\n\n2. Dup.\n\n9. Gap.\n\n"
          "## AFFIDAVIT\n\n1. I say.\n\n2. I say again.\n")
    a = renumber(markdown_to_artifact(md, start_in_body=True))
    assert _numbers(a) == [1, 2, 3, 4, 1, 2]


# --- typed edits ---------------------------------------------------------------

def test_typed_edit_replaces_one_section_and_renumbers():
    a = _art()
    facts = next(s for s in a.sections if s.heading == "Statement Of Facts").model_dump()
    facts["blocks"].insert(2, {"type": "para", "text": "A paragraph typed by the advocate."})
    new = apply_user_sections(a, [facts])
    changed = changed_sections(a, new)
    assert [s.heading for s in changed] == ["Statement Of Facts", "Prayer"]   # prayer para renumbered
    assert _numbers(new) == [1, 2, 3, 4]
    assert all(b.id for s in new.sections for b in s.blocks)
    assert a.sections[0].model_dump() == new.sections[0].model_dump()         # cause title untouched


def test_typed_edit_rejects_unknown_sections_and_bad_blocks():
    a = _art()
    with pytest.raises(EditError):
        apply_user_sections(a, [{"id": "s99", "blocks": []}])
    with pytest.raises(EditError):
        apply_user_sections(a, [{"id": "s2", "blocks": [{"type": "marquee", "text": "x"}]}])
    with pytest.raises(EditError):
        apply_user_sections(a, [])


# --- one-section edit ----------------------------------------------------------

def test_edit_changes_only_the_asked_section():
    a = _art()
    sid = next(s.id for s in a.sections if s.heading == "Statement Of Facts")
    llm = _FakeLLM(lambda p: _section_from_prompt(p)
                   + "\n\n3. The Applicant has no criminal antecedents.")
    new, changed = asyncio.run(apply_instruction(a, [sid], "add a para on antecedents", llm=llm))
    assert llm.calls == 1
    edited = next(s for s in new.sections if s.id == sid)
    texts = [b.text for b in edited.blocks if isinstance(b, ParaBlock)]
    assert texts[-1] == "The Applicant has no criminal antecedents."
    assert _numbers(new) == [1, 2, 3, 4]                       # prayer moved from 3 to 4
    assert sid in [s.id for s in changed]
    untouched = [s for s in new.sections if s.id not in [c.id for c in changed]]
    old = {s.id: s.model_dump() for s in a.sections}
    assert untouched and all(s.model_dump() == old[s.id] for s in untouched)


def test_edit_prompt_carries_the_selection_and_only_this_section():
    a = _art()
    sid = next(s.id for s in a.sections if s.heading == "Prayer")
    seen = {}

    def fn(p):
        seen["p"] = p
        return _section_from_prompt(p)
    asyncio.run(edit_section(a, sid, "tighten this", selected_text="release the Applicant on bail",
                             llm=_FakeLLM(fn)))
    assert 'THE ADVOCATE SELECTED THIS PART' in seen["p"]
    assert "release the Applicant on bail" in seen["p"]
    body = _section_from_prompt(seen["p"])
    assert "## PRAYER" in body and "STATEMENT OF FACTS" not in body
    assert ">> Advocate for the Applicant" in body             # signature marker shown to the model


def test_a_gutted_or_empty_edit_is_refused():
    a = _art()
    sid = a.sections[1].id
    long = markdown_to_artifact("## FACTS\n\n" + "\n\n".join(
        f"{n}. " + "The Applicant states a material fact. " * 6 for n in range(1, 9)),
        start_in_body=True, artifact_id="x")
    with pytest.raises(EditError):
        asyncio.run(edit_section(long, long.sections[0].id, "improve it",
                                 llm=_FakeLLM(lambda p: "I have improved the section.")))
    with pytest.raises(EditError):
        asyncio.run(edit_section(a, sid, "improve it", llm=_FakeLLM(lambda p: "  ")))
    with pytest.raises(EditError):
        asyncio.run(edit_section(a, "s99", "x", llm=_FakeLLM(lambda p: "x")))


def test_code_fence_around_the_reply_is_removed():
    a = _art()
    sid = a.sections[1].id
    sec = asyncio.run(edit_section(a, sid, "no change",
                                   llm=_FakeLLM(lambda p: "```\n" + _section_from_prompt(p) + "\n```")))
    assert sec.model_dump() == a.sections[1].model_dump()


# --- whole draft or part of it? ---------------------------------------------------

@pytest.mark.parametrize("q", [
    "redraft the whole thing", "regenerate the draft", "please rewrite it from scratch",
    "translate this into Marathi", "give me this in Hindi", "start over",
    "convert it into a plaint", "make it a writ petition",
])
def test_explicit_whole_draft_requests(q):
    assert is_whole_draft_request(q)
    route = asyncio.run(route_instruction(_art(), q, llm=_FakeLLM(lambda p: 1 / 0)))
    assert route["scope"] == "whole"


@pytest.mark.parametrize("q", [
    "make ground B stronger", "add a prayer for interim relief",
    "the applicant's age is 36 not 34", "remove paragraph 2",
])
def test_partial_requests_are_not_caught_by_the_whole_draft_rule(q):
    assert not is_whole_draft_request(q)


@pytest.mark.parametrize("q", [
    "add relevant case laws", "cite Supreme Court judgments on parity",
    "add precedents in the grounds", "support ground B with a landmark ruling",
])
def test_requests_for_case_law_never_become_a_partial_edit(q):
    """A partial edit has no judgment search behind it; measured live, it
    wrote citations from the model's memory."""
    assert ae.needs_research(q)
    route = asyncio.run(route_instruction(_art(), q, llm=_FakeLLM(lambda p: 1 / 0)))
    assert route["scope"] == "whole"


@pytest.mark.parametrize("q", [
    "add that the revenue authorities failed to act", "make the prayer stronger",
    "mention the order dated 24.04.1986",
])
def test_ordinary_edits_are_not_mistaken_for_case_law_requests(q):
    assert not ae.needs_research(q)


def test_an_edit_that_invents_a_citation_is_refused():
    a = _art()
    sid = a.sections[1].id
    invent = _FakeLLM(lambda p: _section_from_prompt(p)
                      + "\n\n3. As held in *X v. State*, (2020) 11 SCC 648, bail is the rule.")
    with pytest.raises(EditError, match="citation"):
        asyncio.run(edit_section(a, sid, "strengthen the facts", llm=invent))
    # the advocate supplied the citation, so it may be written
    sec = asyncio.run(edit_section(
        a, sid, "add a para relying on X v. State, (2020) 11 SCC 648", llm=invent))
    assert any("(2020) 11 SCC 648" in getattr(b, "text", "") for b in sec.blocks)


class _RouteLLM:
    def __init__(self, scope, ids):
        self.scope, self.ids = scope, ids

    def invoke(self, prompt):
        return ae._Route(scope=self.scope, section_ids=self.ids, reason="r")


def test_routing_outcomes():
    a = _art()
    ok = asyncio.run(route_instruction(a, "fix the prayer", llm=_RouteLLM("sections", ["s3"])))
    assert ok["scope"] == "sections" and ok["section_ids"] == ["s3"]
    # ids the draft does not have are dropped; nothing left -> whole
    assert asyncio.run(route_instruction(a, "fix", llm=_RouteLLM("sections", ["s77"])))["scope"] == "whole"
    assert asyncio.run(route_instruction(a, "what is bail?", llm=_RouteLLM("none", [])))["scope"] == "none"
    # too many sections is a broad change
    big = markdown_to_artifact("\n\n".join(f"## PART {n}\n\n{n}. Text." for n in range(1, 8)),
                               start_in_body=True)
    many = [s.id for s in big.sections][:ae.MAX_SECTIONS_PER_EDIT + 1]
    assert asyncio.run(route_instruction(big, "fix", llm=_RouteLLM("sections", many)))["scope"] == "whole"

    class _Boom:
        def invoke(self, prompt):
            raise RuntimeError("down")
    assert asyncio.run(route_instruction(a, "fix the prayer", llm=_Boom()))["scope"] == "whole"


# --- versions --------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    from core import artifact_store as mod
    from core import chat_store as cs
    monkeypatch.setattr(cs, "chat_store", cs._SqliteChatHistoryStore(str(tmp_path / "t.db")))
    s = mod._Store()
    monkeypatch.setattr(mod, "artifact_store", s)
    return s, mod


def test_versions_are_append_only(store):
    s, mod = store
    a = _art()
    v1 = asyncio.run(s.save(a, thread_id="t1", source=mod.SOURCE_DRAFT, change_summary="Draft generated"))
    assert v1.version == 1
    edited = a.model_copy(deep=True)
    edited.sections[1].blocks[1].text = "Changed."
    v2 = asyncio.run(s.save(edited, thread_id="t1", source=mod.SOURCE_USER, base_version=1))
    assert v2.version == 2
    assert asyncio.run(s.get("art_test")).version == 2
    assert asyncio.run(s.get("art_test", 1)).sections[1].blocks[1].text != "Changed."
    assert [(v["version"], v["source"]) for v in asyncio.run(s.versions("art_test"))] == \
        [(1, "draft"), (2, "user")]
    assert asyncio.run(s.thread_of("art_test")) == "t1"
    assert asyncio.run(s.get("art_missing")) is None


def test_saving_on_an_old_version_is_refused(store):
    s, mod = store
    a = _art()
    asyncio.run(s.save(a, thread_id="t1", source=mod.SOURCE_DRAFT))
    asyncio.run(s.save(a, thread_id="t1", source=mod.SOURCE_USER, base_version=1))
    with pytest.raises(mod.StaleVersion) as e:
        asyncio.run(s.save(a, thread_id="t1", source=mod.SOURCE_USER, base_version=1))
    assert e.value.latest == 2


# --- chat follow-up, end to end ------------------------------------------------------

def test_followup_edits_part_of_the_draft_and_stores_a_version(store, monkeypatch):
    s, mod = store
    from core import artifact_followup as af
    monkeypatch.setattr(af, "artifact_store", s)
    monkeypatch.setenv("DRAFT_ARTIFACT_EVENTS", "1")
    thread = "4c583f65-3e59-4764-a69d-b77b7baca1fc"

    done = asyncio.run(af.record_draft(thread, BAIL, "en"))
    assert done["type"] == "artifact_done" and done["version"] == 1
    aid = done["artifact_id"]

    async def allow(q):
        return True

    async def route(artifact, q):
        return {"scope": "sections", "section_ids": ["s2"], "reason": "facts"}

    async def apply(artifact, ids, q, **kw):
        return await apply_instruction(artifact, ids, q, llm=_FakeLLM(
            lambda p: _section_from_prompt(p) + "\n\n3. A new fact."))
    monkeypatch.setattr(af, "_input_allowed", allow)
    monkeypatch.setattr(af, "route_instruction", route)
    monkeypatch.setattr(af, "apply_instruction", apply)

    out = asyncio.run(af.try_partial_edit(thread, "add a new fact"))
    patch = out["patch"]
    assert patch["type"] == "artifact_patch" and patch["base_version"] == 1 and patch["version"] == 2
    assert patch["edited_section_ids"] == ["s2"]
    assert "3. A new fact." in out["markdown"] and "IN THE COURT OF SESSIONS" in out["markdown"]
    assert asyncio.run(s.get(aid)).version == 2
    json.dumps(patch)                                           # serialisable for SSE

    async def whole(artifact, q):
        return {"scope": "whole", "section_ids": [], "reason": "redraft"}
    monkeypatch.setattr(af, "route_instruction", whole)
    assert asyncio.run(af.try_partial_edit(thread, "redraft it")) is None      # full pipeline runs
    assert asyncio.run(af.try_partial_edit("no-such-thread", "add a fact")) is None


def test_panel_endpoints(store, monkeypatch):
    """Load, save typed text, stale save, versions, restore, export by id,
    and a selected-section edit — as the panel calls them."""
    from fastapi.testclient import TestClient
    import core.gateway as gw
    s, mod = store
    a = asyncio.run(s.save(_art(), thread_id="t1", source=mod.SOURCE_DRAFT))
    aid = a.artifact_id
    gw.app.dependency_overrides[gw.require_user_key] = lambda: None
    try:
        c = TestClient(gw.app)
        assert c.get(f"/pyapi/artifact/{aid}").json()["version"] == 1
        assert c.get("/pyapi/artifact/art_nope").status_code == 404

        facts = next(x for x in a.model_dump()["sections"] if x["heading"] == "Statement Of Facts")
        facts["blocks"][1]["text"] = "The Applicant resides at Pune."
        r = c.put(f"/pyapi/artifact/{aid}", json={"base_version": 1, "sections": [facts]})
        assert r.status_code == 200 and r.json()["version"] == 2
        assert r.json()["sections"][0]["blocks"][1]["text"] == "The Applicant resides at Pune."

        stale = c.put(f"/pyapi/artifact/{aid}", json={"base_version": 1, "sections": [facts]})
        assert stale.status_code == 409 and stale.json()["latest_version"] == 2
        bad = c.put(f"/pyapi/artifact/{aid}", json={"base_version": 2,
                                                   "sections": [{"id": "s99", "blocks": []}]})
        assert bad.status_code == 400

        vs = c.get(f"/pyapi/artifact/{aid}/versions").json()["versions"]
        assert [(v["version"], v["source"]) for v in vs] == [(1, "draft"), (2, "user")]

        restored = c.post(f"/pyapi/artifact/{aid}/restore", json={"version": 1}).json()
        assert restored["version"] == 3
        assert restored["sections"][1]["blocks"][1]["text"] != "The Applicant resides at Pune."

        docx = c.post("/pyapi/export", json={"artifact_id": aid, "format": "docx"})
        assert docx.status_code == 200 and "wordprocessingml" in docx.headers["content-type"]

        async def fake_apply(artifact, ids, instruction, **kw):
            return await apply_instruction(artifact, ids, instruction, llm=_FakeLLM(
                lambda p: _section_from_prompt(p) + "\n\n3. One more fact."))
        monkeypatch.setattr(ae, "apply_instruction", fake_apply)
        ed = c.post("/pyapi/artifact/edit", json={
            "artifact_id": aid, "base_version": 3, "instruction": "add one more fact",
            "target": {"section_id": "s2", "selected_text": "arrested on 15 March 2026"}})
        events = [json.loads(l[6:]) for l in ed.text.splitlines() if l.startswith("data: ")]
        patch = next(e for e in events if e["type"] == "artifact_patch")
        assert patch["version"] == 4 and patch["edited_section_ids"] == ["s2"]
        assert events[-1]["type"] == "done"
        assert c.post("/pyapi/artifact/edit", json={
            "artifact_id": aid, "base_version": 1, "instruction": "x"}).status_code == 409
    finally:
        gw.app.dependency_overrides.pop(gw.require_user_key, None)


def test_followup_is_off_without_the_flag(store, monkeypatch):
    from core import artifact_followup as af
    monkeypatch.delenv("DRAFT_ARTIFACT_EVENTS", raising=False)
    assert asyncio.run(af.record_draft("t", BAIL)) is None
    assert asyncio.run(af.try_partial_edit("t", "add a fact")) is None
    assert asyncio.run(af.current_draft_markdown("t")) is None
