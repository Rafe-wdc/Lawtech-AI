"""core.statute_retrieval: the sections a draft turns on, from every Act it names.

Measured 2026-11-24 on dev: drafting's whole-instruction legislation query
returned IR Code ss.49/54/48/60/63 for a recovery application (both live drafts
cited ss.49 and 54); "Section 59 of the Industrial Relations Code, 2020"
returned ss.102/39/1; "Section 33C of the Industrial Disputes Act, 1947"
returned the Kerala Casual, Temporary and Badli Workers (Wages) Act.
ES and the picker model are stubbed here; tests/… live checks are in the
branch's evaluation notes.
"""
import asyncio
import sys

import pytest

sys.path.insert(0, ".")
from core import statute_retrieval as sr  # noqa: E402

CENTRAL = "drive/MyDrive/LawTech-Data/Central and State Legislations/CENTRAL/Central_Acts_renamed_csv_column/"


class TestActResolutionScore:
    @pytest.mark.parametrize("asked, source, score", [
        ("Industrial Disputes Act, 1947", CENTRAL + "Industrial Disputes Act, 1947.csv", 100),
        ("industrial dispute act", "Industrial Disputes Act, 1947.csv", 100),
        ("Industrial Relations Code", "Industrial Relations Code, 2020_1.csv", 100),
        ("Code on Wages, 2019", CENTRAL + "Code on Wages Act 2019.csv", 100),
        ("Gratuity Act", "Payment of Gratuity Act, 1972.csv", 60),
        # Not the Act asked for:
        ("Industrial Disputes Act, 1947", "The UNITED PROVINCES INDUSTRIAL DISPUTES ACT, 1947.csv", 0),
        ("Industrial Disputes Act, 1947", "Industrial Disputes (Punjab) Rules, 1958_1.csv", 0),
        ("Industrial Disputes Act, 1947", "Industrial Disputes (Rajasthan Amendment)Act, 1970.csv", 0),
        ("Industrial Relations Code, 2020", "Bombay Industrial Relations Act, 1946.csv", 0),
        ("Industrial Disputes Act, 1947", "Kerala Casual, Temporary and Badli Workers(Wages)Act, 1989.csv", 0),
    ])
    def test_score(self, asked, source, score):
        assert sr._match_score(asked, source) == score

    def test_display_name_strips_path_and_copy_suffix(self):
        assert sr.act_display_name(CENTRAL + "Industrial Disputes Act, 1947.csv") == "Industrial Disputes Act, 1947"
        assert sr.act_display_name("Industrial Relations Code, 2020_1.csv") == "Industrial Relations Code, 2020"


class TestDisplayText:
    def test_code_labels_become_section_labels(self):
        raw = ("code number: Code 59 of the Industrial Relations Code, 2020 code name: Recovery of "
               "money due from employer code provisions: (1) Where any money is due")
        out = sr._display_text(raw)
        assert out.startswith("section number: Section 59 of the Industrial Relations Code, 2020")
        assert "section name: Recovery of money due from employer section provisions: (1)" in out

    def test_section_rows_unchanged(self):
        raw = "section number: Section 33C of the Industrial Disputes Act, 1947 section name: X"
        assert sr._display_text(raw) == raw


class TestExplicitRefs:
    def test_section_and_act_parsed(self):
        act, nums = sr._explicit_refs(
            "Draft an application under Section 33C(2) of the Industrial Disputes Act, 1947")
        assert "industrial disputes act" in (act or "")
        assert [n.lower() for n in nums] == ["33c"]

    def test_code_year_is_not_a_section(self):
        # "Code 2020" must not be read as Section 2020 of anything.
        act, nums = sr._explicit_refs("draft a claim for wages under the Industrial Relations Code 2020")
        assert nums == []


class TestMode:
    def test_off_by_default(self, monkeypatch):
        # Off unless the server sets it, so merging dev into main changes
        # nothing on production.
        monkeypatch.delenv("DRAFTING_STATUTE_RETRIEVAL", raising=False)
        assert sr.mode() == "off"
        assert not sr.applies_to(["Industrial Disputes Act, 1947"])
        monkeypatch.setenv("DRAFTING_STATUTE_RETRIEVAL", "nonsense")
        assert sr.mode() == "off"

    def test_off_by_default_touches_nothing(self, monkeypatch):
        monkeypatch.delenv("DRAFTING_STATUTE_RETRIEVAL", raising=False)
        monkeypatch.setattr(sr, "_es", lambda: pytest.fail("no ES call expected"))

        async def infer(q):
            pytest.fail("no inference when off")
        monkeypatch.setattr(sr, "infer_labour_statutes", infer)
        assert asyncio.run(sr.gather_statutes("Draft a recovery application", ["Industrial Disputes Act, 1947"])) is None
        assert asyncio.run(sr.gather_statutes("Draft a claim for unpaid gratuity", [])) is None

    def test_labour_only_when_set(self, monkeypatch):
        monkeypatch.setenv("DRAFTING_STATUTE_RETRIEVAL", "labour")
        assert sr.applies_to(["Industrial Disputes Act, 1947"])
        assert sr.applies_to(["Code on Wages, 2019"])
        assert not sr.applies_to(["Negotiable Instruments Act, 1881"])
        assert not sr.applies_to([])

    def test_all_and_off(self, monkeypatch):
        monkeypatch.setenv("DRAFTING_STATUTE_RETRIEVAL", "all")
        assert sr.applies_to(["Negotiable Instruments Act, 1881"])
        monkeypatch.setenv("DRAFTING_STATUTE_RETRIEVAL", "off")
        assert not sr.applies_to(["Industrial Disputes Act, 1947"])

    def test_non_labour_request_touches_nothing(self, monkeypatch):
        monkeypatch.setenv("DRAFTING_STATUTE_RETRIEVAL", "labour")
        monkeypatch.setattr(sr, "_es", lambda: pytest.fail("no ES call expected"))
        assert asyncio.run(sr.gather_statutes("Draft a 138 NI Act complaint",
                                              ["Negotiable Instruments Act, 1881"])) is None

    def test_labour_acts_in_old_result(self):
        res = {"hits": [{"source": CENTRAL + "Payment of Gratuity Act, 1972.csv"},
                        {"source": "Negotiable Instruments Act, 1881.csv"},
                        {"source": "Payment of Gratuity Act, 1972.csv"}]}
        assert sr.labour_acts_in(res) == ["Payment of Gratuity Act, 1972"]
        assert sr.labour_acts_in(None) == []


def _hit(act, n, heading, chars=100):
    return {"content": f"section number: Section {n} of the {act} section name: {heading} "
                       "section provisions: " + "x" * chars,
            "source": act + ".csv", "score": 1.0, "act": act, "section": n, "heading": heading}


class TestGather:
    def _stub(self, monkeypatch, picks):
        acts = {
            "Industrial Disputes Act, 1947": sr.ResolvedAct("Industrial Disputes Act, 1947",
                                                            "Industrial Disputes Act, 1947.csv"),
            "Industrial Relations Code, 2020": sr.ResolvedAct("Industrial Relations Code, 2020",
                                                              "Industrial Relations Code, 2020.csv"),
        }
        monkeypatch.setattr(sr, "resolve_act", lambda name: next(
            (v for k, v in acts.items() if sr.ss.same_statute(k, name)), None))
        heads = {"Industrial Disputes Act, 1947.csv": "Recovery of money due from an employer",
                 "Industrial Relations Code, 2020.csv": "Recovery of money due from employer"}
        monkeypatch.setattr(sr, "fetch_section", lambda act, n: _hit(
            sr.act_display_name(act.source), n, heads[act.source]))
        seen = {}

        async def fake_pick(query, acts_, note, already, limit):
            seen.update(acts=[a.source for a in acts_], already=list(already), note=note, limit=limit)
            return picks(acts_)
        monkeypatch.setattr(sr, "pick_sections", fake_pick)
        return seen

    def test_repealed_act_brings_its_code_and_explicit_section_comes_first(self, monkeypatch):
        monkeypatch.setenv("DRAFTING_STATUTE_RETRIEVAL", "labour")
        seen = self._stub(monkeypatch, lambda acts: [(1, "59")])
        ctx = asyncio.run(sr.gather_statutes(
            "Draft an application under Section 33C(2) of the Industrial Disputes Act, 1947",
            ["Industrial Disputes Act, 1947"]))
        assert seen["acts"] == ["Industrial Disputes Act, 1947.csv", "Industrial Relations Code, 2020.csv"]
        assert ctx.acts[1].added_as_successor_of == "Industrial Disputes Act, 1947"
        assert [(h["act"], h["section"].lower()) for h in ctx.hits] == [
            ("Industrial Disputes Act, 1947", "33c"), ("Industrial Relations Code, 2020", "59")]
        assert "REPEALED" in ctx.note and "REPEALED" in seen["note"]

    def test_picker_failure_keeps_explicit_sections(self, monkeypatch):
        monkeypatch.setenv("DRAFTING_STATUTE_RETRIEVAL", "labour")

        def boom(acts):
            raise RuntimeError("picker down")
        self._stub(monkeypatch, boom)
        ctx = asyncio.run(sr.gather_statutes(
            "Draft an application under Section 33C(2) of the Industrial Disputes Act, 1947",
            ["Industrial Disputes Act, 1947"]))
        assert [h["section"].lower() for h in ctx.hits] == ["33c"]

    def test_unknown_act_returns_none(self, monkeypatch):
        monkeypatch.setenv("DRAFTING_STATUTE_RETRIEVAL", "labour")
        monkeypatch.setattr(sr, "resolve_act", lambda name: None)
        assert asyncio.run(sr.gather_statutes("Draft a petition", ["Payment of Wages Act, 1936"])) is None

    def test_no_act_named_infers_the_labour_statute(self, monkeypatch):
        # "a claim for unpaid gratuity" names no Act; the old retrieval returned
        # an unrelated pension regulation.
        monkeypatch.setenv("DRAFTING_STATUTE_RETRIEVAL", "labour")
        seen = self._stub(monkeypatch, lambda acts: [(1, "53")])
        acts = {"Payment of Gratuity Act, 1972": sr.ResolvedAct("x", "Payment of Gratuity Act 1972.csv"),
                "Code on Social Security, 2020": sr.ResolvedAct("y", "Code on Social Security 2020.csv")}
        monkeypatch.setattr(sr, "resolve_act", lambda name: next(
            (v for k, v in acts.items() if sr.ss.same_statute(k, name)), None))
        monkeypatch.setattr(sr, "fetch_section", lambda act, n: _hit(sr.act_display_name(act.source), n, "Gratuity"))

        async def infer(q):
            return ["Payment of Gratuity Act, 1972"]
        monkeypatch.setattr(sr, "infer_labour_statutes", infer)
        ctx = asyncio.run(sr.gather_statutes("Draft a claim for unpaid gratuity of a retired employee", []))
        assert seen["acts"] == ["Payment of Gratuity Act 1972.csv", "Code on Social Security 2020.csv"]
        assert [h["act"] for h in ctx.hits] == ["Code on Social Security 2020"]
        assert "Payment of Gratuity Act, 1972 named in this matter is REPEALED" in ctx.note

    def test_no_act_named_and_not_labour_touches_nothing(self, monkeypatch):
        monkeypatch.setenv("DRAFTING_STATUTE_RETRIEVAL", "labour")
        monkeypatch.setattr(sr, "_es", lambda: pytest.fail("no ES call expected"))

        async def infer(q):
            return []
        monkeypatch.setattr(sr, "infer_labour_statutes", infer)
        assert asyncio.run(sr.gather_statutes("Draft a rent agreement", [])) is None

    def test_off_skips_inference(self, monkeypatch):
        monkeypatch.setenv("DRAFTING_STATUTE_RETRIEVAL", "off")

        async def infer(q):
            pytest.fail("no inference when off")
        monkeypatch.setattr(sr, "infer_labour_statutes", infer)
        assert asyncio.run(sr.gather_statutes("Draft a claim for unpaid gratuity", [])) is None


class TestValidPicks:
    class P:
        def __init__(self, key, section):
            self.key, self.section = key, section

    def test_case_blind_dedupe_against_explicit_sections(self):
        valid = {"A1": (0, {"33C", "33A"}), "A2": (1, {"59", "104"})}
        picks = [self.P("A1", "33C"), self.P("a2", "59"), self.P("A2", "59"), self.P("A2", "999"),
                 self.P("A9", "1"), self.P("A2", "104")]
        assert sr._valid_picks(picks, valid, already=[(0, "33c")], limit=5) == [(1, "59"), (1, "104")]

    def test_limit(self):
        valid = {"A1": (0, {"1", "2", "3"})}
        assert sr._valid_picks([self.P("A1", n) for n in "123"], valid, [], limit=2) == [(0, "1"), (0, "2")]


class TestFormatBlock:
    def test_note_first_then_full_sections_with_caps(self):
        ctx = sr.StatuteContext(
            hits=[_hit("Industrial Relations Code, 2020", "59", "Recovery of money due from employer", 9000),
                  _hit("Industrial Relations Code, 2020", "2", "Definitions", 21000)],
            note="## LAW IN FORCE — x\n", acts=[])
        block = sr.format_block(ctx)
        assert block.startswith("## LAW IN FORCE")
        assert "`Industrial Relations Code, 2020` — Section 59: Recovery of money due from employer" in block
        s59, s2 = block.split("— Section 2:")
        assert "[section continues]" in s59           # 9,000 > 6,000
        assert "[section continues]" not in s2        # definitions keep 22,000

    def test_empty(self):
        assert sr.format_block(sr.StatuteContext(hits=[], note="", acts=[])) == ""


class TestWriterPrompts:
    """The switch also picks the writer prompts; off = the previous prompts byte for byte."""

    def test_originals_unchanged_and_variants_add_the_rules(self):
        import config.prompts as p
        for base, variant in ((p.DRAFTING_SYSTEM_PROMPT, p.DRAFTING_SYSTEM_PROMPT_LABOUR_CODES),
                              (p.DRAFTING_SECTION_PAIR_PROMPT, p.DRAFTING_SECTION_PAIR_PROMPT_LABOUR_CODES)):
            assert "LABOUR CODES ARE IN FORCE" not in base and "NO FACTS GIVEN" not in base
            assert "LABOUR CODES ARE IN FORCE" in variant and "NO FACTS GIVEN" in variant
        assert len(p.DRAFTING_SYSTEM_PROMPT_LABOUR_CODES) == len(p.DRAFTING_SYSTEM_PROMPT) + 1254 + 773
        assert len(p.DRAFTING_SECTION_PAIR_PROMPT_LABOUR_CODES) == len(p.DRAFTING_SECTION_PAIR_PROMPT) + 746 + 1172

    def test_both_writers_choose_by_the_switch(self):
        import inspect
        import agents.drafting as d
        src = inspect.getsource(d)
        assert "DRAFTING_SYSTEM_PROMPT_LABOUR_CODES if _statute_mode() != \"off\"" in src
        assert "DRAFTING_SECTION_PAIR_PROMPT_LABOUR_CODES if _statute_mode() != \"off\"" in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
