"""Details a draft adds that the client's account does not state are taken out.

Repro (2026-11-10/11): complaint and petition drafts added "illiterate",
"accompanied by their musclemen", "(wife of the Complainant)", "aged
parents wholly dependent on him", "no child was born". All names and facts
below are invented.
"""
import asyncio

import pytest

import core.added_facts as af
from core.added_facts import _Item, apply_corrections, build_source, check_added_facts


def _it(sentence, corrected, added="x"):
    return _Item(sentence=sentence, added=added, account_says="nothing", corrected=corrected)


# ---------------------------------------------------------------------------
# apply_corrections: word-level edits in code
# ---------------------------------------------------------------------------

def test_a_mid_sentence_detail_is_taken_out():
    d = ("1. The Complainant states that he is an illiterate, poor and rustic agriculturist "
         "residing at Village Rampur.\n\n2. Next paragraph.")
    new, landed = apply_corrections(d, [_it(
        "The Complainant states that he is an illiterate, poor and rustic agriculturist residing at Village Rampur.",
        "The Complainant states that he is an agriculturist residing at Village Rampur.")])
    assert landed
    assert new == ("1. The Complainant states that he is an agriculturist residing at Village Rampur."
                   "\n\n2. Next paragraph.")


def test_a_parenthetical_relationship_is_taken_out():
    d = "(l) Suit No. 67 of 2022, filed by Meena Joshi (wife of the Complainant) against Accused No. 1."
    new, _ = apply_corrections(d, [_it(d[4:], "Suit No. 67 of 2022, filed by Meena Joshi against Accused No. 1.")])
    assert new == "(l) Suit No. 67 of 2022, filed by Meena Joshi against Accused No. 1."


def test_bold_names_keep_their_markers_when_the_quote_has_none():
    d = "**Accused No. 1** and **Accused No. 2**, accompanied by their musclemen, came to the field."
    new, _ = apply_corrections(d, [_it(
        "Accused No. 1 and Accused No. 2, accompanied by their musclemen, came to the field.",
        "Accused No. 1 and Accused No. 2 came to the field.", added="accompanied by their musclemen")])
    assert new == "**Accused No. 1** and **Accused No. 2** came to the field."


def test_bold_wholly_inside_the_cut_goes_with_it():
    d = "The accused, accompanied by **their musclemen**, threatened him."
    new, _ = apply_corrections(d, [_it(d, "The accused threatened him.", added="accompanied by their musclemen")])
    assert new == "The accused threatened him."


def test_bold_opened_before_the_cut_and_closed_after_it_survives():
    d = "**The accused, accompanied by musclemen, threatened him.** Then he left."
    new, _ = apply_corrections(d, [_it("The accused, accompanied by musclemen, threatened him.",
                                       "The accused threatened him.", added="accompanied by musclemen")])
    assert new == "**The accused threatened him.** Then he left."


def test_a_cut_at_the_start_of_a_sentence_recapitalises():
    d = "He paid Rs. 5,000. Paralyzed by fear, the Complainant signed the deed."
    new, _ = apply_corrections(d, [_it("Paralyzed by fear, the Complainant signed the deed.",
                                       "The Complainant signed the deed.", added="Paralyzed by fear")])
    assert new == "He paid Rs. 5,000. The Complainant signed the deed."


def test_a_whole_sentence_inside_a_paragraph_is_deleted():
    d = "He signed the deed. The accused then took him to a farmhouse. He came home at night."
    new, _ = apply_corrections(d, [_it("The accused then took him to a farmhouse.", "", added="took him to a farmhouse")])
    assert new == "He signed the deed. He came home at night."


def test_a_whole_paragraph_is_deleted_without_leaving_a_gap():
    d = "Para one.\n\nThe accused hurled filthy abuses at the gate.\n\nPara three."
    new, _ = apply_corrections(d, [_it("The accused hurled filthy abuses at the gate.", "", added="hurled filthy abuses at the gate")])
    assert new == "Para one.\n\nPara three."


def test_an_emptied_numbered_paragraph_keeps_its_number_and_says_what_is_missing():
    d = ("2. On 14 March 2022 the accused took Rs. 6,50,000.\n\n"
         "3. The Complainant visited the residence of the accused on many occasions.\n\n"
         "4. The accused stopped answering calls.")
    new, _ = apply_corrections(d, [_it(
        "The Complainant visited the residence of the accused on many occasions.", "",
        added="The Complainant visited the residence of the accused on many occasions")])
    assert new == ("2. On 14 March 2022 the accused took Rs. 6,50,000.\n\n"
                   "3. [Details to be confirmed by the client]\n\n"
                   "4. The accused stopped answering calls.")


def test_cutting_a_paragraph_s_first_sentence_keeps_the_paragraph_number():
    d = ("21. The family depends on him.\n\n"
         "22. His father suffers from age-related infirmities. Owing to the arrest the family suffers.\n\n"
         "23. Next.")
    s = "22. His father suffers from age-related infirmities."
    new, landed = apply_corrections(d, [_it(s, "", added=s)])
    assert landed
    assert new == ("21. The family depends on him.\n\n"
                   "22. Owing to the arrest the family suffers.\n\n23. Next.")


def test_a_whole_sentence_goes_only_when_the_named_detail_is_most_of_it():
    d = "He signed. The accused, who had earlier lent him money at interest, came at night. He left."
    s = "The accused, who had earlier lent him money at interest, came at night."
    assert apply_corrections(d, [_it(s, "", added="at night")]) == (d, [])
    new, landed = apply_corrections(d, [_it(s, "", added="who had earlier lent him money at interest, came at night")])
    assert landed and new == "He signed. He left."


def test_an_amount_inside_a_cut_sentence_leaves_no_stray_dash():
    d = ("10. Immediately after receiving Rs. 6,50,000/- (Rupees Six Lakh Fifty Thousand only) "
         "on 14 March 2022, the Accused began avoiding the Complainant.\n\n11. Next.")
    s = d.split("\n\n")[0][4:]
    new, landed = apply_corrections(d, [_it(s, "[details of the Accused avoiding the Complainant]", added=s)])
    assert landed
    assert new == "10. [Details to be confirmed by the client]\n\n11. Next."


def test_a_detail_can_become_a_placeholder():
    d = "Religion: Hindu, Occupation: Business, residing at Rampur."
    new, _ = apply_corrections(d, [_it(d, "Religion: Hindu, Occupation: [Occupation], residing at Rampur.")])
    assert new == "Religion: Hindu, Occupation: [Occupation], residing at Rampur."


def test_a_trailing_clause_takes_its_comma_with_it():
    d = "He threatened him, sending goons to the village."
    new, _ = apply_corrections(d, [_it(d, "He threatened him.", added="sending goons to the village")])
    assert new == "He threatened him."


def test_a_cut_party_line_leaves_no_stray_comma():
    d = "Age: Adult,\n\nOccupation: Business / Agriculture,\n\nResiding at: Rampur."
    new, _ = apply_corrections(d, [_it(d, "Age: Adult,\n\nResiding at: Rampur.",
                                       added="Occupation: Business / Agriculture")])
    assert new == "Age: Adult,\n\nResiding at: Rampur."


def test_a_placeholder_holding_a_word_of_the_sentence_stays_whole():
    d = "He issued threats to the family."
    new, _ = apply_corrections(d, [_it(d, "He issued [details of the threats].",
                                       added="threats to the family")])
    assert new == "He issued [Details of the threats]."


def test_a_placeholder_already_in_the_sentence_is_kept_as_it_was():
    d = "The applicant, residing at [Address], owns a shop and two trucks."
    new, landed = apply_corrections(d, [_it(d, "The applicant, residing at [Address], owns a shop.",
                                            added="and two trucks")])
    assert landed and new == "The applicant, residing at [Address], owns a shop."


def test_a_correction_that_deletes_more_than_the_named_detail_is_refused():
    d = ("The deed was obtained through deceit, fraud and armed extortion. The accused "
         "brandished a firearm at point-blank range.")
    new, landed = apply_corrections(d, [_it(d, "The accused brandished a firearm.",
                                            added="at point-blank range")])
    assert (new, landed) == (d, [])


def test_a_cut_word_kept_in_brackets_is_dropped():
    d = "He severed contact from June 2022, absconded from his known whereabouts, and kept the money."
    new, landed = apply_corrections(d, [_it(
        d, "He severed contact from June 2022, [absconded], and kept the money.",
        added="absconded from his known whereabouts")])
    assert landed and new == "He severed contact from June 2022, and kept the money."


def test_a_claim_in_brackets_becomes_the_standard_placeholder():
    d = "Accused Nos. 1 to 3, with anti-social elements, arrived at the fields with firearms."
    new, landed = apply_corrections(d, [_it(
        d, "Accused Nos. 1 to 3 [unlawfully grabbed possession of the land].",
        added="with anti-social elements, arrived at the fields with firearms")])
    assert landed
    assert new == "Accused Nos. 1 to 3 [Details to be confirmed by the client]."


def test_a_paragraph_number_the_finder_put_in_front_of_a_mid_paragraph_sentence_is_ignored():
    d = "3. The accused claimed to own the flat. He also said it was free of all loans.\n\n4. Next."
    new, landed = apply_corrections(d, [_it("3. He also said it was free of all loans.",
                                            "3. [details of any other representation]",
                                            added="He also said it was free of all loans")])
    assert landed
    assert new == "3. The accused claimed to own the flat. [Details of any other representation]\n\n4. Next."


def test_a_cut_that_would_strand_a_preposition_is_not_made():
    d = "The Applicant is presently confined in Central Jail, Bhopal. He has no antecedents."
    new, landed = apply_corrections(d, [_it(
        "The Applicant is presently confined in Central Jail, Bhopal.",
        "The Applicant is presently confined in.", added="Central Jail, Bhopal")])
    assert (new, landed) == (d, [])          # and the note will not list it


def test_a_correction_that_brings_in_new_words_is_refused():
    d = "The accused threatened the Complainant at the bank."
    new, landed = apply_corrections(d, [_it(d, "The accused brutally threatened the Complainant.")])
    assert (new, landed) == (d, [])


def test_glue_words_from_the_sentence_may_move():
    d = "He lost his land, his savings and his health."
    new, landed = apply_corrections(d, [_it(d, "He lost his land and his savings.")])
    assert landed and new == "He lost his land and his savings."


def test_every_copy_of_a_repeated_sentence_is_corrected():
    s = "The applicant's aged parents depend on him."
    d = f"Facts. {s}\n\nGrounds. {s}"
    new, _ = apply_corrections(d, [_it(s, "The applicant's parents depend on him.")])
    assert new.count("aged") == 0 and new.count("parents depend on him") == 2


def test_where_a_cut_s_words_recur_each_place_becomes_an_item_in_its_own_sentence():
    d = ("7. His family, including his aged and ailing parents, depends on him.\n\n"
         "12. The arrest has left his aged and ailing parents without support.\n\n"
         "I, Ravi Kumar, aged adult, state on oath.")
    reps = af.repeat_items(d, [_it(
        "His family, including his aged and ailing parents, depends on him.",
        "His family, including his parents, depends on him.", added="aged and ailing")])
    assert [(r.sentence, r.added) for r in reps] == [
        ("The arrest has left his aged and ailing parents without support.", "aged and ailing")]
    new, landed = apply_corrections(d, reps)
    assert landed and "12. The arrest has left his parents without support." in new
    assert "aged adult" in new


def test_a_cut_naming_a_place_is_never_looked_for_elsewhere():
    d = ("I, Ravi Kumar, residing at Rampur, Taluka Sonpur, state on oath.\n\n"
         "2. Meena Kumar (Informant), residing at Rampur, Taluka Sonpur.")
    assert af.repeat_items(d, [_it(
        "2. Meena Kumar (Informant), residing at Rampur, Taluka Sonpur.",
        "2. Meena Kumar (Informant).", added="residing at Rampur, Taluka Sonpur")]) == []


def test_a_repeat_the_confirm_passes_support_stays(monkeypatch):
    """A recital's "and her family" is unsupported; the licence clause's is a term."""
    monkeypatch.setenv("ADDED_FACTS_CHECK", "on")
    d = ("1. The Licensee needs a home for herself and her immediate family members in Pune.\n\n"
         "2. The premises shall be used only as a residence for herself and her immediate family members.")
    first = _it("The Licensee needs a home for herself and her immediate family members in Pune.",
                "The Licensee needs a home for herself in Pune.", added="and her immediate family members")
    # find -> 1 item; confirm x2 -> unsupported; repeat confirm x2 -> pass b supports it.
    llm = _FakeLLM([first], confirms=[set(), set(), set(), {1}])
    res = _run(check_added_facts(d, "source", llm=llm))
    body = res.draft.split("---")[0]
    assert "a home for herself in Pune" in body
    assert "residence for herself and her immediate family members" in body
    assert llm.calls == ["_Found", "_Verdicts", "_Verdicts", "_Verdicts", "_Verdicts"]


def test_a_cut_list_entry_does_not_leave_a_comma_after_its_number():
    d = "3. Mohan Lal Verma (Brother of the Complainant), residing at Rampur.\n\n4. Next."
    new, landed = apply_corrections(d, [_it(
        "3. Mohan Lal Verma (Brother of the Complainant), residing at Rampur.",
        "3. residing at Rampur.", added="Mohan Lal Verma (Brother of the Complainant)")])
    assert landed
    assert new == "3. Residing at Rampur.\n\n4. Next."


def test_a_one_word_cut_is_looked_for_only_with_the_same_neighbours():
    d = ("He lives with his elderly mother. Later his elderly mother fell ill. "
         "The elderly witnesses saw it.")
    reps = af.repeat_items(d, [_it("He lives with his elderly mother.", "He lives with his mother.",
                                   added="elderly")])
    assert [r.sentence for r in reps] == ["Later his elderly mother fell ill."]


def test_an_unparsable_reply_is_retried_once(monkeypatch):
    class _Flaky(_FakeLLM):
        def with_structured_output(self, model, include_raw=False):
            inner = super().with_structured_output(model, include_raw)
            outer = self

            class _R:
                async def ainvoke(self, prompt):
                    outer.tries = getattr(outer, "tries", 0) + 1
                    if outer.tries == 1:
                        return {"raw": None, "parsed": None, "parsing_error": "Invalid json output"}
                    return await inner.ainvoke(prompt)
            return _R()
    monkeypatch.setenv("ADDED_FACTS_CHECK", "on")
    res = _run(check_added_facts(DRAFT, "source", llm=_Flaky(ITEMS[:1], confirms=[set(), set()])))
    assert res.status == "edited"


def test_two_details_in_one_sentence_are_both_taken_out():
    d = "In early 2016 the accused, with armed henchmen, came to the house."
    new, landed = apply_corrections(d, [
        _it(d, "The accused, with armed henchmen, came to the house."),
        _it(d, "In early 2016 the accused came to the house."),
    ])
    assert len(landed) == 2
    assert new == "The accused came to the house."


def test_a_quote_that_dropped_a_comma_still_matches():
    d = "The accused, on 3 March, visited the farm and abused him."
    new, _ = apply_corrections(d, [_it("The accused on 3 March visited the farm and abused him.",
                                       "The accused on 3 March visited the farm.")])
    assert new == "The accused, on 3 March, visited the farm."


def test_hindi_detail_is_taken_out():
    d = "प्रत्यर्थी एवं उसके परिजनों ने याचिकाकर्ता के साथ मारपीट की।"
    new, landed = apply_corrections(d, [_it(d, "प्रत्यर्थी ने याचिकाकर्ता के साथ मारपीट की।")])
    assert landed and new == "प्रत्यर्थी ने याचिकाकर्ता के साथ मारपीट की।"


def test_a_sentence_not_in_the_draft_changes_nothing():
    d = "The accused threatened him."
    assert apply_corrections(d, [_it("Something the draft never says at all here.", "")]) == (d, [])


def test_a_cut_across_a_table_cell_is_not_made():
    d = "| Name | Ravi Kumar, a habitual offender |\n| Age | 40 |"
    new, _ = apply_corrections(d, [_it("Name Ravi Kumar, a habitual offender Age",
                                       "Name Ravi Kumar Age")])
    assert new.count("|") == d.count("|")


def test_edits_that_would_cut_most_of_the_draft_are_not_applied():
    kept = "The Complainant paid the amount on the agreed date. " * 40          # ~2,000 chars
    cut = [f"On day {i} the accused abused him with filthy words in public." for i in range(40)]
    d = kept + " ".join(cut)                                               # ~2,400 chars cut (55%)
    new, landed = apply_corrections(d, [_it(s, "", added=s) for s in cut])
    assert (new, landed) == (d, [])
    third = kept + " ".join(cut[:20])                                      # ~1,200 chars cut (37%)
    new, landed = apply_corrections(third, [_it(s, "", added=s) for s in cut[:20]])
    assert len(landed) == 20 and new == kept.rstrip()
    small = kept + cut[0]
    new, landed = apply_corrections(small, [_it(cut[0], "", added=cut[0])])
    assert landed and new == kept.rstrip()


# ---------------------------------------------------------------------------
# The source: what the client and the user told us
# ---------------------------------------------------------------------------

class _Msg:
    def __init__(self, type_, content):
        self.type, self.content = type_, content


def test_source_holds_uploads_user_turns_summary_and_request_but_not_ai_turns():
    src = build_source(
        "[Uploaded document - fir.pdf]\nThe accused threatened me at the bank.",
        "draft a private complaint",
        [_Msg("human", "Previous summary:"), _Msg("ai", "Fresh chat started."),
         _Msg("human", "My wife is Meena."), _Msg("ai", "The complainant is illiterate.")],
        summary_text="User is drafting a complaint about a loan.")
    assert "threatened me at the bank" in src
    assert "My wife is Meena." in src
    assert "drafting a complaint about a loan" in src
    assert "draft a private complaint" in src
    assert "illiterate" not in src
    assert "Previous summary:" not in src and "Fresh chat started" not in src


# ---------------------------------------------------------------------------
# check_added_facts with a fake model
# ---------------------------------------------------------------------------

class _FakeLLM:
    """Answers the find call with `items` and each confirm call with
    `confirms[i]` (a set of supported item numbers), or raises `error`."""

    def __init__(self, items=(), confirms=(set(), set()), error=None):
        self.items, self.confirms, self.error = list(items), list(confirms), error
        self.calls = []

    def with_structured_output(self, model, include_raw=False):
        outer = self

        class _Runner:
            async def ainvoke(self, prompt):
                outer.calls.append(model.__name__)
                if outer.error:
                    raise outer.error
                if model is af._Found:
                    return {"raw": None, "parsed": af._Found(items=outer.items)}
                sup = outer.confirms.pop(0)
                n = len(outer.items)
                return {"raw": None, "parsed": af._Verdicts(verdicts=[
                    af._Verdict(n=i, evidence="", supported=i in sup) for i in range(1, n + 1)])}
        return _Runner()


DRAFT = ("1. The Complainant is an illiterate farmer of Rampur.\n\n"
         "2. On 3 March 2016 the accused took Rs. 2,00,000 at gunpoint.")
ITEMS = [
    _it("The Complainant is an illiterate farmer of Rampur.",
        "The Complainant is a farmer of Rampur.", added="illiterate"),
    _it("On 3 March 2016 the accused took Rs. 2,00,000 at gunpoint.",
        "On 3 March 2016 the accused took Rs. 2,00,000.", added="at gunpoint"),
]


def _run(coro):
    return asyncio.run(coro)


def test_a_detail_both_confirm_passes_reject_is_taken_out_and_listed(monkeypatch):
    monkeypatch.setenv("ADDED_FACTS_CHECK", "on")
    # Item 2 ("at gunpoint") is supported by the second confirm pass only.
    llm = _FakeLLM(ITEMS, confirms=[set(), {2}])
    res = _run(check_added_facts(DRAFT, "source", llm=llm))
    assert res.status == "edited"
    assert "illiterate" not in res.draft.split("---")[0]
    assert "at gunpoint" in res.draft                      # one pass supported it: kept
    assert res.removed == ["illiterate"]
    assert af.NOTE_MARKER in res.draft and '"illiterate"' in res.draft
    assert llm.calls == ["_Found", "_Verdicts", "_Verdicts"]


def test_a_long_draft_is_read_in_parts_at_paragraph_breaks():
    paras = [f"{i}. " + "The Complainant paid the amount on the agreed date. " * 30 for i in range(1, 9)]
    parts = af._parts("\n\n".join(paras))
    assert len(parts) > 1
    assert all(len(p) <= af.PART_CHARS or "\n\n" not in p for p in parts)
    assert "\n\n".join(parts) == "\n\n".join(paras)        # nothing lost or doubled


def test_every_part_is_searched_and_the_findings_are_pooled(monkeypatch):
    monkeypatch.setenv("ADDED_FACTS_CHECK", "on")
    long_draft = DRAFT + "\n\n" + "\n\n".join(
        f"{i}. " + "The Complainant paid the amount on the agreed date. " * 30 for i in range(3, 12))
    llm = _FakeLLM(ITEMS[:1], confirms=[set(), set()])
    res = _run(check_added_facts(long_draft, "source", llm=llm))
    n_parts = len(af._parts(long_draft))
    assert n_parts > 1 and llm.calls.count("_Found") == n_parts
    assert res.status == "edited" and "illiterate" not in res.draft.split("---")[0]


def test_nothing_unsupported_leaves_the_draft_alone(monkeypatch):
    monkeypatch.setenv("ADDED_FACTS_CHECK", "on")
    res = _run(check_added_facts(DRAFT, "source", llm=_FakeLLM(ITEMS, confirms=[{1, 2}, {1, 2}])))
    assert (res.status, res.draft) == ("clean", DRAFT)


def test_no_items_found_skips_the_confirm_calls(monkeypatch):
    monkeypatch.setenv("ADDED_FACTS_CHECK", "on")
    llm = _FakeLLM([])
    res = _run(check_added_facts(DRAFT, "source", llm=llm))
    assert (res.status, res.draft, llm.calls) == ("clean", DRAFT, ["_Found"])


def test_a_failed_call_fails_closed(monkeypatch):
    monkeypatch.setenv("ADDED_FACTS_CHECK", "on")
    from core.audit_status import get_audit_status, reset_audit_status
    from core.self_refine import get_verifier_stats
    before = get_verifier_stats().get("total", 0)

    async def go():                  # the audit flag is request-scoped (a ContextVar)
        reset_audit_status()
        r = await check_added_facts(DRAFT, "source", llm=_FakeLLM(error=RuntimeError("503")))
        return r, get_audit_status()
    res, status = _run(go())
    assert (res.status, res.draft, status) == ("unverified", DRAFT, "unverified")
    assert get_verifier_stats().get("total", 0) == before + 1


def test_a_timeout_fails_closed(monkeypatch):
    monkeypatch.setenv("ADDED_FACTS_CHECK", "on")

    class _Slow(_FakeLLM):
        def with_structured_output(self, model, include_raw=False):
            class _R:
                async def ainvoke(self, prompt):
                    await asyncio.sleep(5)
            return _R()
    res = _run(check_added_facts(DRAFT, "source", llm=_Slow(), local_timeout=0.05))
    assert (res.status, res.draft) == ("unverified", DRAFT)


def test_shadow_mode_finds_but_changes_nothing(monkeypatch):
    monkeypatch.setenv("ADDED_FACTS_CHECK", "shadow")
    res = _run(check_added_facts(DRAFT, "source", llm=_FakeLLM(ITEMS)))
    assert (res.status, res.draft, res.confirmed) == ("shadow", DRAFT, 2)


def test_off_mode_makes_no_call(monkeypatch):
    monkeypatch.setenv("ADDED_FACTS_CHECK", "off")
    llm = _FakeLLM(ITEMS)
    res = _run(check_added_facts(DRAFT, "source", llm=llm))
    assert (res.draft, llm.calls) == (DRAFT, [])


def test_a_huge_source_is_skipped(monkeypatch):
    monkeypatch.setenv("ADDED_FACTS_CHECK", "on")
    llm = _FakeLLM(ITEMS)
    res = _run(check_added_facts(DRAFT, "x" * (af.MAX_SOURCE_CHARS + 1), llm=llm))
    assert (res.status, llm.calls) == ("skipped", [])


def test_the_prompts_format_with_their_variables():
    find = af.FIND_PROMPT.format(source="SRC", draft="DRF")
    confirm = af.CONFIRM_PROMPT.format(source="SRC", items="1. x")
    assert "SRC" in find and "DRF" in find and "SRC" in confirm and "1. x" in confirm
    assert "{" not in find and "{" not in confirm


# ---------------------------------------------------------------------------
# Drafting wiring
# ---------------------------------------------------------------------------

def test_drafting_runs_the_check_after_self_refine_and_keeps_the_review_note_last(monkeypatch):
    import agents.drafting as d
    seen = {}

    async def fake_check(body, source, **kw):
        seen["body"], seen["source"] = body, source
        return af.CheckResult(draft=body.replace(" illiterate", "") + af.removal_note(["illiterate"]),
                              removed=["illiterate"], status="edited")
    monkeypatch.setattr(af, "check_added_facts", fake_check)
    monkeypatch.setenv("ADDED_FACTS_CHECK", "on")
    banner = f"\n\n---\n\n> ⚠ {d._UNRESOLVED_REVIEW_BANNER_MARKER} — our review found 1 point(s)\n"
    draft = "The Complainant is an illiterate farmer." + banner
    out = _run(d._take_out_added_facts(draft, "[Uploaded document - a.pdf]\nfarmer",
                                       "draft a complaint", {"chat_history": []}))
    assert seen["body"] == "The Complainant is an illiterate farmer."
    assert "farmer" in seen["source"] and "draft a complaint" in seen["source"]
    assert out.endswith(banner)
    assert af.NOTE_MARKER in out and out.index(af.NOTE_MARKER) < out.index(d._UNRESOLVED_REVIEW_BANNER_MARKER)


def test_drafting_wiring_never_raises(monkeypatch):
    import agents.drafting as d

    async def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(af, "check_added_facts", boom)
    monkeypatch.setenv("ADDED_FACTS_CHECK", "on")
    assert _run(d._take_out_added_facts("A draft.", "", "q", {})) == "A draft."


def test_drafting_node_calls_the_check_after_self_refine():
    import inspect
    import agents.drafting as d
    src = inspect.getsource(d.drafting_node)
    assert src.index("await self_refine(") < src.index("_take_out_added_facts(") < src.index("Build AgentResult")


# --- Draft panel (core/draft_to_blocks): the panel turns the whole answer
# into the document, so what this check writes must read well there too.

def test_note_has_no_quote_markers_in_the_panel_or_word():
    from core.draft_to_blocks import markdown_to_artifact
    from core.draft_blocks import to_markdown
    note = af.removal_note(["illiterate", "accompanied by their musclemen"])
    assert "> " not in note and af.NOTE_MARKER in note
    body = "5. The Accused threatened the Complainant.\n\nPlace: Rampur\n\nAdvocate for the Complainant"
    text = to_markdown(markdown_to_artifact(body + note, artifact_id="t", language="en"))
    assert "Details taken out" in text and ">" not in text


def test_placeholders_are_offered_as_blanks_in_the_panel():
    from core.artifact_followup import fields_payload
    from core.draft_to_blocks import markdown_to_artifact
    d = ("1. The Complainant is a resident of Rampur.\n\n"
         "2. He was illiterate and could not read the agreement.\n\n"
         "3. He issued threats to the family.\n\n4. Next.")
    new, _ = apply_corrections(d, [
        _it("2. He was illiterate and could not read the agreement.", "", added="He was illiterate and could not read the agreement"),
        _it("He issued threats to the family.", "He issued [details of the threats].", added="threats to the family"),
    ])
    assert "[Details to be confirmed by the client]" in new and "[Details of the threats]" in new
    fields = fields_payload(markdown_to_artifact(new, artifact_id="t", language="en")).get("fields") or []
    labels = {f.get("label") for f in fields}
    assert {"Details to be confirmed by the client", "Details of the threats"} <= labels
