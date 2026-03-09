"""End-to-end test: start embedding service + API server, then run all 27 prompts.

Usage:
    python tests/test_with_embedding_service.py [--workers N] [--embedding-port PORT] [--api-port PORT]

Defaults:
    --workers 4       (API server workers)
    --embedding-port 5200
    --api-port 5050

This script:
1. Starts the embedding microservice (single process, port 5200)
2. Waits for the retriever model to be loaded (warm-up call)
3. Starts the API server with N workers (port 5050) with EMBEDDING_SERVICE_URL set
4. Runs all 27 test prompts
5. Generates markdown + JSON report
6. Shuts down both services
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime

import requests

# ── Test Prompts ─────────────────────────────────────────────────────────────

PROMPTS = [
    # --- Newacts: Single Section ---
    (1,  "Section 302 of IPC", "Newacts-Single", "Newacts"),
    (2,  "Section 35 of BNS", "Newacts-Single", "Newacts"),
    (3,  "What is Section 438 of CrPC?", "Newacts-Single", "Newacts"),
    # --- Newacts: Multi Section ---
    (4,  "Sections 302 and 307 of IPC", "Newacts-Multi", "Newacts"),
    (5,  "Explain Sections 64, 65 and 66 of BSA", "Newacts-Multi", "Newacts"),
    (6,  "Compare Section 154 and Section 161 of CrPC", "Newacts-Multi", "Newacts"),
    # --- Newacts: Topic ---
    (7,  "punishment for theft in BNS", "Newacts-Topic", "Newacts"),
    (8,  "bail provisions under BNSS", "Newacts-Topic", "Newacts"),
    (9,  "electronic evidence rules in BSA", "Newacts-Topic", "Newacts"),
    # --- Newacts: Nearby / Subsection ---
    (10, "What does Section 100 of BNS say?", "Newacts-Nearby", "Newacts"),
    (11, "Section 528 of BNSS", "Newacts-Nearby", "Newacts"),
    (12, "Section 3(5) of Bharatiya Nyaya Sanhita", "Newacts-Subsection", "Newacts"),
    (13, "IEA Section 65b", "Newacts-Subsection", "Newacts"),
    # --- Newacts: Mapping ---
    (14, "What is the equivalent of Section 498a of IPC in BNS?", "Newacts-Mapping", "Newacts"),
    (15, "Section 125 CrPC new law equivalent", "Newacts-Mapping", "Newacts"),
    # --- Legislation: Single ---
    (16, "Section 138 of Negotiable Instruments Act", "Legislation-Single", "Legislation"),
    (17, "Section 9 of Arbitration Act", "Legislation-Single", "Legislation"),
    # --- Legislation: Multi ---
    (18, "Sections 44 and 45 of Transfer of Property Act", "Legislation-Multi", "Legislation"),
    (19, "Explain Sections 3, 4 and 5 of Consumer Protection Act", "Legislation-Multi", "Legislation"),
    # --- Legislation: Range ---
    (20, "Sections 10 to 15 of Companies Act", "Legislation-Range", "Legislation"),
    # --- Legislation: Topic ---
    (21, "director duties under companies act", "Legislation-Topic", "Legislation"),
    (22, "tenant rights in rent control legislation", "Legislation-Topic", "Legislation"),
    # --- Legislation: Subsection ---
    (23, "Section 138(1) of Negotiable Instruments Act", "Legislation-Subsection", "Legislation"),
    # --- Edge Cases ---
    (24, "What comes after section 35 of BNS?", "Edge-Nearby", "Newacts"),
    (25, "theft", "Edge-Vague", None),
    (26, "Rule 3 of Maharashtra Rent Control Rules", "Edge-NonSection", "Legislation"),
    (27, "Sections 302, 304, 304a, 307 and 376 of IPC", "Edge-ManySection", "Newacts"),
]

SORRY_PATTERNS = [
    r"(?i)i\s+(am\s+)?sorry",
    r"(?i)cannot\s+find",
    r"(?i)could\s+not\s+find",
    r"(?i)no\s+(relevant\s+)?information\s+found",
    r"(?i)unable\s+to\s+(find|locate|retrieve)",
    r"(?i)don'?t\s+have\s+(any\s+)?information",
    r"(?i)no\s+results?\s+found",
    r"(?i)not\s+available\s+in",
]


def is_apologetic(text: str) -> bool:
    if not text or len(text.strip()) < 30:
        return True
    for pat in SORRY_PATTERNS:
        if re.search(pat, text[:500]):
            return True
    return False


def classify_result(resp_json: dict, expected_agent: str | None) -> str:
    result_text = resp_json.get("result", "")
    agents_used = resp_json.get("agents_used", [])
    if expected_agent and expected_agent not in agents_used:
        return "FAIL"
    if is_apologetic(result_text):
        return "WEAK"
    return "PASS"


# ── Service Management ───────────────────────────────────────────────────────

def start_embedding_service(port: int, project_root: str) -> subprocess.Popen:
    """Start the embedding microservice."""
    env = os.environ.copy()
    env["PYTHONPATH"] = project_root
    proc = subprocess.Popen(
        [sys.executable, "-m", "services.embedding_service"],
        cwd=project_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Override port: inject via env or modify launch — for now just use default port
    # The service reads port from its __main__ block (hardcoded 5100)
    # We'll pass the port via a small wrapper
    return proc


def start_embedding_service_on_port(port: int, project_root: str) -> subprocess.Popen:
    """Start embedding service on a specific port."""
    env = os.environ.copy()
    env["PYTHONPATH"] = project_root
    cmd = (
        f"import uvicorn; "
        f"uvicorn.run('services.embedding_service:app', host='0.0.0.0', port={port}, workers=1)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", cmd],
        cwd=project_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc


def start_api_server(port: int, workers: int, embedding_url: str, project_root: str) -> subprocess.Popen:
    """Start the API server with embedding service URL configured."""
    env = os.environ.copy()
    env["PYTHONPATH"] = project_root
    env["EMBEDDING_SERVICE_URL"] = embedding_url
    cmd = (
        f"import uvicorn; "
        f"uvicorn.run('core.gateway:app', host='0.0.0.0', port={port}, workers={workers})"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", cmd],
        cwd=project_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc


def wait_for_service(url: str, label: str, timeout: int = 30) -> bool:
    """Wait for a service to become healthy."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                print(f"  {label} is UP")
                return True
        except Exception:
            pass
        time.sleep(1)
    print(f"  {label} FAILED to start within {timeout}s")
    return False


def warmup_embedding(embedding_url: str, timeout: int = 180) -> bool:
    """Send a warm-up embed request to pre-load the retriever model."""
    print(f"  Warming up retriever model (this loads ~1.3GB BGE-large)...")
    try:
        start = time.time()
        r = requests.post(
            f"{embedding_url}/embed",
            json={"texts": ["test warmup query"], "model": "retriever"},
            timeout=timeout,
        )
        elapsed = time.time() - start
        if r.status_code == 200:
            data = r.json()
            dim = len(data["vectors"][0])
            print(f"  Retriever model loaded in {elapsed:.1f}s (dim={dim})")
            return True
        else:
            print(f"  Warmup FAILED: HTTP {r.status_code} — {r.text[:200]}")
            return False
    except Exception as e:
        print(f"  Warmup FAILED: {e}")
        return False


def kill_proc(proc: subprocess.Popen, label: str):
    """Terminate a subprocess."""
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
            print(f"  {label} stopped (PID {proc.pid})")
        except Exception:
            try:
                proc.kill()
                print(f"  {label} force-killed (PID {proc.pid})")
            except Exception:
                print(f"  {label} could not be killed (PID {proc.pid})")


# ── Report Generation ────────────────────────────────────────────────────────

def generate_markdown(details, summary, total_elapsed, workers, embedding_port, api_port):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(details)

    lines = [
        f"# Test Report — {now}",
        "",
        f"**Architecture:** Embedding microservice (port {embedding_port}) + API server (port {api_port}, {workers} workers)  ",
        f"**Total Prompts:** {total}  ",
        f"**Total Time:** {total_elapsed:.1f}s  ",
        "",
        "## Summary",
        "",
        "| Status | Count |",
        "|--------|-------|",
    ]
    for status in ["PASS", "WEAK", "FAIL", "ERROR"]:
        lines.append(f"| {status} | {summary.get(status, 0)} |")
    lines.append("")

    times = [d["elapsed"] for d in details if d["elapsed"] > 0]
    if times:
        lines.extend([
            "## Timing",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Avg | {sum(times)/len(times):.1f}s |",
            f"| Min | {min(times):.1f}s |",
            f"| Max | {max(times):.1f}s |",
            f"| Median | {sorted(times)[len(times)//2]:.1f}s |",
            "",
        ])

    lines.extend([
        "## Results Overview",
        "",
        "| # | Status | Category | Prompt | Agent(s) | Time | Resp Len |",
        "|---|--------|----------|--------|----------|------|----------|",
    ])
    for d in details:
        agents_str = ", ".join(d["agents_used"]) if d["agents_used"] else "—"
        prompt_short = d["prompt"][:50] + ("..." if len(d["prompt"]) > 50 else "")
        lines.append(
            f"| {d['id']} | {d['status']} | {d['category']} | {prompt_short} "
            f"| {agents_str} | {d['elapsed']:.1f}s | {d['response_len']} |"
        )
    lines.extend(["", "---", "", "## Detailed Results", ""])

    for d in details:
        lines.extend([
            f"### #{d['id']} — {d['status']} — {d['category']}",
            "",
            f"**Query:** `{d['prompt']}`  ",
            f"**Expected Agent:** {d['expected_agent'] or 'Any'}  ",
            f"**Actual Agent(s):** {', '.join(d['agents_used']) if d['agents_used'] else '—'}  ",
            f"**Time:** {d['elapsed']:.1f}s  ",
            f"**Response Length:** {d['response_len']} chars  ",
        ])
        if d.get("tokens"):
            lines.append(f"**Tokens:** {d['tokens']}  ")
        lines.append("")

        if d.get("full_response"):
            lines.extend([
                "<details>",
                "<summary>Full Response</summary>",
                "",
                d["full_response"],
                "",
                "</details>",
                "",
            ])
        elif d.get("error_detail"):
            lines.append(f"**Error:** `{d['error_detail']}`")
            lines.append("")
        lines.extend(["---", ""])

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="End-to-end test with embedding service")
    parser.add_argument("--workers", type=int, default=4, help="API server workers (default: 4)")
    parser.add_argument("--embedding-port", type=int, default=5200, help="Embedding service port")
    parser.add_argument("--api-port", type=int, default=5050, help="API server port")
    parser.add_argument("--timeout", type=int, default=120, help="Per-request timeout in seconds")
    parser.add_argument("--warmup-timeout", type=int, default=600, help="Model warmup timeout in seconds")
    args = parser.parse_args()

    # Resolve project root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)

    embedding_url = f"http://localhost:{args.embedding_port}"
    api_url = f"http://localhost:{args.api_port}/pyapi/search"

    emb_proc = None
    api_proc = None

    try:
        print(f"{'='*80}")
        print(f"  End-to-End Test: Embedding Service + {args.workers}-Worker API Server")
        print(f"  Embedding port: {args.embedding_port}  |  API port: {args.api_port}")
        print(f"{'='*80}\n")

        # Step 1: Start embedding service
        print("[1/4] Starting embedding service...")
        emb_proc = start_embedding_service_on_port(args.embedding_port, project_root)
        if not wait_for_service(f"{embedding_url}/health", "Embedding service", timeout=30):
            print("FATAL: Embedding service failed to start")
            return 1

        # Step 2: Warm up the retriever model
        print("\n[2/4] Warming up embedding model...")
        if not warmup_embedding(embedding_url, timeout=args.warmup_timeout):
            print("FATAL: Model warm-up failed (possible OOM)")
            return 1

        # Step 3: Start API server
        print(f"\n[3/4] Starting API server ({args.workers} workers)...")
        api_proc = start_api_server(args.api_port, args.workers, embedding_url, project_root)
        # Give workers time to spawn
        if not wait_for_service(f"http://localhost:{args.api_port}/docs", "API server", timeout=180):
            print("FATAL: API server failed to start")
            return 1

        # Step 4: Run all 27 prompts
        print(f"\n[4/4] Running {len(PROMPTS)} test prompts...\n")
        results = {"PASS": 0, "WEAK": 0, "FAIL": 0, "ERROR": 0}
        details = []
        total_start = time.time()

        for idx, prompt, category, expected_agent in PROMPTS:
            sys.stdout.write(f"  #{idx:2d} [{category:20s}] {prompt[:55]:55s} ... ")
            sys.stdout.flush()

            start = time.time()
            try:
                resp = requests.post(
                    api_url,
                    json={"Promptquery": prompt},
                    timeout=args.timeout,
                )
                elapsed = time.time() - start

                if resp.status_code != 200:
                    status = "ERROR"
                    agents = []
                    result_text = ""
                    tokens = 0
                    error_detail = f"HTTP {resp.status_code}: {resp.text[:200]}"
                else:
                    data = resp.json()
                    status = classify_result(data, expected_agent)
                    agents = data.get("agents_used", [])
                    result_text = data.get("result", "")
                    tokens = data.get("total_tokens_consumed", 0)
                    error_detail = ""

            except requests.exceptions.Timeout:
                elapsed = args.timeout
                status = "ERROR"
                agents = []
                result_text = ""
                tokens = 0
                error_detail = "TIMEOUT"
            except Exception as e:
                elapsed = time.time() - start
                status = "ERROR"
                agents = []
                result_text = ""
                tokens = 0
                error_detail = str(e)[:200]

            results[status] += 1
            details.append({
                "id": idx,
                "prompt": prompt,
                "category": category,
                "expected_agent": expected_agent,
                "status": status,
                "agents_used": agents,
                "response_len": len(result_text),
                "elapsed": round(elapsed, 1),
                "tokens": tokens,
                "full_response": result_text,
                "error_detail": error_detail,
            })

            color = {"PASS": "\033[92m", "WEAK": "\033[93m", "FAIL": "\033[91m", "ERROR": "\033[91m"}
            reset = "\033[0m"
            print(f"{color.get(status, '')}{status:5s}{reset}  ({elapsed:.1f}s)")

        total_elapsed = time.time() - total_start

        # Summary
        total = len(PROMPTS)
        print(f"\n{'='*80}")
        print(f"  RESULTS: {results['PASS']}/{total} PASS, "
              f"{results['WEAK']} WEAK, {results['FAIL']} FAIL, {results['ERROR']} ERROR")
        print(f"  Total time: {total_elapsed:.1f}s")
        print(f"  Architecture: 1 embedding service + {args.workers} API workers")
        print(f"{'='*80}")

        # Non-PASS details
        non_pass = [d for d in details if d["status"] != "PASS"]
        if non_pass:
            print(f"\n--- Non-PASS Details ---")
            for d in non_pass:
                print(f"\n  #{d['id']} [{d['status']}] {d['prompt']}")
                print(f"    Agents: {d['agents_used']}, Response len: {d['response_len']}")
                if d["full_response"]:
                    preview = d["full_response"].replace("\n", " ")[:150]
                    print(f"    Preview: {preview}...")
        else:
            print("\n  All prompts passed!")

        # Generate reports
        md_content = generate_markdown(
            details, results, total_elapsed,
            args.workers, args.embedding_port, args.api_port,
        )
        md_path = os.path.join(script_dir, "test_report_microservice.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"\n  Markdown report: {md_path}")

        json_details = []
        for d in details:
            jd = dict(d)
            if len(jd.get("full_response", "")) > 500:
                jd["response_preview"] = jd["full_response"][:500] + "..."
            else:
                jd["response_preview"] = jd["full_response"]
            del jd["full_response"]
            json_details.append(jd)

        json_path = os.path.join(script_dir, "test_results_microservice.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                "summary": results,
                "total_elapsed": total_elapsed,
                "architecture": {
                    "embedding_port": args.embedding_port,
                    "api_port": args.api_port,
                    "api_workers": args.workers,
                },
                "details": json_details,
            }, f, indent=2)
        print(f"  JSON results:   {json_path}")

        return 0 if results["FAIL"] == 0 and results["ERROR"] == 0 else 1

    finally:
        print("\n  Shutting down services...")
        kill_proc(api_proc, "API server")
        kill_proc(emb_proc, "Embedding service")
        print("  Done.")


if __name__ == "__main__":
    sys.exit(main())
