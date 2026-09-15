"""Quality audit 2026-09-15: bugs that restricted agents or degraded answers.

Each test pins one fix. Pure tests, no LLM, no network.

1. SCI topic search matched the WHOLE query as a phrase (MUST) -> 0 hits on
   every topic query; live check: 0 / 0 / 0 hits vs 4033 / 1074 / 10000.
2. Regular-bail overlay named 480 BNSS as the Sessions/HC provision (it is
   483) and 478 as the Magistrate's (it is 480); base prompt example told the
   writer to cite 480 for anticipatory bail (482).
3. Merge/synthesis clipped every agent at 12K/8K chars (SCI answers are
   17-21K; 10 of 12 citations sat past the cut) and pasted a literal
   "[... truncated for merge]" marker into the model input.
4. Supporting-agent dedup tokenised with [A-Za-z] only, so a Hindi answer's
   prose was invisible and only shared English anchors counted.
5. The post-merge critic audited under the extractor's language while the
   merge was written in the resolved user_language.
6. The Newacts veto could remove the Legislation primary while task still
   said "Legislation" -> anchor-less generic synthesis.
7. Non_legal's hard-coded greeting carried error= -> valid_results empty ->
   "hello" went to the web-search last resort.
8. Constitution/Maxim retrieved on context+question, which the ES sanitiser
   cuts at 500 chars: the question never reached Elasticsearch.
9. Degraded answers (a planned agent errored) were cached for an hour.
10. Drafting: the repair pass regenerated Prayer/Verification without the
    niche overlay; the refiner's full re-emission shipped with no mechanical
    cleanup afterwards.
11. self_refine's verifier-unavailable fallback rewrite had no shrink floor.
"""
from __future__ import annotations

import inspect
import re

import pytest


# ---------------------------------------------------------------- 1. SCI search
class _FakeES:
    def __init__(self):
        self.bodies = []

    def search(self, index=None, body=None, **kw):
        self.bodies.append(body)
        return {"hits": {"total": {"value": 0}, "hits": []}}


def test_sci_topic_search_uses_match_as_must_and_phrase_as_boost(monkeypatch):
    import tools.shared.sci_judgment_tools as m
    fake = _FakeES()
    monkeypatch.setattr(m, "get_es_client", lambda: fake)
    m.search_by_topic.invoke({"query": "key Supreme Court principles on anticipatory bail under Section 438 CrPC"})
    assert fake.bodies, "no ES call made"
    q = fake.bodies[0]["query"]["bool"]
    must = q["must"]
    assert len(must) == 1
    mm = must[0]["multi_match"]
    assert mm["type"] != "phrase", "phrase MUST over the whole query returns 0 hits on topic queries"
    assert mm.get("minimum_should_match"), "match MUST needs a term floor"
    types = [c["multi_match"]["type"] for c in q["should"]]
    assert "phrase" in types, "phrase proximity must stay as a SHOULD boost"


# ------------------------------------------------------------ 2. bail overlay
def test_regular_bail_overlay_maps_bnss_sections_correctly():
    from config.drafting_niches import BAIL_APPLICATION_REGULAR as O
    assert "Section 483 BNSS" in O and "erstwhile Section 439 CrPC" in O
    assert "Section 480 BNSS (bail powers of Court of Session / High Court)" not in O
    assert "Magistrate's court: Section 480 BNSS" in O
    assert "Bailable offences: Section 478 BNSS" in O


def test_anticipatory_bail_example_and_notice_section():
    from config.prompts import DRAFTING_SYSTEM_PROMPT as P
    from config import drafting_niches as N
    src = inspect.getsource(N)
    assert '"cite Section 480 BNSS as' not in P
    assert "notice u/s 179 BNSS" not in src           # 179 BNSS is attendance of witnesses
    assert "35(3) BNSS" in src


def test_other_overlay_mappings_corrected():
    from config import drafting_niches as N
    src = inspect.getsource(N)
    assert "Sections 210, 227 BNSS / former 190, 204 CrPC" in src
    assert "Section 297 BNSS (formerly Section 297 CrPC)" not in src and "Section 336 BNSS" in src
    assert "Section 149 MV Act (insurer" not in src and "Section 150 MV Act" in src
    assert "+50% for age <40 as future prospects for a permanent employee" in src


# ------------------------------------------------------- 3. clip and marker
def test_agent_content_caps_cover_a_full_sci_answer():
    import agents.orchestrator as orch
    src = inspect.getsource(orch)
    caps = [int(x) for x in re.findall(r"_MAX_AGENT_CONTENT = (\d+)", src)]
    assert caps and all(c >= 21000 for c in caps), caps


def test_truncation_marker_is_removed_from_output():
    from agents.orchestrator import _CLIP_MARKER_RE
    text = "## Answer\n\nBody.\n\n[... truncated for merge]\n\nMore.\n[... truncated for synthesis]"
    out = _CLIP_MARKER_RE.sub("", text)
    assert "truncated for" not in out and "Body." in out and "More." in out


def test_merge_and_synthesis_outputs_are_marker_stripped():
    import agents.orchestrator as orch
    src = inspect.getsource(orch)
    assert 'merged = _CLIP_MARKER_RE.sub("", response.content)' in src
    assert 'synthesized = _CLIP_MARKER_RE.sub("", synthesized)' in src


# ---------------------------------------------------------- 4. dedup tokens
def test_dedup_token_set_sees_hindi_prose_and_ignores_urls():
    from agents.orchestrator import _content_token_set
    hindi = "धारा 498ए के अंतर्गत गिरफ्तारी से पहले पुलिस को अर्नेश कुमार दिशानिर्देशों का पालन करना होगा https://api.sci.gov.in/x.pdf"
    toks = _content_token_set(hindi)
    assert any(re.match(r"[ऀ-ॿ]", t) for t in toks), toks
    assert not any("sci.gov" in t or "https" in t for t in toks)
    assert "498" in toks


# ------------------------------------------------------- 5. audit language
class _Intent:
    def __init__(self, language):
        self.language = language

    def model_copy(self, update):
        return _Intent(update.get("language", self.language))


def test_audit_intent_follows_resolved_user_language():
    from agents.orchestrator import _audit_intent
    assert _audit_intent(None, "hi") is None
    same = _Intent("hi")
    assert _audit_intent(same, "hi") is same
    fixed = _audit_intent(_Intent("en"), "hi")
    assert fixed.language == "hi"


def test_both_post_merge_refines_use_audit_intent():
    import agents.orchestrator as orch
    src = inspect.getsource(orch)
    assert src.count('_audit_intent(state.get("user_intent"), user_language)') == 2


# --------------------------------------------------------- 6. veto primary
def test_newacts_veto_moves_primary_task():
    import agents.orchestrator as orch
    src = inspect.getsource(orch.orchestrator_plan_node)
    assert 'if task == "Legislation" and "Legislation" not in tasks_planned and "Newacts" in tasks_planned' in src
    assert 'task = "Newacts"' in src


# ---------------------------------------------------- 7. non_legal fallback
def test_non_legal_fallback_greeting_is_not_an_error():
    import agents.non_legal as nl
    src = inspect.getsource(nl)
    block = src[src.index("Non-legal agent failed"):]
    block = block[block.index("AgentResult("):]
    block = block[:block.index("\n        )")]
    assert "fallback_used=True" in block
    assert "error=" not in block


# ---------------------------------------------- 8. constitution retrieval
def test_constitution_and_maxim_retrieve_on_the_question():
    import agents.constitution_maxim as cm
    sig = inspect.signature(cm._handle_constitution_or_maxim)
    assert "retrieval_query" in sig.parameters
    src = inspect.getsource(cm)
    assert "_retrieve_from_es, task, retrieval_query or query" in src
    assert src.count("retrieval_query=query") == 2


# ------------------------------------------------------ 9. cache predicate
def test_degraded_answers_are_not_cacheable():
    import core.gateway as gw
    import core.chat_runner as cr
    g = inspect.getsource(gw)
    c = inspect.getsource(cr)
    assert "and not _degraded" in g and 'getattr(r, "error", None)' in g
    assert "and not any_agent_errored" in c and "any_agent_errored = True" in c


# ------------------------------------------------- 10. drafting repair path
def test_repair_pass_receives_niche_overlay_and_refined_draft_is_recleaned():
    import agents.drafting as d
    src = inspect.getsource(d)
    repair = src[src.index("section_position_start=total - len(missing) + j + 1"):]
    repair = repair[:repair.index(")\n")]
    assert "niche_overlay=niche_overlay" in repair
    tail = src[src.index("draft = refined_draft"):]
    tail = tail[:tail.index("except Exception as refine_err")]
    assert "validate_draft(draft)" in tail and "correct_cross_pairs" in tail


# -------------------------------------------- 11. verifier-unavailable floor
def test_verifier_unavailable_fallback_refine_has_shrink_floor():
    import core.self_refine as sr
    src = inspect.getsource(sr.self_refine)
    block = src[src.index("_critique_is_verifier_unavailable(critique)"):]
    block = block[:block.index("return current, history")]
    assert "len(fallback) >= 0.85 * len(current)" in block
