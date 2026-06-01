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
    SectionPlan,
    DraftOutline,
    _detect_doc_type,
    _wants_temporary_injunction,
    _inject_mandatory_sections,
    _format_stance_for_section,
    validate_draft,
    _MAX_SECTIONS,
)


# ---------------------------------------------------------------------------
# Phase 4 -- doc-type detection + mandatory section injection
# ---------------------------------------------------------------------------

class TestDocTypeDetection:
    def test_civil_suit_keywords(self):
        assert _detect_doc_type("Draft a partition suit", "SUIT FOR PARTITION") == "civil_suit"
        assert _detect_doc_type("plaint for recovery of money", "Money Recovery Suit") == "civil_suit"
        assert _detect_doc_type("declaration and permanent injunction", "Declaratory Suit") == "civil_suit"

    def test_bail(self):
        assert _detect_doc_type("Draft a bail application", "Anticipatory Bail") == "bail"

    def test_writ(self):
        assert _detect_doc_type("file a writ petition under Article 226", "Writ Petition") == "writ"

    def test_notice(self):
        assert _detect_doc_type("legal notice under section 138", "Demand Notice") == "notice"

    def test_unknown_falls_back_to_other(self):
        assert _detect_doc_type("random text", "Some Title") == "other"


class TestWantsTemporaryInjunction:
    def _outline(self, *titles):
        return DraftOutline(
            document_title="X", court_details="Y",
            sections=[SectionPlan(title=t, description="") for t in titles],
        )

    def test_explicit_temporary(self):
        assert _wants_temporary_injunction(
            "prayer for temporary injunction", self._outline("Prayer"),
        )

    def test_interim_relief(self):
        assert _wants_temporary_injunction(
            "interim relief during pendency", self._outline("Grounds"),
        )

    def test_no_injunction(self):
        assert not _wants_temporary_injunction(
            "draft a sale deed", self._outline("Recitals", "Consideration"),
        )


class TestMandatorySectionInjection:
    def test_civil_suit_pack_appended_when_missing(self):
        outline = DraftOutline(
            document_title="Suit for Partition",
            court_details="Civil Court",
            sections=[
                SectionPlan(title="Brief Facts", description=""),
                SectionPlan(title="Prayer", description=""),
            ],
        )
        result = _inject_mandatory_sections(outline, "draft a civil suit for partition")
        titles = [s.title for s in result]
        assert "Schedule of Properties" in titles
        assert "Valuation and Court Fee" in titles
        assert "List of Documents" in titles
        assert "Verification" in titles
        assert "Affidavit in Support" in titles

    def test_existing_section_not_duplicated(self):
        outline = DraftOutline(
            document_title="Suit", court_details="X",
            sections=[
                SectionPlan(title="Brief Facts", description=""),
                SectionPlan(title="Schedule of Properties", description=""),  # already present
                SectionPlan(title="Prayer", description=""),
            ],
        )
        result = _inject_mandatory_sections(outline, "civil suit partition")
        # Only one "Schedule" section even after injection
        schedule_count = sum(1 for s in result if "schedule" in s.title.lower())
        assert schedule_count == 1

    def test_ti_injection_when_requested(self):
        outline = DraftOutline(
            document_title="Suit", court_details="X",
            sections=[
                SectionPlan(title="Brief Facts", description=""),
                SectionPlan(title="Prayer", description=""),
            ],
        )
        result = _inject_mandatory_sections(
            outline, "civil suit with prayer for temporary injunction",
        )
        ia_titles = [s.title for s in result if "interim" in s.title.lower()]
        assert len(ia_titles) == 1
        assert "Order XXXIX" in ia_titles[0]

    def test_ti_not_injected_when_not_requested(self):
        outline = DraftOutline(
            document_title="Suit", court_details="X",
            sections=[SectionPlan(title="Brief Facts", description="")],
        )
        result = _inject_mandatory_sections(outline, "draft a sale deed")
        ia_titles = [s.title for s in result if "interim" in s.title.lower()]
        assert len(ia_titles) == 0


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

    def test_flags_sec_38_sra_near_temporary_injunction(self):
        draft = (
            "The plaintiff prays for a temporary injunction under "
            "Section 38 of the Specific Relief Act, 1963."
        )
        _, warnings = validate_draft(draft)
        assert any("Section 38 SRA" in w and "temporary" in w for w in warnings)

    def test_no_warning_when_sec_38_used_for_permanent_injunction(self):
        draft = (
            "Para 1: The plaintiff prays for a permanent injunction under "
            "Section 38 of the Specific Relief Act, 1963.\n\n"
            "Para 2: A temporary injunction is also sought under Order XXXIX "
            "Rules 1 and 2 of the Code of Civil Procedure, 1908."
        )
        _, warnings = validate_draft(draft)
        # Two paragraphs are separated; the Sec 38 paragraph mentions permanent,
        # not temporary, so no trap warning.
        sec38_warnings = [w for w in warnings if "Section 38 SRA" in w]
        assert len(sec38_warnings) == 0

    def test_strips_orphan_citation_tails(self):
        draft = "This principle was reaffirmed by the Supreme Court in."
        cleaned, warnings = validate_draft(draft)
        assert "Supreme Court in." not in cleaned
        assert any("orphan citation" in w for w in warnings)

    def test_stance_violation_warning(self):
        stance = DoctrinalStance(
            non_applicable_statutes=["Section 38 Specific Relief Act"],
        )
        draft = "The plaintiff cites Section 38 Specific Relief Act."
        _, warnings = validate_draft(draft, stance)
        assert any("non-applicable" in w for w in warnings)


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
