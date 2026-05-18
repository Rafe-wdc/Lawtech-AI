"""Live test: sample 10 random GST AAAR docs from ES, derive realistic prompts,
and exercise both routing (orchestrator classifier) and retrieval (gst_judgment_node).

Reports per-prompt:
- routed task (orchestrator)
- tools invoked by ReAct agent
- sources returned
- response length / error / first 200 chars
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.clients import get_es_client
from core.settings import ES_INDICES
from agents.gst_judgment import gst_judgment_node
from agents.orchestrator import _classify_and_plan


# ---------------- 1. Random sampling ----------------

def sample_docs(n: int = 10, seed: int = 17) -> list[dict]:
    """Pull n random docs from gst_judgements using ES random_score."""
    es = get_es_client()
    body = {
        "size": n,
        "query": {
            "function_score": {
                "query": {"match_all": {}},
                "random_score": {"seed": seed, "field": "_seq_no"},
            }
        },
        "_source": [
            "parties", "case_no", "judgment_date", "state_ut",
            "brief_of_order", "ar_order_no_date", "pdf_links",
        ],
    }
    res = es.search(index=ES_INDICES["gst_judgments"], body=body)
    return res["hits"]["hits"]


# ---------------- 2. Prompt synthesis ----------------

# Six prompt styles — round-robin across the 10 sampled docs so we
# exercise different tools (topic, party, state, case_no, follow-up).
PROMPT_TEMPLATES = [
    # 0: brief-of-order based topic search
    lambda d: f"What did the AAAR rule on the issue of '{(d.get('brief_of_order') or 'classification').split('.')[0][:120]}'? Cite relevant orders.",
    # 1: party name lookup
    lambda d: f"Find the GST AAAR appeal involving {d.get('parties', 'the applicant')[:80]}",
    # 2: state-filtered query
    lambda d: f"What are some {d.get('state_ut', 'state')} AAAR rulings on GST classification?",
    # 3: case number direct lookup
    lambda d: f"Look up GST AAAR order {d.get('case_no', '')}",
    # 4: combined state + topic
    lambda d: f"Any {d.get('state_ut', 'state')} GST appellate orders on the issue from the brief: {(d.get('brief_of_order') or 'classification')[:80]}",
    # 5: generic intent — should still route to GST_Judgment
    lambda d: f"Show me a GST advance ruling appeal where the issue was {(d.get('brief_of_order') or 'classification of goods')[:100]}",
]


def build_prompts(docs: list[dict]) -> list[tuple[str, dict, str]]:
    """Return list of (prompt, source_doc, template_label)."""
    out = []
    labels = [
        "topic-from-brief", "party-name", "state-filter",
        "case-number", "state+topic", "intent-generic",
    ]
    for i, hit in enumerate(docs):
        src = hit["_source"]
        tpl_idx = i % len(PROMPT_TEMPLATES)
        prompt = PROMPT_TEMPLATES[tpl_idx](src).strip()
        out.append((prompt, src, labels[tpl_idx]))
    return out


# ---------------- 3. Routing test ----------------

def test_routing(prompt: str) -> tuple[str, list[str]]:
    """Run the orchestrator classifier/planner and return (task, agents)."""
    try:
        task, agents = _classify_and_plan(prompt, chat_summary=None)
        return task, agents
    except Exception as e:
        return f"ERROR: {e}", []


# ---------------- 4. Retrieval test ----------------

async def test_agent(prompt: str) -> dict:
    """Invoke gst_judgment_node directly with a minimal state."""
    state = {
        "original_query": prompt,
        "query": prompt,
        "agent_queries": {},
        "user_context": "",
        "user_language": "en",
    }
    t0 = time.time()
    try:
        result = await gst_judgment_node(state)
        elapsed = time.time() - t0
        agent_result = result["agent_results"]["GST_Judgment"]
        return {
            "ok": True,
            "elapsed_s": round(elapsed, 1),
            "error": agent_result.error,
            "content_len": len(agent_result.content or ""),
            "content_preview": (agent_result.content or "")[:200].replace("\n", " "),
            "source_count": len(agent_result.sources),
            "first_source_party": agent_result.sources[0].parties if agent_result.sources else None,
            "first_source_state": agent_result.sources[0].state_ut if agent_result.sources else None,
        }
    except Exception as e:
        return {"ok": False, "elapsed_s": round(time.time() - t0, 1), "error": str(e)}


# ---------------- 5. Driver ----------------

async def main():
    print("Sampling 10 random docs from gst_judgements ...")
    docs = sample_docs(n=10)
    print(f"  got {len(docs)} docs\n")

    prompts = build_prompts(docs)

    print("=" * 90)
    print("ROUTING TEST (orchestrator classifier)")
    print("=" * 90)
    routing_results = []
    for i, (prompt, _src, label) in enumerate(prompts, 1):
        task, agents = test_routing(prompt)
        routing_results.append({"i": i, "label": label, "task": task, "agents": agents})
        ok = "OK " if task == "GST_Judgment" else "MISS"
        print(f"[{i:2d}] [{ok}] {label:18s}  task={task:14s} agents={agents}")
        print(f"     prompt: {prompt[:120]}")

    routing_ok = sum(1 for r in routing_results if r["task"] == "GST_Judgment")
    print(f"\nRouting score: {routing_ok}/10 prompts routed to GST_Judgment\n")

    print("=" * 90)
    print("AGENT TEST (gst_judgment_node end-to-end)")
    print("=" * 90)
    agent_results = []
    # Run sequentially — each ReAct call is expensive and concurrent runs would
    # blow rate limits / muddle progress logs
    for i, (prompt, src, label) in enumerate(prompts, 1):
        print(f"\n[{i:2d}] {label} — running...")
        print(f"     prompt: {prompt[:140]}")
        res = await test_agent(prompt)
        agent_results.append({"i": i, "label": label, **res})
        if res.get("ok"):
            print(f"     {res['elapsed_s']}s | {res['source_count']} sources | "
                  f"content_len={res['content_len']} | "
                  f"first_source={res.get('first_source_party')} ({res.get('first_source_state')})")
            if res.get("error"):
                print(f"     ERROR: {res['error']}")
            print(f"     preview: {res['content_preview']}")
        else:
            print(f"     FAILED in {res['elapsed_s']}s — {res['error']}")

    agent_ok = sum(1 for r in agent_results if r.get("ok") and r.get("source_count", 0) > 0)
    agent_no_err = sum(1 for r in agent_results if r.get("ok") and not r.get("error"))

    print("\n" + "=" * 90)
    print("SUMMARY")
    print("=" * 90)
    print(f"  Routing: {routing_ok}/10 went to GST_Judgment")
    print(f"  Agent:   {agent_no_err}/10 completed without error")
    print(f"  Sources: {agent_ok}/10 returned at least 1 source")

    # Dump JSON report
    out = Path(__file__).parent / "gst_live_test_report.json"
    out.write_text(json.dumps({
        "routing": routing_results,
        "agent": agent_results,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nFull report saved to: {out}")


if __name__ == "__main__":
    random.seed(17)
    asyncio.run(main())
