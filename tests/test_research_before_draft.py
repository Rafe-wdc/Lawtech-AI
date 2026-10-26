"""research_before_draft: an explicit "research before drafting" instruction
outranks the classifier's production-verb + document-noun rule.

Reported 2026-10-25 (prod thread 4f9eae8): "...i need to prepare a draft of
suit for eviction ... ( before drafting I want to research on the legal
aspect, how should i state the facts of the case)". The classifier chose
Drafting, the cite-appendix strip removed the research agents, a full plaint
shipped and the critic's "Please check before filing" banner fired.

Deterministic: the classifier, the intent extractor and the per-agent query
rewriter are stubbed with the outputs observed for each query; the plan node
and the synthesis node run for real.
"""
from __future__ import annotations

import asyncio

import pytest

import agents.orchestrator as orch
from config.intent import UserIntent
from core.language import _format_intent_directives
from core.state import AgentResult

EVICTION = (
    "I had send the legal notice to the licensee ... now today is 25 oct and i need to "
    "prepare a draft of suit for eviction, arrears of rent and mense profit and damages and "
    "permanent injuction. ( before drafting I want to research on the legal aspect, how "
    "should i state the facts of the case)"
)
TATA = (
    "BEFORE DRAFTING: Review all attached material ... Discuss the matter in depth. ... "
    "DRAFT THE LEGAL NOTICE ... OUTPUT 2. A list of weaknesses ... 3. The recommended "
    "consumer forum ... 4. The complete legal notice"
)
RESEARCH_ONLY = ("First analyse the law on eviction of a licensee from a shop in Nagpur and "
                 "tell me how to frame the facts. Don't draft the plaint yet.")
RESEARCH_AND_DRAFT = ("Research the law on cheque dishonour under Section 138 NI Act and then "
                      "draft the legal notice. Give me both: first the analysis, then the notice.")
PLAIN = "Draft a plaint for eviction of a tenant from a shop in Nagpur for non-payment of rent."
PLAIN_REASONING = "Draft a bail application under Section 483 BNSS with detailed legal reasoning and case laws."


def _intent(task_intent="draft", rbd="none"):
    return UserIntent(task_intent=task_intent, research_before_draft=rbd, confidence=0.95)


# --- the pure override ------------------------------------------------------------

def test_research_only_moves_the_plan_off_drafting():
    task, plan, intent, changed = orch._apply_research_first_override(
        "Drafting", ["Drafting", "Scenario", "Legal_Concepts"], _intent("draft", "research_only"))
    assert changed and task == "Scenario"
    assert "Drafting" not in plan and plan[0] == "Scenario" and "Legal_Concepts" in plan
    assert intent.task_intent == "analyze"          # no later rule can re-add Drafting


def test_research_only_with_drafting_alone_still_gets_a_research_agent():
    task, plan, _, _ = orch._apply_research_first_override(
        "Drafting", ["Drafting"], _intent("draft", "research_only"))
    assert task == "Scenario" and plan == ["Scenario"]


def test_research_and_draft_keeps_drafting_primary_and_adds_scenario():
    task, plan, intent, changed = orch._apply_research_first_override(
        "Drafting", ["Drafting", "Legislation"], _intent("draft", "research_and_draft"))
    assert changed and task == "Drafting"
    assert plan[:2] == ["Drafting", "Scenario"] and "Legislation" in plan
    assert intent.task_intent == "draft"


@pytest.mark.parametrize("task,plan", [("Drafting", ["Drafting"]), ("Scenario", ["Scenario", "Legislation"])])
def test_no_flag_changes_nothing(task, plan):
    assert orch._apply_research_first_override(task, list(plan), _intent()) == (task, plan, _intent(), False)
    assert orch._apply_research_first_override(task, list(plan), None)[3] is False


# --- directives: present only on the flag -----------------------------------------

def test_directives_absent_without_the_flag():
    assert "USER SEQUENCING" not in _format_intent_directives(_intent())


def test_research_only_directive_forbids_the_draft_and_offers_it_next():
    d = _format_intent_directives(_intent("analyze", "research_only"))
    assert "Do NOT draft the document" in d and "offering to draft the document next" in d


def test_research_and_draft_directive_splits_the_two_parts():
    d = _format_intent_directives(_intent("draft", "research_and_draft"))
    assert "write ONLY the document" in d and "write ONLY the research" in d


# --- the real plan node (legacy path: classifier + extractor) ----------------------

def _plan(monkeypatch, query, classifier, intent):
    monkeypatch.setenv("DYNAMIC_ORCHESTRATOR", "0")
    monkeypatch.setattr(orch, "_classify_and_plan", lambda *a, **k: classifier)
    monkeypatch.setattr(orch, "_extract_user_intent", lambda *a, **k: (query, intent))
    monkeypatch.setattr(orch, "_rewrite_queries_for_agents", lambda q, agents: {a: q for a in agents})
    state = {"query": query, "original_query": query, "user_language": "en",
             "chat_history": [], "messages": []}
    out = asyncio.run(orch.orchestrator_plan_node(state))
    return out["task"], out["tasks_planned"], out.get("agent_queries") or {}


def test_plan_eviction_query_routes_to_research(monkeypatch):
    # observed: classifier Drafting, extractor research_only / analyze
    task, plan, _ = _plan(monkeypatch, EVICTION, ("Drafting", ["Drafting", "Scenario", "Legal_Concepts"]),
                          _intent("analyze", "research_only"))
    assert task == "Scenario" and "Drafting" not in plan and "Scenario" in plan


def test_plan_explicit_research_only(monkeypatch):
    task, plan, _ = _plan(monkeypatch, RESEARCH_ONLY, ("Scenario", ["Scenario", "Legislation"]),
                          _intent("analyze", "research_only"))
    assert task == "Scenario" and "Drafting" not in plan


@pytest.mark.parametrize("query", [TATA, RESEARCH_AND_DRAFT])
def test_plan_research_and_draft_keeps_the_research_agents(monkeypatch, query):
    # without the flag the cite-appendix strip reduces this plan to ['Drafting']
    task, plan, queries = _plan(monkeypatch, query, ("Drafting", ["Drafting", "Scenario", "Legislation"]),
                                _intent("draft", "research_and_draft"))
    assert task == "Drafting" and plan[:2] == ["Drafting", "Scenario"] and "Legislation" in plan
    # the statute agent researches; it is not handed the citations-only brief
    assert not queries.get("Legislation", "").startswith(orch._CITATIONS_ONLY_PREFIX)


@pytest.mark.parametrize("query", [PLAIN, PLAIN_REASONING])
def test_plan_ordinary_drafting_is_unchanged(monkeypatch, query):
    task, plan, _ = _plan(monkeypatch, query, ("Drafting", ["Drafting", "Scenario", "Legal_Concepts"]),
                          _intent("draft", "none"))
    assert task == "Drafting" and plan == ["Drafting"]       # strip behaves as before


# --- the real synthesis node --------------------------------------------------------

DRAFT = "IN THE COURT OF THE CIVIL JUDGE\n\nPlaint ...\n\nPRAYER\n(a) decree ..."
RESEARCH = "### Service of notice\nRefusal of RPAD is deemed service ...\n\n### How to state the facts\n..."


def _synth(monkeypatch, rbd, results):
    async def _same(text, **kw):
        return text
    monkeypatch.setattr("core.judgments_cited.append_judgments_cited", _same)
    q = "query"
    state = {"original_query": q, "query": q, "task": "Drafting", "tasks_planned": list(results),
             "previous_artifact_content": "", "user_intent": _intent("draft", rbd),
             "agent_results": {k: AgentResult(agent_name=k, content=v) for k, v in results.items()}}
    return asyncio.run(orch.orchestrator_synthesize_node(state))["final_response"]


def test_synthesis_puts_research_before_the_draft(monkeypatch):
    ans = _synth(monkeypatch, "research_and_draft",
                 {"Drafting": DRAFT, "Scenario": RESEARCH, "Legislation": "Section 106 TPA ..."})
    p1, p2 = ans.index("## Part 1"), ans.index("## Part 2")
    assert p1 < ans.index("Refusal of RPAD") < p2 < ans.index("IN THE COURT OF")
    assert "Section 106 TPA" in ans[p1:p2]                    # statute research sits in Part 1
    assert "REFERENCES & CITATIONS" not in ans                # not duplicated as an appendix


def test_synthesis_without_the_flag_ships_the_draft_alone(monkeypatch):
    ans = _synth(monkeypatch, "none", {"Drafting": DRAFT, "Scenario": RESEARCH})
    assert ans.startswith("IN THE COURT OF") and "Part 1" not in ans and "Refusal of RPAD" not in ans


def test_classifier_itself_routes_research_first_away_from_drafting():
    # The intent call can fail (prod-like run 2026-10-26: "Server disconnected",
    # default intent); the classifier must carry the rule on its own.
    p = orch.CLASSIFY_AND_PLAN_PROMPT
    i = p.index("3. **Drafting**")
    rule = p[i:p.index("4. **Newacts**", i)]
    assert "before drafting I want to" in rule and "**Scenario**" in rule


def test_plan_research_only_survives_an_intent_failure(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("Server disconnected without sending a response.")
    monkeypatch.setenv("DYNAMIC_ORCHESTRATOR", "0")
    monkeypatch.setattr(orch, "_classify_and_plan", lambda *a, **k: ("Scenario", ["Scenario", "Legislation"]))
    monkeypatch.setattr(orch, "_extract_user_intent", _boom)
    monkeypatch.setattr(orch, "_rewrite_queries_for_agents", lambda q, agents: {a: q for a in agents})
    out = asyncio.run(orch.orchestrator_plan_node(
        {"query": EVICTION, "original_query": EVICTION, "user_language": "en", "chat_history": [], "messages": []}))
    assert out["task"] == "Scenario" and "Drafting" not in out["tasks_planned"]


def test_research_and_draft_gives_each_side_its_scope(monkeypatch):
    # The directive alone did not hold: each agent wrote both halves.
    _, plan, queries = _plan(monkeypatch, RESEARCH_AND_DRAFT, ("Drafting", ["Drafting", "Scenario", "Legislation"]),
                             _intent("draft", "research_and_draft"))
    assert queries["Drafting"].startswith(RESEARCH_AND_DRAFT)          # user text verbatim and first
    assert queries["Drafting"].endswith(orch._DOCUMENT_ONLY_SCOPE)
    assert queries["Scenario"].endswith(orch._RESEARCH_ONLY_SCOPE)
    assert queries["Legislation"].endswith(orch._RESEARCH_ONLY_SCOPE)


def test_ordinary_drafting_query_is_not_scoped(monkeypatch):
    _, _, queries = _plan(monkeypatch, PLAIN, ("Drafting", ["Drafting"]), _intent("draft", "none"))
    assert orch._DOCUMENT_ONLY_SCOPE not in (queries.get("Drafting") or "")
