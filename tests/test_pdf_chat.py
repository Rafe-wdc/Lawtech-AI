"""PDF Upload + Chat Performance Test

Tests the full PDF pipeline via /pyapi/chat: upload file + ask question (SSE).
Follow-up questions reuse the same thread_id without re-uploading.
Measures latency, answer quality, and file processing for each PDF.

Usage:
    # Test all PDFs in a folder:
    python tests/test_pdf_chat.py --pdf-dir ./test_pdfs

    # Specific files:
    python tests/test_pdf_chat.py --files doc1.pdf doc2.pdf

    # With API key:
    python tests/test_pdf_chat.py --pdf-dir ./test_pdfs --api-key mykey

    # Fewer questions (faster):
    python tests/test_pdf_chat.py --pdf-dir ./test_pdfs --max-questions 3

    # Sequential (no concurrency):
    python tests/test_pdf_chat.py --pdf-dir ./test_pdfs --concurrency 1
"""

import argparse
import asyncio
import glob
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import httpx

# Default questions — stress-test OCR quality, retrieval, and generation
DEFAULT_QUESTIONS = [
    "What is this document about? Give a brief summary in 3-4 sentences.",
    "Who are the parties involved in this document? List their names and roles.",
    "What specific acts, sections, or legal provisions are cited in this document?",
    "List all important dates mentioned — filing dates, hearing dates, order dates.",
    "Which court or authority issued this document? Mention the judge name if available.",
    "What relief was sought and what was the final order or decision?",
    "What were the main arguments made by each side?",
    "List any case laws or precedents cited in this document.",
    "What are the key facts of the case?",
    "What legal remedies or next steps are available to the parties?",
]


@dataclass
class QAResult:
    question: str
    answer_preview: str = ""
    answer_length: int = 0
    time_sec: float = 0.0
    error: str = ""
    has_content: bool = False
    file_processing_msg: str = ""


@dataclass
class PdfTestResult:
    filename: str
    file_size_mb: float = 0.0
    thread_id: str = ""

    # First message (upload + Q1) metrics
    upload_ok: bool = False
    upload_time_sec: float = 0.0
    upload_error: str = ""
    file_processing_summary: str = ""

    # Q&A metrics
    qa_results: list = field(default_factory=list)

    # Aggregates
    avg_qa_time_sec: float = 0.0
    total_time_sec: float = 0.0


async def _read_sse_response(response: httpx.Response) -> dict:
    """Read SSE stream and collect tokens, thread_id, file_processing events."""
    thread_id = ""
    tokens = []
    file_msgs = []
    sources = []
    error = ""

    full_response = ""

    async for line in response.aiter_lines():
        if not line.startswith("data: "):
            continue
        data_str = line[6:].strip()
        if data_str == "[DONE]":
            break
        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        etype = event.get("type", "")
        if etype == "thread_id":
            thread_id = event.get("data", "")
        elif etype == "token":
            tokens.append(event.get("content", ""))
        elif etype == "response":
            # Final assembled response (sent after streaming tokens)
            full_response = event.get("content", "")
        elif etype == "file_processing":
            file_msgs.append(event.get("message", ""))
        elif etype == "sources":
            sources = event.get("data", [])
        elif etype == "error":
            error = event.get("message", event.get("data", str(event)))
        elif etype == "done":
            pass

    # Prefer full response if available, otherwise concatenate tokens
    full_answer = full_response or "".join(tokens)
    return {
        "thread_id": thread_id,
        "answer": full_answer,
        "file_processing_msgs": file_msgs,
        "sources": sources,
        "error": error,
    }


async def chat_with_file(
    client: httpx.AsyncClient, base_url: str,
    filepath: str, question: str, headers: dict,
    thread_id: str = "",
) -> tuple[str, QAResult]:
    """Send a question with optional file via /pyapi/chat (SSE).

    Returns (thread_id, QAResult).
    If thread_id is provided, this is a follow-up (no file uploaded).
    """
    filename = os.path.basename(filepath)
    start = time.perf_counter()

    try:
        data = {"query": question}
        if thread_id:
            data["globalThreadId"] = thread_id

        if not thread_id:
            # First message: attach the file
            with open(filepath, "rb") as f:
                files = [("files", (filename, f, "application/pdf"))]
                async with client.stream(
                    "POST",
                    f"{base_url}/pyapi/chat",
                    data=data,
                    files=files,
                    headers=headers,
                    timeout=600.0,
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        elapsed = time.perf_counter() - start
                        return "", QAResult(
                            question=question,
                            time_sec=round(elapsed, 2),
                            error=f"HTTP {resp.status_code}: {body.decode()[:300]}",
                        )
                    result = await _read_sse_response(resp)
        else:
            # Follow-up: no file
            async with client.stream(
                "POST",
                f"{base_url}/pyapi/chat",
                data=data,
                headers=headers,
                timeout=300.0,
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    elapsed = time.perf_counter() - start
                    return thread_id, QAResult(
                        question=question,
                        time_sec=round(elapsed, 2),
                        error=f"HTTP {resp.status_code}: {body.decode()[:300]}",
                    )
                result = await _read_sse_response(resp)

        elapsed = time.perf_counter() - start
        answer = result["answer"]
        tid = result["thread_id"] or thread_id

        if result["error"]:
            return tid, QAResult(
                question=question,
                time_sec=round(elapsed, 2),
                error=result["error"],
                file_processing_msg="; ".join(result["file_processing_msgs"]),
            )

        return tid, QAResult(
            question=question,
            answer_preview=answer[:200],
            answer_length=len(answer),
            time_sec=round(elapsed, 2),
            has_content=len(answer) > 50,
            file_processing_msg="; ".join(result["file_processing_msgs"]),
        )

    except Exception as e:
        elapsed = time.perf_counter() - start
        return thread_id, QAResult(
            question=question,
            time_sec=round(elapsed, 2),
            error=str(e),
        )


async def test_one_pdf(
    client: httpx.AsyncClient, base_url: str,
    filepath: str, questions: list[str], headers: dict,
) -> PdfTestResult:
    """Full test for one PDF: upload with Q1, then follow-up Q2..Qn."""
    filename = os.path.basename(filepath)
    file_size = os.path.getsize(filepath) / (1024 * 1024)
    result = PdfTestResult(filename=filename, file_size_mb=round(file_size, 2))
    total_start = time.perf_counter()

    if not questions:
        result.total_time_sec = 0
        return result

    # Q1: Upload file + first question
    print(f"  [{filename}] Uploading ({file_size:.1f} MB) + Q1: {questions[0][:50]}...")
    thread_id, qa1 = await chat_with_file(
        client, base_url, filepath, questions[0], headers
    )
    result.thread_id = thread_id
    result.qa_results.append(asdict(qa1))

    if qa1.error:
        print(f"  [{filename}] Q1 ERROR: {qa1.error[:120]}")
        result.upload_ok = False
        result.upload_error = qa1.error
        result.upload_time_sec = qa1.time_sec
        result.total_time_sec = round(time.perf_counter() - total_start, 2)
        return result

    result.upload_ok = True
    result.upload_time_sec = qa1.time_sec
    result.file_processing_summary = qa1.file_processing_msg
    status = "OK" if qa1.has_content else "EMPTY"
    print(f"  [{filename}] Q1 {status}: {qa1.answer_length} chars, {qa1.time_sec}s")
    if qa1.file_processing_msg:
        print(f"  [{filename}] Files: {qa1.file_processing_msg}")

    # Q2..Qn: Follow-up questions on same thread
    for i, q in enumerate(questions[1:], 2):
        print(f"  [{filename}] Q{i}/{len(questions)}: {q[:55]}...")
        _, qa = await chat_with_file(
            client, base_url, filepath, q, headers, thread_id=thread_id
        )
        result.qa_results.append(asdict(qa))

        if qa.error:
            print(f"  [{filename}] Q{i} ERROR: {qa.error[:100]}")
        else:
            status = "OK" if qa.has_content else "EMPTY"
            print(f"  [{filename}] Q{i} {status}: {qa.answer_length} chars, {qa.time_sec}s")

    # Aggregates
    qa_times = [q["time_sec"] for q in result.qa_results if not q.get("error")]
    result.avg_qa_time_sec = round(sum(qa_times) / len(qa_times), 2) if qa_times else 0
    result.total_time_sec = round(time.perf_counter() - total_start, 2)

    return result


async def run_tests(
    pdf_files: list[str], base_url: str, api_key: str,
    questions: list[str], concurrency: int = 1,
):
    """Run the full test suite across all PDFs."""
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    # Health check
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{base_url}/pyapi/health", headers=headers, timeout=10)
            health = resp.json()
            print(f"Server: {base_url}")
            print(f"Health: {health.get('status', 'unknown')}")
            print(f"Version: {health.get('version', 'unknown')}")
        except Exception as e:
            print(f"ERROR: Cannot reach server at {base_url}: {e}")
            print("Start the server first: python -m uvicorn core.gateway:app --host 0.0.0.0 --port 5000")
            return []

    print(f"\nTesting {len(pdf_files)} PDFs with {len(questions)} questions each")
    print(f"Concurrency: {concurrency}")
    print("=" * 70)

    semaphore = asyncio.Semaphore(concurrency)

    async def bounded_test(client, filepath):
        async with semaphore:
            return await test_one_pdf(client, base_url, filepath, questions, headers)

    async with httpx.AsyncClient() as client:
        tasks = [bounded_test(client, f) for f in pdf_files]
        results = await asyncio.gather(*tasks)

    return results


def print_report(results: list[PdfTestResult]):
    """Print a formatted test report."""
    print("\n" + "=" * 70)
    print("TEST REPORT")
    print("=" * 70)

    total_qa_time = 0
    total_questions = 0
    passed_questions = 0
    failed_uploads = 0

    for r in results:
        print(f"\n--- {r.filename} ({r.file_size_mb} MB) ---")

        if not r.upload_ok:
            print(f"  Upload: FAILED ({r.upload_error[:100]})")
            failed_uploads += 1
            continue

        print(f"  Upload + Q1: {r.upload_time_sec}s")
        if r.file_processing_summary:
            print(f"  File processing: {r.file_processing_summary}")
        print(f"  Thread: {r.thread_id}")

        for qa in r.qa_results:
            status = "PASS" if qa.get("has_content") else ("ERROR" if qa.get("error") else "EMPTY")
            marker = "+" if status == "PASS" else "-"
            print(f"  [{marker}] Q: {qa['question'][:55]}...")
            print(f"      {status} | {qa['answer_length']} chars | {qa['time_sec']}s")
            if qa.get("error"):
                print(f"      Error: {qa['error'][:80]}")
            total_questions += 1
            if qa.get("has_content"):
                passed_questions += 1

        total_qa_time += sum(qa["time_sec"] for qa in r.qa_results)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"PDFs tested:       {len(results)}")
    print(f"Upload failures:   {failed_uploads}")
    print(f"Questions asked:   {total_questions}")
    if total_questions:
        print(f"Answers received:  {passed_questions}/{total_questions} "
              f"({passed_questions/total_questions*100:.0f}%)")
    print(f"Total QA time:     {total_qa_time:.1f}s")
    if total_questions > 0:
        print(f"Avg QA latency:    {total_qa_time/total_questions:.1f}s per question")
    print(f"Total test time:   {sum(r.total_time_sec for r in results):.1f}s")


def save_results(results: list[PdfTestResult], output_path: str):
    """Save detailed results to JSON."""
    data = [asdict(r) for r in results]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\nDetailed results saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Test PDF upload + chat via /pyapi/chat (SSE streaming)"
    )

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdf-dir", help="Directory containing PDF files to test")
    source.add_argument("--files", nargs="+", help="Specific PDF files to test")

    parser.add_argument("--url", default="http://localhost:5000",
                        help="Server base URL (default: http://localhost:5000)")
    parser.add_argument("--api-key", default="",
                        help="API key for X-API-Key header")
    parser.add_argument("--questions", nargs="+", default=None,
                        help="Custom questions (default: 10 built-in legal questions)")
    parser.add_argument("--max-questions", type=int, default=None,
                        help="Limit number of questions per PDF")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="Number of parallel PDF tests (default: 1)")
    parser.add_argument("--output", default="tests/test_results_pdf.json",
                        help="Path to save JSON results")

    args = parser.parse_args()

    # Collect PDF files
    if args.pdf_dir:
        pdf_dir = os.path.abspath(args.pdf_dir)
        if not os.path.isdir(pdf_dir):
            print(f"ERROR: Directory not found: {pdf_dir}")
            sys.exit(1)
        pdf_files = sorted(glob.glob(os.path.join(pdf_dir, "*.pdf")))
        if not pdf_files:
            print(f"ERROR: No PDF files found in {pdf_dir}")
            sys.exit(1)
    else:
        pdf_files = [os.path.abspath(f) for f in args.files]
        missing = [f for f in pdf_files if not os.path.exists(f)]
        if missing:
            print(f"ERROR: Files not found: {missing}")
            sys.exit(1)

    print(f"Found {len(pdf_files)} PDFs:")
    for f in pdf_files:
        size = os.path.getsize(f) / (1024 * 1024)
        print(f"  {os.path.basename(f)} ({size:.1f} MB)")

    questions = args.questions or DEFAULT_QUESTIONS
    if args.max_questions:
        questions = questions[:args.max_questions]

    results = asyncio.run(run_tests(
        pdf_files=pdf_files,
        base_url=args.url,
        api_key=args.api_key,
        questions=questions,
        concurrency=args.concurrency,
    ))

    if results:
        print_report(results)
        save_results(results, args.output)


if __name__ == "__main__":
    main()
