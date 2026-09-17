"""No section of a draft is ever delivered twice.

Reported 2026-09-09 by an advocate on a s.239 CrPC discharge application:
the delivered draft carried MOST RESPECTFULLY SHOWETH, PRAYER and
VERIFICATION twice and a second BRIEF FACTS heading over a prayer body. The
pair writer re-emits earlier sections despite rule 1 of the pair prompt, and
the re-emitted copies are paraphrased, so exact-block dedupe misses them.

Two guards, both pure functions (no LLM, no network):

  * agents.drafting._drop_reemitted_sections  - at the source, per pair
  * agents.drafting._collapse_repeated_sections - at delivery, in validate_draft
"""
from __future__ import annotations

from agents.drafting import (
    _collapse_repeated_sections,
    _drop_reemitted_sections,
    validate_draft,
)

INTRO = (
    "**MOST RESPECTFULLY SHOWETH:**\n\n"
    "1. That the present application is filed on behalf of the Applicant, "
    "Sh. Ashish Chopra, seeking discharge in FIR No. 387/2022, P.S. Preet Vihar.\n\n"
    "2. That the charge-sheet has been filed and is pending before this Hon'ble Court.\n\n"
)
FACTS = (
    "## BRIEF FACTS OF THE CASE (PROSECUTION VERSION)\n\n"
    "3. That on 03.11.2022 a raiding party was constituted by Inspector Arvind Kumar "
    "of Special Staff, East District, on the basis of secret information.\n\n"
)
GROUNDS = (
    "## GROUNDS FOR DISCHARGE\n\n"
    "a. No prima facie case is made out against the Applicant.\n\n"
    "b. The Applicant was admittedly not present at the premises on 03.11.2022.\n\n"
)
PRAYER = (
    "## PRAYER\n\n"
    "It is most respectfully prayed that this Hon'ble Court may be pleased to:\n\n"
    "a) discharge the Applicant from FIR No. 387/2022;\n\n"
    "b) pass any other order deemed fit in the interest of justice.\n\n"
)
PRAYER_AGAIN = (
    "## PRAYER\n\n"
    "In view of the aforesaid, it is prayed that the Applicant be discharged "
    "and such further orders be passed as this Hon'ble Court deems just.\n\n"
)
VERIF = (
    "## VERIFICATION\n\n"
    "I, Ashish Chopra, the Applicant above-named, verify that the contents of "
    "this application are true to my knowledge.\n\n"
    "Verified at Delhi on 09 September 2026.\n\n"
    "**(ASHISH CHOPRA)**\n\nApplicant/Accused\n\n"
)
VERIF_AGAIN = (
    "## VERIFICATION\n\n"
    "I, Ashish Chopra, do hereby solemnly affirm that the contents hereof are "
    "true and correct and nothing material has been concealed.\n\n"
    "**DEPONENT**\n\n"
)
AFFIDAVIT = (
    "## AFFIDAVIT IN SUPPORT OF THE APPLICATION\n\n"
    "**IN THE COURT OF THE METROPOLITAN MAGISTRATE, KARKARDOOMA COURTS, DELHI**\n\n"
    "I, Ashish Chopra, do hereby solemnly affirm and state as under:\n\n"
    "1. That I am the Applicant and am well conversant with the facts.\n\n"
    "## VERIFICATION\n\n"
    "Verified that the contents of the above affidavit are true.\n\n"
    "**DEPONENT**\n\n"
)


def _headings(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines()
            if l.startswith("##") or (l.startswith("**") and l.rstrip().endswith((":**", "**")) and len(l.split()) >= 2)]


# ------------------------------------------------- guard 1: per pair, at source
class TestDropReemittedSections:
    def test_pair_that_reemits_intro_and_prayer_keeps_only_its_own_sections(self):
        already = ["most respectfully showeth", "brief facts of the case (prosecution version)"]
        pair_out = INTRO + GROUNDS + PRAYER
        cleaned, dropped = _drop_reemitted_sections(
            pair_out, planned=["GROUNDS FOR DISCHARGE", "PRAYER"], already=already)
        assert dropped == ["most respectfully showeth"]
        assert "## GROUNDS FOR DISCHARGE" in cleaned and "## PRAYER" in cleaned
        assert "MOST RESPECTFULLY SHOWETH" not in cleaned

    def test_planned_section_is_never_dropped_even_if_a_similar_heading_exists(self):
        # The pair was asked for PRAYER; an earlier "PRAYER" must not block it.
        cleaned, dropped = _drop_reemitted_sections(
            PRAYER, planned=["PRAYER"], already=["prayer"])
        assert dropped == [] and "## PRAYER" in cleaned

    def test_nothing_already_present_means_nothing_dropped(self):
        cleaned, dropped = _drop_reemitted_sections(INTRO + FACTS, planned=["x"], already=[])
        assert dropped == [] and cleaned.strip() == (INTRO + FACTS).strip()

    def test_headless_leading_text_is_kept(self):
        text = "continuing paragraph 7 of the facts.\n\n" + GROUNDS
        cleaned, dropped = _drop_reemitted_sections(
            text, planned=["GROUNDS FOR DISCHARGE"], already=["brief facts"])
        assert cleaned.startswith("continuing paragraph 7")


# ---------------------------------------------- guard 2: at delivery, validator
class TestCollapseRepeatedSections:
    def test_lawyer_reported_shape_is_collapsed_to_one_of_each(self):
        draft = (INTRO + INTRO + FACTS + GROUNDS + PRAYER + VERIF
                 + "## BRIEF FACTS OF THE CASE (PROSECUTION VERSION)\n\n" + PRAYER_AGAIN[len("## PRAYER\n\n"):]
                 + VERIF_AGAIN)
        out, dropped = _collapse_repeated_sections(draft)
        heads = [h.lower() for h in _headings(out)]
        assert heads.count("## prayer") == 1
        assert heads.count("## verification") == 1
        assert heads.count("**most respectfully showeth:**") == 1
        assert heads.count("## brief facts of the case (prosecution version)") == 1
        assert len(dropped) == 3
        # order of the surviving sections is the original order
        assert out.index("SHOWETH") < out.index("BRIEF FACTS") < out.index("GROUNDS") \
            < out.index("## PRAYER") < out.index("## VERIFICATION")

    def test_paraphrased_second_prayer_is_dropped(self):
        out, dropped = _collapse_repeated_sections(GROUNDS + PRAYER + VERIF + PRAYER_AGAIN)
        assert dropped == ["prayer"]
        assert "In view of the aforesaid, it is prayed" not in out
        assert "discharge the Applicant from FIR" in out

    def test_supporting_affidavit_keeps_its_caption_and_verification(self):
        out, dropped = _collapse_repeated_sections(GROUNDS + PRAYER + VERIF + AFFIDAVIT)
        assert dropped == []
        assert out.count("## VERIFICATION") == 2
        assert "IN THE COURT OF THE METROPOLITAN MAGISTRATE" in out

    def test_single_word_bold_lines_are_not_sections(self):
        # **vs**, **DEPONENT**, **Petitioner** may legitimately recur.
        text = ("**vs**\n\nState of NCT of Delhi\n\n**vs**\n\nAshish Chopra\n\n"
                "**DEPONENT**\n\nsigned\n\n**DEPONENT**\n\nsigned again\n\n")
        out, dropped = _collapse_repeated_sections(text)
        assert dropped == [] and out == text

    def test_distinct_sections_with_shared_words_survive(self):
        text = ("## GROUNDS FOR BAIL\n\nbody one\n\n"
                "## GROUNDS FOR CANCELLATION OF BAIL\n\nbody two\n\n"
                "## PARITY WITH CO-ACCUSED\n\nbody three\n\n")
        out, dropped = _collapse_repeated_sections(text)
        assert dropped == [] and out == text

    def test_containing_heading_counts_as_the_same_section(self):
        text = "## GROUNDS FOR DISCHARGE\n\nfirst\n\n## LEGAL GROUNDS FOR DISCHARGE\n\nsecond\n\n"
        out, dropped = _collapse_repeated_sections(text)
        assert dropped == ["legal grounds for discharge"]
        assert "second" not in out


# ------------------------------------------------------ end to end: validator
def test_validate_draft_never_delivers_a_repeated_section():
    draft = INTRO + FACTS + GROUNDS + PRAYER + VERIF + INTRO + PRAYER_AGAIN + VERIF_AGAIN
    r = validate_draft(draft)
    out = r[0] if isinstance(r, tuple) else r
    assert out.count("MOST RESPECTFULLY SHOWETH") == 1
    assert out.count("## PRAYER") == 1
    assert out.count("## VERIFICATION") == 1
    warnings = r[1] if isinstance(r, tuple) and len(r) > 1 else []
    assert any("repeated section" in str(w).lower() for w in warnings)
