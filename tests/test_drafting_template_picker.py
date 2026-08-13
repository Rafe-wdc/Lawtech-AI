"""Regression tests for reference-template selection (Q-8 / Q-15).

THE BUG
=======
The reference template is the ONLY source of a draft's structure, and it was
chosen by an LLM shown nothing but file paths. Four runs of one query —

    "Draft a bail application for cheating under Section 420 IPC"

— picked three different templates:

    Section 439 CrPC   (Sessions Court bail)      runs 1, 2   9 sections
    Section 436 CrPC   (Magistrate, bailable)     run 3       5 sections
    Section 307 IPC    (ATTEMPTED MURDER)         run 4       6 sections

Run 4's logged reasoning: "this file is a bail application template that also
mentions Section 420 IPC" — i.e. guessing from a filename that lists several
sections. Wrong template means wrong document.

THE CAUSE — measured, not assumed
=================================
The original hypothesis was that the funnel collapsed: size:100 returns
PASSAGES, so a few long templates could crowd out the right one. **Probing the
live index disproved that** — 100 passages already yielded 100 distinct
templates, because each template is stored as a single passage.

The real cause is what those 100 candidates ARE. For "bail application for
cheating under Section 420 IPC", BM25 ranks by keyword overlap, so the list is
dominated by *cheating* documents of the wrong TYPE:

    Format for Complaint for the offence of cheating DRAFT 4.csv
    Format of a Notice, Sec 138 NI Act and Sec. 420 IPC.csv
    Draft or specimen of A Notice w/s. 138 NI Act and Sec 420.csv

Complaints and notices — not bail applications. The actual bail templates rank
lower. Choosing from that list on filenames alone, the picker took a template
whose name mentioned Section 420 and got the document type wrong.

THE FIX
=======
Each candidate now carries an OPENING LINES preview, which begins with the
template's own "Draft Heading:" and its cause title — naming both the DOCUMENT
TYPE and the FORUM:

    "Specimen/Draft/Format for Complaint Under Section 420 IPC"  -> a COMPLAINT
    "IN THE COURT OF HON'BLE SESSIONS COURT ___"                 -> 439, Sessions
    "BEFORE THE HON'BLE MAGISTRATE ___"                          -> 436, Magistrate

`collapse` on source.keyword is kept: it is a no-op on today's corpus but
guarantees size:100 stays 100 *documents* if templates are ever re-indexed as
multiple passages.

Run:
    pytest tests/test_drafting_template_picker.py -v
    python tests/test_drafting_template_picker.py     # no pytest required
"""

from __future__ import annotations

import inspect
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("OPENAI_API_KEY", "test-key-unused")
os.environ.setdefault("GOOGLE_API_KEY", "test-key-unused")
os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://localhost:1")

import agents.drafting as d  # noqa: E402
from agents.drafting import _PICKER_PREVIEW_CHARS  # noqa: E402
from config.prompts import DRAFTING_PICKER_PROMPT  # noqa: E402

# The three real templates from the incident.
REAL_CANDIDATES = [
    ("/content/formats_and_all_drafts/Bail Application under Section 439 of "
     "CrPC or under section 483 of BNSS.csv",
     "IN THE COURT OF HON'BLE SESSIONS COURT _____, AT _____ Cri. Bail. "
     "Application No. __ /20__ In C.R. No __ /20__"),
    ("/content/formats_and_all_drafts/Format First bail Application under "
     "section 436 of CrPC DRAFT 1.csv",
     "BEFORE THE HON'BLE MAGISTRATE __ , AT __ Cri. Misc. Application "
     "No. __ /20 In C.R No __ /20 __"),
    ("/content/formats_and_all_drafts/Format of Bail Application In Case of "
     "Section 307 of Indian Penal Code, 1860 or Section 109 of .csv", ""),
]


class TestCandidatesCarryPreviews:

    def test_picker_accepts_path_preview_pairs(self):
        src = inspect.getsource(d._pick_reference_source)
        assert "pairs" in src and "OPENING LINES" in src

    def test_bare_paths_still_work(self):
        """Backwards compatible — a list of plain strings must not break."""
        src = inspect.getsource(d._pick_reference_source)
        assert "isinstance(c, str)" in src, (
            "older callers passing bare paths would crash")

    def test_call_site_passes_previews_not_bare_paths(self):
        src = inspect.getsource(d._acquire_reference_draft)
        assert "_pick_reference_source(search_query, candidates)" in src, (
            "the picker is being handed bare paths again — the previews are "
            "the whole point of this fix")

    def test_preview_budget_is_enough_for_a_cause_title(self):
        assert _PICKER_PREVIEW_CHARS >= 150
        # Both real cause titles must fit inside the budget.
        for _, preview in REAL_CANDIDATES[:2]:
            head = preview[:_PICKER_PREVIEW_CHARS]
            assert ("SESSIONS COURT" in head) or ("MAGISTRATE" in head), (
                "budget too small to reach the forum name")

    def test_previews_actually_separate_the_confusable_templates(self):
        """Names are indistinguishable; opening lines are not."""
        n439, n436 = REAL_CANDIDATES[0][0], REAL_CANDIDATES[1][0]
        assert "bail" in n439.lower() and "bail" in n436.lower(), (
            "both names say 'bail application' — that is the problem")
        p439, p436 = REAL_CANDIDATES[0][1], REAL_CANDIDATES[1][1]
        assert "SESSIONS COURT" in p439 and "SESSIONS COURT" not in p436
        assert "MAGISTRATE" in p436 and "MAGISTRATE" not in p439


class TestCollapseReturnsDistinctTemplates:

    def test_query_collapses_on_source(self):
        src = inspect.getsource(d._acquire_reference_draft)
        assert '"collapse"' in src and "source.keyword" in src, (
            "size:100 returns 100 passages, not 100 documents, without this")

    def test_page_content_is_requested(self):
        src = inspect.getsource(d._acquire_reference_draft)
        assert '"page_content"' in src, "no preview can be built without it"

    def test_falls_back_when_collapse_is_unsupported(self):
        """Losing previews beats losing the corpus to a web draft."""
        src = inspect.getsource(d._acquire_reference_draft)
        assert "retrying without collapse" in src.lower()
        assert src.count("fallback_body") >= 1

    def test_funnel_width_is_logged(self):
        src = inspect.getsource(d._acquire_reference_draft)
        assert "distinct_templates" in src and "passages_returned" in src, (
            "without both numbers you cannot tell whether the funnel collapsed")


class TestPickerPromptUsesThePreviews:

    def test_prompt_no_longer_claims_names_are_the_only_metadata(self):
        assert "OPENING LINES" in DRAFTING_PICKER_PROMPT
        assert "The file names ARE the metadata" not in DRAFTING_PICKER_PROMPT

    def test_prompt_warns_about_multi_section_filenames(self):
        """The exact reasoning that produced the Section 307 pick."""
        low = DRAFTING_PICKER_PROMPT.lower()
        assert "lists several statutory sections" in low
        assert "forum" in low and "provision" in low

    def test_none_remains_a_valid_answer(self):
        assert '"none"' in DRAFTING_PICKER_PROMPT, (
            "'none' triggers the web fallback — it must survive this change")

    def test_document_type_is_a_hard_gate_applied_first(self):
        """Previews alone were not enough.

        With previews but only guidance, the picker read the preview,
        correctly identified the file as a COMPLAINT, and chose it anyway
        for a bail application — its own logged reasoning said so. The type
        rule has to be an ordered gate, not a preference.
        """
        low = DRAFTING_PICKER_PROMPT.lower()
        assert "step 1" in low and "step 2" in low, "the gate must be ordered"
        assert "never interchangeable" in low
        assert "shared statutory section does not make two document types" in low, (
            "this is the exact error to prevent: picking a complaint for a "
            "bail application because both cite s.420")

    def test_picker_must_state_the_type_match_in_its_reasoning(self):
        """Forcing the comparison into the output makes the error self-evident."""
        low = DRAFTING_PICKER_PROMPT.lower()
        assert "reasoning" in low and "do not match" in low


# --- Standalone runner (venv has no pytest) ---------------------------------

if __name__ == "__main__":
    failures = 0
    for cls in (TestCandidatesCarryPreviews, TestCollapseReturnsDistinctTemplates,
                TestPickerPromptUsesThePreviews):
        inst = cls()
        for name in sorted(n for n in dir(inst) if n.startswith("test_")):
            try:
                getattr(inst, name)()
                print(f"  PASS  {cls.__name__}.{name}")
            except Exception as e:
                failures += 1
                print(f"  FAIL  {cls.__name__}.{name}: {type(e).__name__}: {e}")
    print("\nALL PASSED" if not failures else f"\n{failures} FAILURE(S)")
    sys.exit(1 if failures else 0)
