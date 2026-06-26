"""Regression tests for the Phase 1-6 drafting quality improvements.

Covers:
 - Phase 1: cite_appendix flag gates the Scenario/Legislation/Judgment fanout
 - Phase 3: doctrinal stance JSON is generated and formatted into a section block
 - Phase 4: mandatory procedural section injection (Schedule, Court Fee, IA, etc.)
 - Phase 5: pre-return validator strips [CITE: ...], fixes mojibake, flags Sec 38 SRA traps
 - Phase 6: DRAFTING_SYSTEM_PROMPT no longer asks for [CITE: ...] markers

Most tests are pure-Python (no server needed). The end-to-end partition-suit
test is opt-in via DRAFTING_QUALITY_E2E=1 (requires a running server + API key).

Usage:
    pytest tests/test_drafting_quality.py -v
    DRAFTING_QUALITY_E2E=1 pytest tests/test_drafting_quality.py::test_partition_suit_e2e -v
"""
from __future__ import annotations

import json
import os
import re

import pytest

from agents.drafting import (
    DoctrinalStance,
    DoctrinalCase,
    ProceduralSectionPlan,
    SectionPlan,
    DraftOutline,
    _inject_procedural_sections,
    _format_stance_for_section,
    validate_draft,
    _MAX_SECTIONS,
)


# ---------------------------------------------------------------------------
# Procedural-section injection (driven by DoctrinalStance, no regex).
#
# Replaces the prior `_detect_doc_type` + `_wants_temporary_injunction` +
# `_inject_mandatory_sections` + `_MANDATORY_PACKS` tests. The doctrinal
# stance LLM call now enumerates which procedural sections the pleading
# must carry; `_inject_procedural_sections` appends any that the outline
# missed. No keyword classifier, no hardcoded pack.
# ---------------------------------------------------------------------------

class TestProceduralSectionInjection:
    def _stance(self, sections):
        return DoctrinalStance(
            procedural_sections=[
                ProceduralSectionPlan(
                    title=t, description=d,
                    estimated_paragraphs=p, needs_citations=c,
                )
                for (t, d, p, c) in sections
            ],
        )

    def test_appends_missing_sections(self):
        outline = DraftOutline(
            document_title="Suit for Partition",
            court_details="Civil Court",
            sections=[
                SectionPlan(title="Brief Facts", description=""),
                SectionPlan(title="Prayer", description=""),
            ],
        )
        stance = self._stance([
            ("Schedule of Properties", "Property details", 3, False),
            ("Valuation and Court Fee", "Suit value", 2, False),
            ("Verification", "Order VI Rule 15 CPC", 1, False),
        ])
        result = _inject_procedural_sections(outline, stance)
        titles = [s.title for s in result]
        assert "Schedule of Properties" in titles
        assert "Valuation and Court Fee" in titles
        assert "Verification" in titles

    def test_existing_section_not_duplicated(self):
        outline = DraftOutline(
            document_title="Suit", court_details="X",
            sections=[
                SectionPlan(title="Brief Facts", description=""),
                SectionPlan(title="Schedule of Properties", description=""),
                SectionPlan(title="Prayer", description=""),
            ],
        )
        stance = self._stance([
            ("Schedule of Properties", "Property details", 3, False),
        ])
        result = _inject_procedural_sections(outline, stance)
        schedule_count = sum(1 for s in result if "schedule" in s.title.lower())
        assert schedule_count == 1

    def test_ia_section_added_when_stance_says_so(self):
        outline = DraftOutline(
            document_title="Suit", court_details="X",
            sections=[
                SectionPlan(title="Brief Facts", description=""),
                SectionPlan(title="Prayer", description=""),
            ],
        )
        stance = self._stance([
            ("Interim Application under Order XXXIX Rules 1 & 2 CPC",
             "Three-fold test", 5, True),
        ])
        result = _inject_procedural_sections(outline, stance)
        ia_titles = [s.title for s in result if "interim" in s.title.lower()]
        assert len(ia_titles) == 1
        assert "Order XXXIX" in ia_titles[0]

    def test_none_stance_returns_outline_unchanged(self):
        outline = DraftOutline(
            document_title="Sale Deed",
            court_details="N/A",
            sections=[SectionPlan(title="Recitals", description="")],
        )
        result = _inject_procedural_sections(outline, None)
        assert len(result) == 1
        assert result[0].title == "Recitals"

    def test_empty_procedural_list_returns_outline_unchanged(self):
        outline = DraftOutline(
            document_title="Legal Notice",
            court_details="N/A",
            sections=[SectionPlan(title="Subject", description="")],
        )
        stance = DoctrinalStance(procedural_sections=[])
        result = _inject_procedural_sections(outline, stance)
        assert len(result) == 1
        assert result[0].title == "Subject"


# ---------------------------------------------------------------------------
# Phase 3 -- doctrinal stance formatting
# ---------------------------------------------------------------------------

class TestStanceFormatting:
    def test_none_stance_returns_empty(self):
        assert _format_stance_for_section(None) == ""

    def test_full_stance_block_contains_lanes(self):
        stance = DoctrinalStance(
            property_lane="self_acquired_intestate",
            injunction_lane="temporary_only",
            applicable_statutes=["Section 8 HSA 1956"],
            non_applicable_statutes=["Section 38 SRA -- permanent only"],
            key_cases=[
                DoctrinalCase(
                    name="Dalpat Kumar v. Prahlad Singh",
                    citation="(1992) 1 SCC 719",
                    holding="three-fold injunction test",
                ),
            ],
            must_plead=["Section 11 HAMA ceremony"],
            must_not_plead=["2005 HSA amendment"],
            court_fee_rule="ad valorem under Maharashtra CFA",
        )
        block = _format_stance_for_section(stance)
        assert "PROPERTY LANE: self_acquired_intestate" in block
        assert "INJUNCTION LANE: temporary_only" in block
        assert "Section 8 HSA 1956" in block
        assert "Section 38 SRA" in block
        assert "Dalpat Kumar" in block
        assert "(1992) 1 SCC 719" in block
        assert "Section 11 HAMA" in block
        assert "2005 HSA amendment" in block


# ---------------------------------------------------------------------------
# Phase 5 -- validator
# ---------------------------------------------------------------------------

class TestValidator:
    def test_strips_cite_placeholders(self):
        draft = "The plaintiff relies on [CITE: SC case on adopted child]."
        cleaned, warnings = validate_draft(draft)
        assert "[CITE:" not in cleaned
        assert any("[CITE:" in w for w in warnings)

    def test_fixes_mojibake_roundtrip(self):
        # Real cp1252-misread-as-UTF-8 sequence for en-dash
        # `–` in UTF-8 is b"\xe2\x80\x93"; if those bytes are decoded as cp1252
        # we get "â€"" -- which is exactly what shows up in the draft.
        original_bytes = "Pune – City".encode("utf-8")
        bad = original_bytes.decode("cp1252")  # produces mojibake
        cleaned, _ = validate_draft(bad)
        assert "–" in cleaned
        assert "â€" not in cleaned

    def test_fixes_bare_a_circumflex_as_dash(self):
        # Surviving fragment case: the trailing two bytes of a 3-byte
        # UTF-8 en-dash (E2 80 93) were dropped somewhere upstream,
        # leaving a bare  between spaces. The codec roundtrip
        # cannot fix this (0xE2 alone is an invalid UTF-8 lead, so
        # strict decode aborts and the string is left untouched).
        # Symptom in the Manjri Greens WS: "Pune â 412307" where
        # an en-dash should have separated the city from the pincode.
        draft = "Manjri Budruk, Taluka Haveli, Dist. Pune â 412307"
        cleaned, _ = validate_draft(draft)
        assert " – " in cleaned
        assert " â " not in cleaned

    # NOTE: tests for forbidden statute pairings (Sec 38 SRA + temp
    # injunction; Sec 54 CPC + residential partition), orphan citation
    # tails ("Supreme Court in."), and stance compliance violations
    # were retired. Those checks now live in `core/self_refine.self_refine`
    # which runs at request time against the typed DoctrinalStance +
    # UserIntent. Unit-testing the LLM critic would require mocking
    # Gemini Flash with structured output; we cover the critic schema
    # in `tests/test_self_refine.py` and the end-to-end behaviour in
    # the partition-suit E2E gated below (DRAFTING_QUALITY_E2E=1).

    # ── HTML stripping (locked-in contract) ──────────────────────────────
    # The frontend renders markdown only — raw HTML shows up as literal
    # text. validate_draft must strip every HTML-shaped tag while keeping
    # the inner content. If any of these regress, real users see ugly
    # `<p align="center">FOO</p>` strings in the document.

    def test_strips_p_align_center(self):
        draft = '<p align="center">PARTNERSHIP DEED</p>\nThis is the body.'
        cleaned, warnings = validate_draft(draft)
        assert "<p" not in cleaned and "</p>" not in cleaned
        assert "PARTNERSHIP DEED" in cleaned
        assert "This is the body." in cleaned
        assert any("HTML" in w for w in warnings)

    def test_strips_p_align_right_and_keeps_content(self):
        draft = '<p align="right">Party of the First Part</p>'
        cleaned, _ = validate_draft(draft)
        assert "<p" not in cleaned
        assert "Party of the First Part" in cleaned

    def test_br_becomes_newline(self):
        draft = "Line one<br>Line two<br />Line three<br/>"
        cleaned, _ = validate_draft(draft)
        assert "<br" not in cleaned
        # Three line breaks added
        assert cleaned.count("\n") >= 3

    def test_hr_becomes_markdown_separator(self):
        draft = "Above<hr>Below"
        cleaned, _ = validate_draft(draft)
        assert "<hr" not in cleaned
        assert "---" in cleaned

    def test_div_span_center_all_stripped(self):
        draft = (
            '<div class="x"><span style="color:red">A</span></div>'
            "<center>Centered</center>"
        )
        cleaned, _ = validate_draft(draft)
        assert "<" not in cleaned and ">" not in cleaned
        assert "A" in cleaned
        assert "Centered" in cleaned

    def test_pure_markdown_unchanged(self):
        # Sanity: markdown with NO HTML must not be touched.
        draft = (
            "## Section 1\n\n"
            "1. **Bold** statement with _italic_ and a [link](http://x).\n"
            "2. Another point.\n\n"
            "| col | val |\n| --- | --- |\n| a | 1 |\n"
        )
        cleaned, warnings = validate_draft(draft)
        assert cleaned == draft  # byte-identical
        assert not any("HTML" in w for w in warnings)

    def test_html_strip_is_idempotent(self):
        # If validate_draft runs twice, second pass should be a no-op.
        draft = '<p align="center">Title</p>\nBody'
        once, _ = validate_draft(draft)
        twice, _ = validate_draft(once)
        assert once == twice

    def test_html_count_in_warning(self):
        # Multiple tags collapse into one warning that names the count.
        draft = "<p>A</p><div>B</div><span>C</span>"
        cleaned, warnings = validate_draft(draft)
        html_warnings = [w for w in warnings if "HTML" in w]
        assert len(html_warnings) == 1
        assert "6" in html_warnings[0]  # 3 opening + 3 closing = 6 tags


# ───────────────────────────────────────────────────────────────────────────
# chat_runner final-response sanitizer (defense-in-depth layer)
# ───────────────────────────────────────────────────────────────────────────

class TestFinalResponseSanitizer:
    """The chat_runner._strip_html_from_response is the last line of defense
    between LLM output and the user. Tests below lock the contract."""

    def _strip(self, s: str) -> str:
        from core.chat_runner import _strip_html_from_response
        return _strip_html_from_response(s)

    def test_strips_p_align_attributes(self):
        out = self._strip('<p align="center">Hello</p>')
        assert "<" not in out and ">" not in out
        assert "Hello" in out

    def test_empty_input_returns_empty(self):
        assert self._strip("") == ""

    def test_no_html_returns_same_object(self):
        s = "Just plain markdown. No tags here. **bold** *italic*."
        assert self._strip(s) == s

    def test_br_to_newline(self):
        out = self._strip("Top<br>Mid<br/>Bot")
        assert "<br" not in out
        assert out.count("\n") >= 2


# ---------------------------------------------------------------------------
# Phase 1 -- agent fanout gating (unit-level smoke)
# ---------------------------------------------------------------------------

class TestCiteAppendixGating:
    def test_drafting_state_default_off(self):
        """The default ENV value is off; cite_appendix=None should evaluate
        to 'do not fan out citation agents'."""
        from core.settings import DRAFTING_CITE_APPENDIX_DEFAULT
        # We don't assert the env value (deployment-dependent); we just verify
        # the constant exists and is a bool.
        assert isinstance(DRAFTING_CITE_APPENDIX_DEFAULT, bool)

    def test_search_request_has_cite_appendix_field(self):
        from core.gateway import SearchRequest
        # Should accept the field
        req = SearchRequest(
            Promptquery="draft a bail application", cite_appendix=True,
        )
        assert req.cite_appendix is True
        req2 = SearchRequest(Promptquery="draft a bail application")
        assert req2.cite_appendix is None  # default = inherit env

    def test_state_schema_has_cite_appendix(self):
        from core.state import LegalAgentState
        # TypedDict members are in __annotations__
        assert "cite_appendix" in LegalAgentState.__annotations__


class TestMaxSections:
    def test_max_sections_allows_full_civil_suit_pack(self):
        # 8 LLM-generated + 5 mandatory + 1 IA = 14 typical max; 16 cap is safe
        assert _MAX_SECTIONS >= 14


# ---------------------------------------------------------------------------
# Doc-type gate (Jun 2026): stops office-letter requests from being
# force-fitted to court templates.
#
# Background: a 2026-06-20 census of the drafting OpenSearch index found
# zero office-letter templates among 497 entries. Without this gate, asking
# for an RTI application returned a court petition with Prayer/Verification
# because BM25 had nothing letter-shaped to retrieve.
# ---------------------------------------------------------------------------

class TestDocTypeGate:
    def test_doc_type_constants_exported(self):
        from agents.drafting import (
            DOC_TYPES, DOC_TYPE_TO_FOOTER_KIND,
            DOC_TYPES_USE_SKELETON, SYNTHETIC_SKELETONS,
        )
        # Every doc type must have a footer-kind mapping
        for t in DOC_TYPES:
            assert t in DOC_TYPE_TO_FOOTER_KIND, f"{t} missing from footer-kind map"
        # The 3 non-court types that bypass the drafting index must have skeletons
        for t in ("office_letter", "police_complaint", "legal_notice"):
            assert t in DOC_TYPES_USE_SKELETON
            assert t in SYNTHETIC_SKELETONS
            assert len(SYNTHETIC_SKELETONS[t]) > 500, (
                f"Skeleton for {t} looks too short — outline LLM needs structure"
            )

    def test_synthetic_office_letter_skeleton_shape(self):
        """The office-letter skeleton must have addressee/subject/closing
        and must NOT have court-filing scaffolding."""
        from agents.drafting import SYNTHETIC_SKELETONS
        s = SYNTHETIC_SKELETONS["office_letter"]
        # Letter shape signals
        assert "To," in s
        assert "Subject:" in s
        assert "Sir / Madam" in s
        assert "respectfully submit" in s.lower() or "respectfully" in s.lower()
        # Anti-signals (court scaffolding must not appear)
        assert "IN THE COURT OF" not in s
        assert "IN THE MATTER OF" not in s
        assert "Versus" not in s
        assert "Prayer" not in s
        assert "Verification" not in s

    def test_synthetic_police_complaint_skeleton_shape(self):
        from agents.drafting import SYNTHETIC_SKELETONS
        s = SYNTHETIC_SKELETONS["police_complaint"]
        assert "To," in s
        assert "Police Inspector" in s or "Station House Officer" in s
        assert "Subject:" in s
        assert "register an FIR" in s.lower() or "FIR" in s
        # Police complaint is a LETTER not a court pleading
        assert "IN THE COURT OF" not in s
        assert "Versus" not in s
        assert "Prayer" not in s
        assert "Verification" not in s

    def test_synthetic_legal_notice_skeleton_shape(self):
        from agents.drafting import SYNTHETIC_SKELETONS
        s = SYNTHETIC_SKELETONS["legal_notice"]
        assert "To," in s
        assert "Subject:" in s
        assert "Take notice" in s
        assert "Advocate" in s
        assert "IN THE COURT OF" not in s
        assert "Versus" not in s
        assert "Prayer" not in s
        assert "Verification" not in s

    def test_outline_rules_branches_for_every_doc_type(self):
        """Every DOC_TYPE must have a doc-type-specific rule branch in the
        outline prompt, and the non-court branches must explicitly forbid
        Prayer / Verification."""
        from agents.drafting import DOC_TYPES
        from config.prompts import DRAFT_OUTLINE_RULES_BY_TYPE
        for t in DOC_TYPES:
            assert t in DRAFT_OUTLINE_RULES_BY_TYPE, f"{t} missing rule branch"
        # Non-court branches must drop Prayer/Verification
        for t in ("office_letter", "police_complaint", "legal_notice", "agreement_deed"):
            rules = DRAFT_OUTLINE_RULES_BY_TYPE[t]
            assert "DO NOT include" in rules, f"{t} branch should forbid court artefacts"
            assert "Prayer" in rules, f"{t} branch should name Prayer in its forbid-list"

    def test_outline_prompt_makes_prayer_conditional(self):
        """The generic outline rules must no longer hard-code Prayer as a
        universal MUST. It can only fire for court_filing / tribunal_appellate."""
        from config.prompts import DRAFT_OUTLINE_PROMPT
        # The pre-fix wording was: "Prayer/relief section MUST appear as the
        # final substantive section before Verification/Affidavit." Without a
        # type guard it forced Prayer onto every doc type. Confirm it is now
        # qualified.
        assert "For court_filing and tribunal_appellate ONLY" in DRAFT_OUTLINE_PROMPT
        # The doc-type rules placeholder is present
        assert "{doc_type_rules}" in DRAFT_OUTLINE_PROMPT


# ---------------------------------------------------------------------------
# Niche-format web fallback (Jun 2026): when the corpus has no match, the
# selector grades it 'none' and the pipeline tries the open web for a real
# template before falling back to a generic skeleton.
#
# These are pure-Python tests — no network — that lock the schema, generic
# skeletons, and validator surface in place.
# ---------------------------------------------------------------------------

class TestNicheFormatWebFallback:
    def test_template_source_has_quality_fields(self):
        from agents.drafting import TemplateSource
        fields = TemplateSource.model_fields
        # The selector must now surface quality + reason so drafting_node
        # can branch into the web-fallback path when the corpus misses.
        assert "source" in fields
        assert "match_quality" in fields
        assert "reason" in fields

    def test_generic_court_skeletons_exported(self):
        from agents.drafting import GENERIC_COURT_SKELETONS
        # Court-side doc types must have a terminal-fallback skeleton in
        # case both BM25 and the web fail.
        for t in ("court_filing", "tribunal_appellate", "agreement_deed", "affidavit"):
            assert t in GENERIC_COURT_SKELETONS, f"{t} missing generic skeleton"
            assert len(GENERIC_COURT_SKELETONS[t]) > 500, (
                f"Generic skeleton for {t} looks too short"
            )

    def test_generic_court_filing_skeleton_shape(self):
        from agents.drafting import GENERIC_COURT_SKELETONS
        s = GENERIC_COURT_SKELETONS["court_filing"]
        # Court-pleading scaffolding must be present
        assert "IN THE" in s and "COURT OF" in s
        assert "IN THE MATTER OF" in s
        assert "Versus" in s
        assert "PRAYER" in s
        assert "VERIFICATION" in s

    def test_generic_tribunal_skeleton_shape(self):
        from agents.drafting import GENERIC_COURT_SKELETONS
        s = GENERIC_COURT_SKELETONS["tribunal_appellate"]
        assert "BEFORE THE HON" in s
        assert "Appeal No" in s
        assert "(Appellant)" in s
        assert "(Respondent)" in s
        assert "Vs." in s
        # Court-pleading anti-signals (appellate is NOT a court filing)
        assert "IN THE COURT OF" not in s
        assert "Versus" not in s  # appellate uses "Vs." not "**Versus**"
        # PRAYER is allowed for appellate (it's a written submission with a relief block)
        assert "PRAYER" in s

    def test_generic_agreement_skeleton_shape(self):
        from agents.drafting import GENERIC_COURT_SKELETONS
        s = GENERIC_COURT_SKELETONS["agreement_deed"]
        assert "BETWEEN" in s and "AND" in s
        assert "WHEREAS" in s or "Recital" in s
        # Agreement anti-signals
        assert "IN THE COURT OF" not in s
        assert "PRAYER" not in s
        assert "VERIFICATION" not in s

    def test_generic_affidavit_skeleton_shape(self):
        from agents.drafting import GENERIC_COURT_SKELETONS
        s = GENERIC_COURT_SKELETONS["affidavit"]
        assert "solemnly affirm" in s.lower() or "do hereby" in s.lower()
        assert "DEPONENT" in s
        assert "Notary" in s or "Oath Commissioner" in s

    def test_generic_skeleton_for_unknown_doc_type_falls_back(self):
        from agents.drafting import _generic_skeleton_for, GENERIC_COURT_SKELETONS
        # Defensive default for a doc_type the dict doesn't know about
        out = _generic_skeleton_for("totally_made_up_type")
        assert out == GENERIC_COURT_SKELETONS["court_filing"]

    def test_generic_skeleton_prefers_office_letter_for_office_types(self):
        from agents.drafting import _generic_skeleton_for, SYNTHETIC_SKELETONS
        # office_letter is in the synthetic skeletons (first-line gate),
        # not GENERIC_COURT_SKELETONS. The helper must read from synthetic
        # first.
        out = _generic_skeleton_for("office_letter")
        assert out == SYNTHETIC_SKELETONS["office_letter"]

    def test_web_template_verdict_model_exists(self):
        # The validator's Pydantic model must surface is_usable_template
        # and reason. Import via private name since it's not exported.
        from agents.drafting import _WebTemplateVerdict
        fields = _WebTemplateVerdict.model_fields
        assert "is_usable_template" in fields
        assert "reason" in fields


# ---------------------------------------------------------------------------
# End-to-end (opt-in) -- partition suit regression
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("DRAFTING_QUALITY_E2E") != "1",
    reason="Set DRAFTING_QUALITY_E2E=1 + a running server + LAWTECH_API_KEY to enable",
)
class TestPartitionSuitE2E:
    """End-to-end regression for the partition + injunction prompt. Requires:
       - server running on http://localhost:5000
       - LAWTECH_API_KEY env var
       Compares the response against all 13 originally-flagged issues.
    """

    PROMPT = (
        "Draft a civil suit for partition along with a prayer for temporary "
        "injunction. Facts: Rupa is an adopted daughter of the deceased owner. "
        "She has no siblings. Her father died 5 years ago, leaving behind two "
        "flats in Pune. The only legal heirs are Rupa and her mother. Her name "
        "is not yet on the property documents. Her mother is attempting to "
        "dispossess her and has expressed intent to transfer the properties to "
        "her cousin. The suit should include legal provisions for partition of "
        "the properties, declaration of her share, and a temporary injunction "
        "restraining the mother or any third party from selling, transferring, "
        "or creating third-party rights in the properties until disposal of the suit."
    )

    @classmethod
    def setup_class(cls):
        import requests
        api_key = os.environ.get("LAWTECH_API_KEY")
        assert api_key, "Set LAWTECH_API_KEY env var to run E2E tests"
        url = os.environ.get("LAWTECH_API_URL", "http://localhost:5000") + "/pyapi/search"
        cls.response = requests.post(
            url,
            json={"Promptquery": cls.PROMPT},
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            timeout=120,
        )
        cls.response.raise_for_status()
        cls.payload = cls.response.json()
        cls.body = cls.payload["result"]

    def test_only_drafting_agent_fanned_out(self):
        assert self.payload["agents_used"] == ["Drafting"]

    def test_no_cite_placeholders(self):
        assert "[CITE:" not in self.body

    def test_no_sec_38_sra_near_temporary_injunction(self):
        # Same per-paragraph rule the validator uses
        for para in re.split(r"\n\s*\n", self.body):
            if re.search(r"Section\s*38.*Specific Relief", para, re.IGNORECASE):
                assert not re.search(r"temporary\s+injunction", para, re.IGNORECASE), (
                    f"Sec 38 SRA mis-cited for temporary injunction in: {para[:200]}"
                )

    def test_no_2005_amendment_for_self_acquired(self):
        # Self-acquired intestate devolution should not invoke the 2005 HSA amendment
        if re.search(r"self-acquired", self.body, re.IGNORECASE):
            assert "2005 amendment" not in self.body.lower()

    def test_has_mandatory_procedural_blocks(self):
        body_low = self.body.lower()
        assert "schedule of properties" in body_low or "schedule of property" in body_low
        assert "court fee" in body_low
        assert "list of documents" in body_low
        assert "interim application" in body_low
        assert any(k in body_low for k in ("notary", "oath commissioner"))

    def test_no_mojibake(self):
        assert "â€" not in self.body

    def test_substantive_length(self):
        # v1 was 12.5k chars; we expect at least 18k now with all the procedural blocks
        assert len(self.body) >= 18_000, f"draft only {len(self.body)} chars"

    def test_total_response_under_30k_chars(self):
        # With citation appendix off, response is just the draft -- should not blow up
        assert len(self.body) <= 30_000, (
            "Response shouldn't be ballooning -- check for accidental appendix"
        )
