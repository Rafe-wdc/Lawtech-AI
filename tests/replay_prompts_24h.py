"""Replay a stratified sample of real prod prompts against a Lawtech-AI server
and flag known response-quality signatures.

Input:  a JSONL export of prompts, one object per line with keys `i` (index)
        and `t` (prompt text) — the shape of `_prompts_24h.jsonl`.
Output: one folder per prompt under --out, plus `summary.csv` and `sample.json`.

    <out>/<NNN>_<category>/
        prompt.txt      the prompt exactly as sent
        events.jsonl    every SSE event, with ms offset from first byte
        answer.md       the final response text (`response` event, else
                        concatenated `token` events)
        meta.json       agents, token_usage, cost, timings, request id,
                        detected flags

Usage (run ON the dev box — its nginx blocks /pyapi from outside):

    export LAWTECH_API_KEY=...
    python tests/replay_prompts_24h.py \
        --input _prompts_24h.jsonl \
        --base http://localhost:5000/pyapi \
        --out _replay_24h_out \
        --n 40

    # preview the sample without sending anything
    python tests/replay_prompts_24h.py --input _prompts_24h.jsonl --dry-run

Then copy back `<out>/` and the matching window of `logs/agent.log`; the
request ids in meta.json join the two.

Sampling: prompts are bucketed by regex into categories mirroring the
2026-09-12 audit (drafting, translation, case law, statute, long pasted
document, cold follow-up, control). Prompts that reference an attachment or
carry internal pipeline markers are excluded — they cannot be replayed
faithfully without the file / thread. A fixed seed makes the sample stable
across runs so before/after comparisons line up.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import re
import sys
import time
import unicodedata
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover
    print("pip install httpx", file=sys.stderr)
    raise

# ---------------------------------------------------------------------------
# Categorisation
# ---------------------------------------------------------------------------

_RE = {
    "attachment": re.compile(
        r"attach|enclos|upload|\bpdf\b|the notice|the fir\b|the plaint|the affidavit|"
        r"संलग्न|जोडलेल|attched|attch", re.I),
    "internal": re.compile(
        r"PRIOR DRAFT \(the text|SEARCH followups|DIRECTIVE followups|\[cite:", re.I),
    "translate": re.compile(
        r"translat|tarnslet|tranlate|convert .{0,40}(hindi|marathi|english|gujarati|"
        r"tamil|telugu|kannada|bengali|urdu)|अनुवाद|भाषांतर|tarjuma", re.I),
    # a drafting VERB — the prompt asks us to produce a document
    "draft_verb": re.compile(
        r"\bdraft|\bdrat\b|मसौदा|तयार कर|बनाओ|बनाना|banao|banana|banaye|prepare|"
        r"\bformat\b|\bformatt\b|\bwrite (a|an|the)\b|^\s*make\b|\bmake (a|an|the|me)\b|"
        r"correct the|improve the|तैयार|लिख", re.I),
    # question-shaped prompt: a document noun alone does not make it drafting
    "question": re.compile(
        r"^\s*(what|how|whether|can|could|is|are|does|do|why|which|when|who|any)\b|\?\s*$",
        re.I),
    # a document NOUN — weaker signal, used only when no case-law ask is present
    "draft_noun": re.compile(
        r"application|petition|notice|agreement|affidavit|शपथ|अर्ज|याचिका|reply|"
        r"rejoinder|written statement|counter|plaint|complaint|deed|undertaking", re.I),
    # referential words that mark a turn as depending on earlier context
    "referential": re.compile(
        r"\b(above|this|that|it|same|previous|said|earlier|again|more)\b|उपरोक्त|"
        r"वरील|यह|हे|उक्त|^(convert|translate|tranlate|tarnslet|make it|shorten|"
        r"expand|elaborate|add|remove|change)\b", re.I),
    "caselaw": re.compile(
        r"judg|case ?law|precedent|supreme court|high court|ruling|citation|"
        r"\bINSC\b|\bSCC\b|निर्णय|फैसला|न्यायालय", re.I),
    "statute": re.compile(
        r"\bsection\b|\bsec\.?\s*\d|धारा|कलम|\bipc\b|\bbns\b|\bcrpc\b|\bbnss\b|"
        r"\bbsa\b|\biea\b|article \d|\bact\b", re.I),
}

CATEGORY_ORDER = [
    "drafting", "translate", "caselaw", "statute", "long_doc",
    "cold_followup", "control",
]

# target counts per bucket for --n 40; scaled proportionally for other n
_TARGET_40 = {
    "drafting": 10, "translate": 6, "caselaw": 7, "statute": 5,
    "long_doc": 4, "cold_followup": 4, "control": 4,
}


def categorise(t: str) -> str | None:
    """Return the sampling bucket for a prompt, or None to exclude it."""
    if _RE["internal"].search(t):
        return None
    if _RE["attachment"].search(t):
        return None
    if len(t) > 8000:
        return "long_doc"
    # a short turn that leans on earlier context ("Convert above in english")
    if len(t) < 60 and _RE["referential"].search(t):
        return "cold_followup"
    if _RE["translate"].search(t):
        return "translate"
    has_verb = bool(_RE["draft_verb"].search(t))
    has_noun = bool(_RE["draft_noun"].search(t))
    asks_caselaw = bool(_RE["caselaw"].search(t))
    if has_verb:
        return "drafting"
    if asks_caselaw:
        return "caselaw"
    if has_noun and not _RE["question"].search(t):
        return "drafting"
    if _RE["statute"].search(t):
        return "statute"
    return "control"


def dominant_script(t: str) -> str:
    counts: dict[str, int] = {}
    for c in t:
        if not c.isalpha():
            continue
        try:
            name = unicodedata.name(c).split()[0]
        except ValueError:
            continue
        counts[name] = counts.get(name, 0) + 1
    if not counts:
        return "none"
    return max(counts, key=counts.get).lower()


def wants_regional(t: str) -> str | None:
    """Return the requested output language when the prompt names one."""
    m = re.search(
        r"\b(in|into|to)\s+(hindi|marathi|gujarati|tamil|telugu|kannada|"
        r"malayalam|bengali|punjabi|odia|urdu)\b", t, re.I)
    if m:
        return m.group(2).lower()
    if re.search(r"मराठी(त|मध्ये)|marathi madhe|marathit", t, re.I):
        return "marathi"
    if re.search(r"हिंदी में|hindi me\b", t, re.I):
        return "hindi"
    if dominant_script(t) == "devanagari" and re.search(r"marathi|मराठी", t, re.I):
        return "marathi"
    return None


_SCRIPT_FOR_LANG = {
    "hindi": "devanagari", "marathi": "devanagari", "gujarati": "gujarati",
    "tamil": "tamil", "telugu": "telugu", "kannada": "kannada",
    "malayalam": "malayalam", "bengali": "bengali", "punjabi": "gurmukhi",
    "odia": "oriya", "urdu": "arabic",
}


def script_share(t: str, script: str) -> float:
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return 0.0
    hit = 0
    for c in letters:
        try:
            if unicodedata.name(c).split()[0].lower() == script:
                hit += 1
        except ValueError:
            pass
    return hit / len(letters)


# ---------------------------------------------------------------------------
# Failure signatures (mirror the 2026-09-12 audit categories)
# ---------------------------------------------------------------------------

_FUTURE_CITE = re.compile(
    r"\b202[6-9]\s*(?:INSC\s*\d+|SCC\s+OnLine\s+[A-Za-z]+\s+\d+|:[A-Z]{2,5}HC:\s*\d+)",
    re.I)


def detect_flags(prompt: str, answer: str, events: list[dict]) -> list[str]:
    flags: list[str] = []
    a = answer or ""
    if "Draft incomplete" in a or any(e.get("type") == "draft_incomplete" for e in events):
        flags.append("draft_incomplete_banner")
    if re.search(r"Verify (the case law )?before (filing|relying)", a):
        flags.append("leaked_verify_warning")
    if "judgment date(s) in the future" in a or "UNVERIFIABLE CITATION" in a:
        flags.append("leaked_provenance_warning")
    if "I was unable to retrieve information" in a:
        flags.append("leaked_fallback_apology")
    if "I was unable to read the uploaded file" in a:
        flags.append("no_document_path")
    if re.search(r"\[cite:\s*\d", a, re.I) or re.search(r"\[CITE:", a):
        flags.append("leaked_cite_marker")
    # future-dated citations that the model introduced (not ones the user
    # pasted in — a pasted 2026 judgment legitimately cites itself)
    introduced = [m.group(0) for m in _FUTURE_CITE.finditer(a)
                  if m.group(0) not in prompt]
    if introduced:
        flags.append(f"future_dated_citation_introduced:{len(introduced)}")
    if any(e.get("type") == "error" for e in events):
        flags.append("sse_error_event")
    if not a.strip():
        flags.append("empty_answer")

    # regional-language request answered in the wrong script
    lang = wants_regional(prompt)
    if lang and a.strip():
        want = _SCRIPT_FOR_LANG.get(lang)
        if want and script_share(a, want) < 0.6:
            flags.append(f"off_target_language:{lang}:{script_share(a, want):.2f}")

    # translation / conversion much shorter than its source
    if re.search(r"translat|convert|tarnslet|tranlate", prompt, re.I) and len(prompt) > 1500:
        if len(a) < 0.5 * len(prompt):
            flags.append(f"conversion_truncated:{len(a)}/{len(prompt)}")

    # duplicated heading blocks
    heads = re.findall(r"^#{1,3}\s+(.+)$", a, re.M)
    norm = [re.sub(r"\W+", " ", h).strip().lower() for h in heads]
    dup = {h for h in norm if norm.count(h) > 1 and len(h) > 3}
    if dup:
        flags.append(f"repeated_headings:{len(dup)}")
    return flags


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def load_prompts(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            out.append({"i": str(d.get("i")), "t": d["t"]})
    return out


def build_sample(prompts: list[dict], n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    buckets: dict[str, list[dict]] = {k: [] for k in CATEGORY_ORDER}
    seen: set[str] = set()
    excluded = 0
    for p in prompts:
        if p["t"] in seen:
            continue
        seen.add(p["t"])
        cat = categorise(p["t"])
        if cat is None:
            excluded += 1
            continue
        buckets[cat].append({**p, "category": cat})
    scale = n / 40.0
    sample: list[dict] = []
    for cat in CATEGORY_ORDER:
        want = max(1, round(_TARGET_40[cat] * scale))
        pool = buckets[cat]
        rng.shuffle(pool)
        sample.extend(pool[:want])
    print(f"[sample] {len(prompts)} prompts, {len(seen)} unique, "
          f"{excluded} excluded (attachment / internal marker)")
    for cat in CATEGORY_ORDER:
        got = sum(1 for s in sample if s["category"] == cat)
        print(f"[sample]   {cat:14s} pool={len(buckets[cat]):4d}  picked={got}")
    return sample[:n]


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

async def run_one(client: httpx.AsyncClient, base: str, api_key: str,
                  prompt: str, timeout: float) -> tuple[list[dict], dict]:
    events: list[dict] = []
    meta: dict = {"http_status": None, "request_id": None, "error": None}
    t0 = time.perf_counter()
    first_byte_ms = None
    try:
        async with client.stream(
            "POST", f"{base}/search/stream",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json={"Promptquery": prompt},
            timeout=timeout,
        ) as resp:
            meta["http_status"] = resp.status_code
            meta["request_id"] = (resp.headers.get("x-request-id")
                                  or resp.headers.get("x-req-id"))
            if resp.status_code != 200:
                meta["error"] = (await resp.aread()).decode("utf-8", "replace")[:500]
                return events, meta
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                now_ms = (time.perf_counter() - t0) * 1000
                if first_byte_ms is None:
                    first_byte_ms = now_ms
                try:
                    evt = json.loads(line[6:])
                except Exception:
                    continue
                evt["_ms"] = round(now_ms)
                events.append(evt)
    except Exception as e:  # network / timeout
        meta["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    meta["first_byte_ms"] = round(first_byte_ms) if first_byte_ms else None
    meta["total_ms"] = round((time.perf_counter() - t0) * 1000)
    # The gateway does not echo a request-id header. For /search/stream it
    # sets the log request id to the first 8 chars of the thread id
    # (core/gateway.py: set_request_id(thread_id[:8])), so derive it from the
    # thread_id event — that is the key that joins meta.json to agent.log.
    if meta.get("request_id") is None:
        for e in events:
            if e.get("type") == "thread_id" and e.get("data"):
                meta["request_id"] = str(e["data"])[:8]
                break
    return events, meta


def assemble(events: list[dict]) -> tuple[str, dict]:
    answer = ""
    info: dict = {"agents_planned": None, "agents_used": None,
                  "token_usage": None, "thread_id": None,
                  "query_rewritten": None, "effective_query": None,
                  "cached": False, "event_counts": {}}
    tokens: list[str] = []
    for e in events:
        et = e.get("type")
        info["event_counts"][et] = info["event_counts"].get(et, 0) + 1
        if et == "token":
            tokens.append(e.get("content", ""))
        elif et == "token_reset":
            tokens = []
        elif et == "response":
            answer = e.get("content", "") or answer
        elif et == "agents_planned":
            info["agents_planned"] = e.get("agents")
        elif et == "thread_id":
            info["thread_id"] = e.get("data")
        elif et == "done":
            info["agents_used"] = e.get("agents_used")
            info["token_usage"] = e.get("token_usage")
            info["query_rewritten"] = e.get("query_rewritten")
            info["effective_query"] = e.get("effective_query")
            info["cached"] = bool(e.get("cached"))
            info["thread_id"] = e.get("thread_id") or info["thread_id"]
    if not answer:
        answer = "".join(tokens)
    return answer, info


async def replay(sample: list[dict], base: str, api_key: str, out: Path,
                 timeout: float, concurrency: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "sample.json").write_text(
        json.dumps(sample, ensure_ascii=False, indent=1), encoding="utf-8")
    sem = asyncio.Semaphore(concurrency)
    rows: list[dict] = []

    async with httpx.AsyncClient() as client:
        async def worker(idx: int, s: dict) -> None:
            async with sem:
                folder = out / f"{idx:03d}_{s['category']}"
                folder.mkdir(exist_ok=True)
                (folder / "prompt.txt").write_text(s["t"], encoding="utf-8")
                print(f"[{idx:03d}] {s['category']:13s} {s['t'][:60]!r}", flush=True)
                events, meta = await run_one(client, base, api_key, s["t"], timeout)
                answer, info = assemble(events)
                flags = detect_flags(s["t"], answer, events)
                with (folder / "events.jsonl").open("w", encoding="utf-8") as fh:
                    for e in events:
                        fh.write(json.dumps(e, ensure_ascii=False) + "\n")
                (folder / "answer.md").write_text(answer, encoding="utf-8")
                tu = info.get("token_usage") or {}
                meta_out = {
                    "source_index": s["i"], "category": s["category"],
                    "prompt_chars": len(s["t"]), "answer_chars": len(answer),
                    "prompt_script": dominant_script(s["t"]),
                    "answer_script": dominant_script(answer),
                    "requested_language": wants_regional(s["t"]),
                    **meta, **info,
                    "total_tokens": tu.get("total_tokens"),
                    "cost_usd": tu.get("cost_usd"),
                    "flags": flags,
                }
                (folder / "meta.json").write_text(
                    json.dumps(meta_out, ensure_ascii=False, indent=1), encoding="utf-8")
                rows.append({
                    "idx": idx, "source_index": s["i"], "category": s["category"],
                    "http": meta["http_status"], "error": meta["error"] or "",
                    "agents": ",".join(info.get("agents_used") or info.get("agents_planned") or []),
                    "first_byte_ms": meta.get("first_byte_ms"),
                    "total_ms": meta.get("total_ms"),
                    "prompt_chars": len(s["t"]), "answer_chars": len(answer),
                    "total_tokens": tu.get("total_tokens"),
                    "cost_usd": tu.get("cost_usd"),
                    "thread_id": info.get("thread_id"),
                    "flags": ";".join(flags),
                    "prompt_head": s["t"][:80].replace("\n", " "),
                })
                status = "FLAGS: " + ", ".join(flags) if flags else "clean"
                print(f"[{idx:03d}]   -> {meta.get('total_ms')} ms, "
                      f"{len(answer)} chars, {status}", flush=True)

        await asyncio.gather(*(worker(i, s) for i, s in enumerate(sample)))

    rows.sort(key=lambda r: r["idx"])
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    flagged = [r for r in rows if r["flags"]]
    print(f"\n[done] {len(rows)} prompts, {len(flagged)} flagged, "
          f"summary at {out / 'summary.csv'}")
    counts: dict[str, int] = {}
    for r in flagged:
        for f in r["flags"].split(";"):
            key = f.split(":")[0]
            counts[key] = counts.get(key, 0) + 1
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"[done]   {k:32s} {v}")


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="JSONL with {i, t} rows")
    ap.add_argument("--base", default="http://localhost:5000/pyapi",
                    help="API base, e.g. http://localhost:5000/pyapi")
    ap.add_argument("--out", default="_replay_24h_out")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20260915)
    ap.add_argument("--timeout", type=float, default=360.0)
    ap.add_argument("--concurrency", type=int, default=2,
                    help="parallel requests; keep low, drafting holds a semaphore of 3 per worker")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the sample and exit without sending anything")
    args = ap.parse_args()

    prompts = load_prompts(Path(args.input))
    sample = build_sample(prompts, args.n, args.seed)
    if args.dry_run:
        for i, s in enumerate(sample):
            print(f"{i:03d} {s['category']:13s} {len(s['t']):6d}  {s['t'][:70]!r}")
        return 0

    api_key = os.environ.get("LAWTECH_API_KEY")
    if not api_key:
        print("LAWTECH_API_KEY is not set", file=sys.stderr)
        return 2
    asyncio.run(replay(sample, args.base.rstrip("/"), api_key, Path(args.out),
                       args.timeout, args.concurrency))
    return 0


if __name__ == "__main__":
    sys.exit(main())
