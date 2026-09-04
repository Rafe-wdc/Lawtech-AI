"""A/B the REAL routing prompts across model candidates.

Runs CLASSIFY_AND_PLAN_PROMPT and USER_INTENT_EXTRACTION_PROMPT (verbatim,
with the real Pydantic schemas) against a labelled set of Indian-legal
queries on the current model vs Gemini 3.x candidates.
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, r"c:\Users\Admin\Downloads\Lawtech-AI\Lawtech-AI")
os.chdir(r"c:\Users\Admin\Downloads\Lawtech-AI\Lawtech-AI")

from dotenv import load_dotenv
load_dotenv(".env")

from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate

from agents.orchestrator import CLASSIFY_AND_PLAN_PROMPT, ClassifyAndPlan, QueryAnalysisV2
from config.prompts import USER_INTENT_EXTRACTION_PROMPT, wrap_untrusted

# (query, expected_task, note)
CASES = [
    ("draft a bail application under BNSS for my client arrested in a cheque bounce case",
     "Drafting", "core drafting"),
    ("What does Section 138 of the Negotiable Instruments Act say?",
     "Legislation", "statute text"),
    ("Supreme Court judgments on anticipatory bail in dowry cases",
     "SCI_Judgment", "SCI-specific"),
    ("Is GST applicable on notice pay recovery? Any AAAR rulings on this?",
     "GST_Judgment", "GST-specific"),
    ("What is Article 21 of the Constitution?",
     "Constitution", "constitution"),
    ("What does the maxim audi alteram partem mean?",
     "Maxim", "maxim"),
    ("My landlord has not returned my security deposit of Rs 2 lakh after 8 months "
     "despite repeated reminders. What are my legal options?",
     "Scenario", "fact-pattern analysis"),
    ("hi, how are you doing today?",
     "Non_legal", "greeting"),
    ("What are the key changes in the Bharatiya Nyaya Sanhita compared to the IPC?",
     "Newacts", "new acts"),
    ("मुझे भारतीय दंड संहिता की धारा 498A के बारे में विस्तार से बताइए",
     "Legislation", "Hindi statute query"),
    ("Draft a legal notice to my employer for unpaid salary of 4 months and cite "
     "the relevant case law supporting my claim",
     "Drafting", "multi-intent: draft + case law"),
    ("Compare Section 498A IPC with its BNS equivalent in a table",
     "Newacts", "table format directive"),
    ("What is the punishment for cheque bounce and give me recent High Court "
     "judgments on it",
     "Legislation", "multi-intent: statute + judgments"),
    ("Explain the doctrine of basic structure in brief, in Hindi",
     "Constitution", "language directive + depth"),
    ("summarize the key obligations in the attached agreement",
     "Document", "attachment reference"),
]

# (label, model_id, kwargs)
MODELS = [
    ("2.5-flash-lite (current)", "gemini-2.5-flash-lite",
     dict(temperature=0.1, thinking_budget=0)),
    ("3.5-flash-lite minimal", "gemini-3.5-flash-lite",
     dict(temperature=1.0, thinking_level="minimal")),
    ("3.5-flash-lite low", "gemini-3.5-flash-lite",
     dict(temperature=1.0, thinking_level="low")),
    ("3.8-flash low", "gemini-3.8-flash",
     dict(temperature=1.0, thinking_level="low")),
]


def make_llm(model_id, kwargs, schema):
    return init_chat_model(
        f"google_genai:{model_id}", max_output_tokens=4096,
        max_retries=1, timeout=90, **kwargs,
    ).with_structured_output(schema, include_raw=True)


def run_classify(label, model_id, kwargs, case):
    query, expected, note = case
    t0 = time.time()
    try:
        chain = ChatPromptTemplate.from_template(CLASSIFY_AND_PLAN_PROMPT) | \
            make_llm(model_id, kwargs, ClassifyAndPlan)
        r = chain.invoke({"query": wrap_untrusted(query),
                          "chat_summary": wrap_untrusted("")})
        p = r["parsed"]
        if p is None:
            return dict(model=label, q=note, ok=False,
                        err=f"parse_fail: {str(r.get('parsing_error'))[:80]}",
                        secs=round(time.time() - t0, 1))
        return dict(model=label, q=note, ok=True, task=p.task,
                    agents=p.agents, expected=expected,
                    match=(p.task == expected), secs=round(time.time() - t0, 1))
    except Exception as e:
        return dict(model=label, q=note, ok=False,
                    err=f"{type(e).__name__}: {str(e)[:90]}",
                    secs=round(time.time() - t0, 1))


def run_intent(label, model_id, kwargs, case):
    query, expected, note = case
    t0 = time.time()
    try:
        chain = ChatPromptTemplate.from_template(USER_INTENT_EXTRACTION_PROMPT) | \
            make_llm(model_id, kwargs, QueryAnalysisV2)
        r = chain.invoke({
            "query": wrap_untrusted(query),
            "chat_summary": wrap_untrusted(""),
            "previous_intent_hint": "(no previous turn — this is a fresh conversation)",
        })
        p = r["parsed"]
        if p is None:
            return dict(model=label, q=note, ok=False,
                        err=f"parse_fail: {str(r.get('parsing_error'))[:80]}",
                        secs=round(time.time() - t0, 1))
        i = getattr(p, "intent", p)
        return dict(model=label, q=note, ok=True,
                    fmt=str(getattr(getattr(i, "response_format", ""), "value", "")),
                    lang=getattr(i, "language", ""),
                    task_intent=getattr(i, "task_intent", ""),
                    depth=str(getattr(getattr(i, "response_depth", ""), "value",
                                      getattr(i, "response_depth", ""))),
                    sci=getattr(i, "wants_supreme_court", None),
                    gst=getattr(i, "wants_gst_rulings", None),
                    caselaw=getattr(i, "include_case_law", None),
                    conf=getattr(i, "confidence", None),
                    secs=round(time.time() - t0, 1))
    except Exception as e:
        return dict(model=label, q=note, ok=False,
                    err=f"{type(e).__name__}: {str(e)[:90]}",
                    secs=round(time.time() - t0, 1))


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "classify"
    fn = run_classify if which == "classify" else run_intent
    jobs = [(lbl, mid, kw, c) for (lbl, mid, kw) in MODELS for c in CASES]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda a: fn(*a), jobs))
    out = rf"C:\Users\Admin\AppData\Local\Temp\claude\c--Users-Admin-Downloads-Lawtech-AI\ff30d7af-8feb-4e5c-b170-1f7641683f7d\scratchpad\{which}_results.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, ensure_ascii=False, default=str)
    print("WROTE", out, len(results))
