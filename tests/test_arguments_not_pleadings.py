"""An arguments request is analysis, never a pleading.

Advocate test 2026-09-11 on five argument-style prompts: "Give me arguments
in favor of the accused in a bail application under Section 439 CrPC"
came back as a MEMORANDUM OF ARGUMENTS with cause title, PRAYER and
[placeholders]; "List the points I should orally submit ..." came back as
written submissions with a PRAYER. Two causes, both covered here:
routing (extractor says draft) and output shape (a non-drafting agent
writes a pleading). Pure tests.
"""
from __future__ import annotations

import asyncio

import pytest

from agents.orchestrator import _is_argument_request
from core.pleading_furniture import pleading_kinds, strip_pleading_furniture


# ------------------------------------------------------------ routing
@pytest.mark.parametrize("q", [
    "Give me arguments in favor of the accused in a bail application under Section 439 CrPC where the accused has been in custody for 8 months in a case involving Section 420 IPC",
    "What are the counter-arguments the prosecution can raise against a bail application in a Section 420 IPC matter?",
    "List the points I should orally submit at the time of arguments in a Section 138 NI Act cheque bounce trial from the accused side",
    "Draft strong arguments to argue on the next date of hearing in a domestic violence complaint under DV Act where the wife is seeking maintenance",
    "धारा 498A IPC के मामले में अभियुक्त की ओर से बहस के लिए मुख्य तर्क क्या होने चाहिए?",
    "what defences are available to the accused in a cheque bounce case",
    "how should I argue against the anticipatory bail of the co-accused",
    "Case for the petitioner in a writ petition under Article 226 for regularisation of service",
    "How do I oppose the anticipatory bail application of the accused in a 498A case",
    "Reasons why bail should be granted to the accused in a Section 376 case where FIR was delayed",
    "Points in favour of the accused in the bail application before the Sessions Court",
    "Provide me Arguments on behalf of iqbal in Bail application before the high court with Supporting case laws",
])
def test_argument_requests_are_recognised(q):
    assert _is_argument_request(q) is True


@pytest.mark.parametrize("q", [
    "Draft the grounds of appeal against the conviction order",
    "Draft a bail application with grounds of parity and delay",
    "Draft written arguments for the accused in the Section 138 trial",
    "Prepare a memorandum of arguments on behalf of the applicant",
    "Draft a bail application under Section 439 CrPC for an accused in custody for 8 months",
    "Prepare written submissions for the respondent in the maintenance case",
    "Give me a legal notice for non-payment of rent",
    "What is Section 439 CrPC?",
    "Summarise the attached judgment",
])
def test_document_requests_and_plain_questions_are_not_argument_requests(q):
    assert _is_argument_request(q) is False


def test_plan_node_applies_the_override_on_both_signals():
    import inspect
    import agents.orchestrator as orch
    src = inspect.getsource(orch)
    assert "_apply_argument_override(" in src
    assert "_original_query, task, tasks_planned, extracted_intent" in src


IQBAL = ("Provide me Arguments on behalf of iqbal in Bail application before the high court with Supporting "
         "case laws or Citations with full Detatied and the Major point was: There is no Motive estabilsed")


class _Intent:
    def __init__(self, task_intent):
        self.task_intent = task_intent

    def model_copy(self, update):
        return _Intent(update.get("task_intent", self.task_intent))


def test_classifier_drafting_with_analyze_intent_is_corrected():
    # 2026-09-13 retest: extractor said analyze, classifier picked Drafting; the
    # intent-gated override never ran and a full pleading shipped.
    from agents.orchestrator import _apply_argument_override
    task, planned, intent, changed = _apply_argument_override(
        IQBAL, "Drafting", ["Drafting", "SCI_Judgment", "Newacts"], _Intent("analyze"))
    assert changed and task == "Scenario"
    assert "Drafting" not in planned and planned[0] == "Scenario"
    assert intent.task_intent == "analyze"


def test_draft_intent_with_newacts_classifier_is_corrected():
    # 2026-09-11 shape: extractor said draft, classifier picked Newacts.
    from agents.orchestrator import _apply_argument_override
    task, planned, intent, changed = _apply_argument_override(
        IQBAL, "Newacts", ["Newacts", "Drafting"], _Intent("draft"))
    assert changed and task == "Newacts" and "Drafting" not in planned
    assert intent.task_intent == "analyze"


def test_explicit_drafting_request_is_never_corrected():
    from agents.orchestrator import _apply_argument_override
    q = "Draft a bail application under Section 439 CrPC for Iqbal, in custody for 4 years, no recovery, no motive"
    task, planned, intent, changed = _apply_argument_override(q, "Drafting", ["Drafting", "SCI_Judgment"], _Intent("draft"))
    assert not changed and task == "Drafting" and planned == ["Drafting", "SCI_Judgment"]
    assert intent.task_intent == "draft"


def test_correct_route_is_left_untouched():
    from agents.orchestrator import _apply_argument_override
    task, planned, intent, changed = _apply_argument_override(
        IQBAL, "Scenario", ["Scenario", "Newacts"], _Intent("analyze"))
    assert not changed and task == "Scenario" and planned == ["Scenario", "Newacts"]


# ------------------------------------------------------- output shape
TEST1_SHAPE = (
    "## MEMORANDUM OF ARGUMENTS ON BEHALF OF THE ACCUSED / APPLICANT\n\n"
    "IN THE COURT OF SESSIONS JUDGE, [DISTRICT]\n\n"
    "CRIMINAL MISC. (BAIL) APPLICATION NO. [Number/Year]\n\n"
    "FIR NO. [Number/Year]\n\nPOLICE STATION: [Name of Police Station]\n\n"
    "APPLICATION UNDER SECTION 439 OF THE CODE OF CRIMINAL PROCEDURE, 1973\n\n"
    "[Name of Accused / Applicant] .....Applicant\n\nVERSUS\n\nState .....Respondent\n\n"
    "### 1. PERIOD OF CUSTODY AND PRESUMPTION OF INNOCENCE\n\n"
    "1. The applicant has been in custody for eight months; the offence under Section 420 IPC carries a maximum of seven years.\n\n"
    "### 2. TRIPLE TEST\n\n"
    "2. No flight risk: permanent resident with family ties. No tampering: documentary case. No influence: witnesses are officials.\n\n"
    "### 3. PARITY\n\n3. Co-accused released on 12.03.2026.\n\n"
    "### PRAYER\n\nIt is therefore prayed that the applicant be released on bail.\n\n"
    "Accused/Applicant Through Counsel\n\n[Name of Advocate]\n\nPlace: [City]\n\nDate: [Date]\n"
)


def test_test1_shape_is_recognised_as_pleading_furniture():
    kinds = pleading_kinds(TEST1_SHAPE)
    assert {"caption", "number", "parties", "prayer", "signoff"} <= set(kinds)


def test_furniture_is_stripped_and_arguments_kept_for_scenario():
    out, st = strip_pleading_furniture(TEST1_SHAPE, "Scenario")
    assert st["prayer_removed"] == 1 and st["lines_removed"] > 0
    for gone in ("IN THE COURT OF SESSIONS", "APPLICATION NO.", "FIR NO.", "POLICE STATION", "VERSUS",
                 ".....Applicant", "PRAYER", "Through Counsel", "Place:", "Date:", "[Name of Advocate]"):
        assert gone not in out, gone
    for kept in ("PERIOD OF CUSTODY AND PRESUMPTION OF INNOCENCE", "TRIPLE TEST", "PARITY",
                 "eight months", "Co-accused released on 12.03.2026"):
        assert kept in out, kept


def test_drafting_and_document_answers_are_never_touched():
    for task in ("Drafting", "Document", None):
        out, st = strip_pleading_furniture(TEST1_SHAPE, task)
        assert out == TEST1_SHAPE and st["prayer_removed"] == 0


def test_single_kind_is_not_enough_to_strip():
    # a judgment summary may legitimately have a "Prayer" heading for the reliefs sought
    text = "## Facts\n\ntext\n\n## Prayer of the petitioner\n\nThe petitioner sought quashing.\n\n## Holding\n\nDismissed.\n"
    out, st = strip_pleading_furniture(text, "Judgment")
    assert out == text and st["prayer_removed"] == 0


def test_ordinary_scenario_analysis_is_untouched():
    text = ("### STATUTORY FRAMEWORK\n\nSection 439 CrPC empowers the Sessions Court to grant bail.\n\n"
            "### GROUNDS FOR BAIL\n\n1. Period of custody.\n2. Nature of evidence.\n")
    out, st = strip_pleading_furniture(text, "Scenario")
    assert out == text and st["kinds"] == []


def test_guardrail_strips_for_scenario_but_not_for_drafting():
    from agents.guardrail import guardrail_output_node
    out_s = asyncio.run(guardrail_output_node({"final_response": TEST1_SHAPE, "task": "Scenario"}))["final_response"]
    out_d = asyncio.run(guardrail_output_node({"final_response": TEST1_SHAPE, "task": "Drafting"}))["final_response"]
    assert "PRAYER" not in out_s and "IN THE COURT OF SESSIONS" not in out_s and "TRIPLE TEST" in out_s
    assert "PRAYER" in out_d and "IN THE COURT OF SESSIONS" in out_d


def test_prompt_rule_present():
    from config.prompts import INDIAN_LEGAL_BEHAVIORAL_DISCIPLINE as D
    assert "ARGUMENTS ARE NOT PLEADINGS" in D
