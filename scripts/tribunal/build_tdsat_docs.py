r"""Build tribunal_judgements documents from the scraped TDSAT orders.

Input  (from the tdsat.gov.in scrape):
    <data>/metadata.csv   one row per order
    <data>/pdfs/*.pdf     the order PDFs
Output:
    <data>/tdsat_docs.jsonl   one JSON document per line, "_id" included
    <data>/tdsat_report.txt   counts, OCR results, unparsed benches

Text comes from the PDF's text layer (PyMuPDF). Scanned orders with no text
layer go through the app's Gemini Vision OCR (core.file_processor), so the
GOOGLE_API_KEY in the repo .env is used. Re-runs are resumable: documents
already in tdsat_docs.jsonl are kept and skipped.

PDF links point at our S3 copy (tribunal/tdsat/<file>); copy the PDFs there
with upload_tdsat_pdfs.py. No OpenSearch access here: upload the documents
with tribunal_index.py.

Usage (from the repo root, project venv):
    python scripts/tribunal/build_tdsat_docs.py --data D:\scraping_websites\data\tdsat
    python scripts/tribunal/build_tdsat_docs.py --data ... --limit 20     # trial run
    python scripts/tribunal/build_tdsat_docs.py --data ... --no-ocr       # text layer only
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from config.tribunals import TDSAT  # noqa: E402

TDSAT_CODE, TDSAT_NAME = TDSAT.code, TDSAT.name

# Chars per page below which an order is treated as scanned.
MIN_TEXT_PER_PAGE = 50

# Where upload_tdsat_pdfs.py puts the PDFs, next to the migrated tribunal PDFs.
S3_PREFIX = "tribunal/tdsat/"

# TDSAT bench members as they appear in the site's "member" column, matched on
# the name with every non-letter removed ("MR.P.K. RASTOGI" -> "PKRASTOGI").
# Spelling variants seen in the scrape are listed explicitly.
TDSAT_MEMBERS = [
    ("Justice S.B. Sinha", ["SBSINHA"]),
    ("P.K. Rastogi", ["RASTOGI"]),
    ("G.D. Gaiha", ["GAIHA"]),
    ("Justice Ram Krishna Gautam", ["RAMKRISHNAGAUTAM"]),
    ("Justice Aftab Alam", ["AFTABALAM"]),
    ("Dr. Kuldip Singh", ["KULDIPSINGH"]),
    ("Bipin Bihari Srivastava", ["SRIVASTAVA"]),
    ("Justice Shiva Kirti Singh", ["SHIVAKIRTISINGH"]),
    ("A.K. Bhargava", ["BHARGAVA"]),
    ("Justice Dhirubhai Naranbhai Patel", ["DHIRUBHAINARANBHAIPATEL"]),
    ("Subodh Kumar Gupta", ["SUBODHKUMARGUPTA"]),
    ("Dr. Sanjeev Banzal", ["SANJEEVBANZAL"]),
    ("Justice N. Santosh Hegde", ["SANTOSHHEGDE", "SANTHOSHHEGDE"]),
    ("Vinod Vaish", ["VINODVAISH"]),
    ("Lt. Gen. D.P. Sehgal (Retd.)", ["SEHGAL"]),
    ("Justice Arun Kumar", ["ARUNKUMAR"]),
    ("Dr. J.S. Sarma", ["JSSARMA"]),
    ("Justice D.P. Wadhwa", ["WADHWA"]),
    ("Justice Suhas C. Sen", ["SUHASCSEN"]),
    ("R.U.S. Prasad", ["RUSPRASAD"]),
    ("P.R. Dasgupta", ["DASGUPTA", "DASTUPTA"]),
    ("Biresh Kumar (Registrar)", ["BIRESHKUMAR"]),
]


def parse_bench(raw: str) -> list[str]:
    """Bench member names in the order they appear in the raw string."""
    compact = re.sub(r"[^A-Z]", "", (raw or "").upper())
    found = []
    for name, keys in TDSAT_MEMBERS:
        positions = [compact.find(k) for k in keys if k in compact]
        if positions:
            found.append((min(positions), name))
    return [name for _, name in sorted(found)]


def iso_date(ddmmyyyy: str) -> str | None:
    try:
        return datetime.strptime(ddmmyyyy.strip(), "%d-%m-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def doc_id(pdf_url: str) -> str:
    return "tdsat__" + Path(pdf_url).stem


def with_s3_link(doc: dict) -> dict:
    """Point the document's PDF link at our S3 copy.

    The app shows only links to its own S3 bucket (core.url_filter), so a
    tdsat.gov.in link would be stripped from answers and the sources panel.
    The PDFs are copied there by upload_tdsat_pdfs.py; the site's URL is kept
    in source_pdf_link."""
    from urllib.parse import quote
    from core.settings import S3_BUCKET, S3_REGION
    key = f"{S3_PREFIX}{Path(doc['source'].replace(chr(92), '/')).name}"
    url = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{quote(key, safe='/')}"
    return {**doc, "s3_bucket": S3_BUCKET, "s3_key": key,
            "pdf_links": [{"label": "PDF (S3)", "url": url}]}


def extract_text(pdf: Path) -> tuple[str, int]:
    import fitz
    with fitz.open(pdf) as doc:
        return "\n".join(p.get_text() for p in doc), doc.page_count


def ocr_text(pdf: Path, pages: int) -> str:
    from core.file_processor import _vision_ocr_pdf
    text = _vision_ocr_pdf(str(pdf), pages, file_name=pdf.name)
    # The OCR output is markdown for the chat UI: drop page markers and bold.
    text = re.sub(r"(?m)^-{3} Pages? [\d\-– ]+ -{3}\s*$", "", text)
    return text.replace("**", "")


def build_doc(row: dict, pdf_dir: Path, use_ocr: bool) -> dict:
    pdf = pdf_dir / row["file_name"]
    text, pages = extract_text(pdf)
    method = "pymupdf"
    if len(text.strip()) < MIN_TEXT_PER_PAGE * max(pages, 1):
        if use_ocr:
            ocr = ocr_text(pdf, pages)
            if len(ocr.strip()) > len(text.strip()):
                text, method = ocr, "gemini_vision_ocr"
        else:
            method = "pymupdf_low_text"

    case_no = clean(row["case_no"])
    case_type = case_no.split("/")[0].strip() if "/" in case_no else ""
    date = iso_date(row["order_date"])
    petitioner, respondent = clean(row["petitioner"]), clean(row["respondent"])
    parties = clean(row.get("party_detail")) or f"{petitioner} vs {respondent}"
    bench = parse_bench(row["member_name"])
    text = text.strip()

    return {
        "_id": doc_id(row["pdf_url"]),
        "tribunal_name": TDSAT_NAME,
        "tribunal_code": TDSAT_CODE,
        "court_name": "tribunal",
        "court_code": "TRIB",
        "case_no": case_no,
        "case_type": case_type,
        "petition_type": case_type.lower(),
        "title": parties,
        "parties": parties,
        "petitioner_names": [petitioner] if petitioner else [],
        "respondent_names": [respondent] if respondent else [],
        "bench": clean(row["member_name"]),
        "bench_judge_names": bench,
        "judgment_date": date,
        "year": int(date[:4]) if date else None,
        "full_text": text,
        "char_count": len(text),
        "page_count": pages,
        "extraction_method": method,
        "text_status": "done" if len(text) >= 200 else "low_text",
        "pdf_links": [],            # set by with_s3_link
        "source_pdf_link": row["pdf_url"],
        "source": f"tribunal\\{TDSAT_CODE}\\{date[:4] if date else 'unknown'}\\{row['file_name']}",
        "ingest_source": "tdsat_scrape",
        "indexed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help=r"folder with metadata.csv and pdfs\ ")
    ap.add_argument("--limit", type=int, default=0, help="only the first N rows (trial run)")
    ap.add_argument("--no-ocr", action="store_true", help="skip OCR for scanned orders")
    ap.add_argument("--workers", type=int, default=3, help="orders processed in parallel")
    ap.add_argument("--env", default=str(REPO / ".env"), help=".env with GOOGLE_API_KEY (for OCR)")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(args.env)

    data = Path(args.data)
    out_path, report_path = data / "tdsat_docs.jsonl", data / "tdsat_report.txt"
    rows = [r for r in csv.DictReader(open(data / "metadata.csv", encoding="utf-8"))
            if r.get("download_status", "ok") == "ok"]
    if args.limit:
        rows = rows[: args.limit]

    done: dict[str, dict] = {}
    if out_path.exists():
        for line in open(out_path, encoding="utf-8"):
            if line.strip():
                d = json.loads(line)
                done[d["_id"]] = d
    todo = [r for r in rows if doc_id(r["pdf_url"]) not in done]
    print(f"{len(rows)} orders, {len(rows) - len(todo)} already built, {len(todo)} to build")

    errors: list[tuple[str, str]] = []
    started = time.time()
    with open(out_path, "a", encoding="utf-8") as out, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(build_doc, r, data / "pdfs", not args.no_ocr): r for r in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            row = futures[fut]
            try:
                d = fut.result()
            except Exception as e:  # keep going; the report lists failures
                errors.append((row["file_name"], str(e)[:200]))
                continue
            out.write(json.dumps(d, ensure_ascii=False) + "\n")
            out.flush()
            done[d["_id"]] = d
            if i % 50 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}  ({time.time() - started:.0f}s, errors {len(errors)})")

    built = [with_s3_link(done[doc_id(r["pdf_url"])]) for r in rows if doc_id(r["pdf_url"]) in done]
    # Rewrite the file in metadata order with the final link fields; this
    # also upgrades documents built by an earlier version of this script.
    with open(out_path, "w", encoding="utf-8") as out:
        for d in built:
            out.write(json.dumps(d, ensure_ascii=False) + "\n")

    by_method: dict[str, int] = {}
    for d in built:
        by_method[d["extraction_method"]] = by_method.get(d["extraction_method"], 0) + 1
    low = [d for d in built if d["text_status"] != "done"]
    no_bench = [d for d in built if not d["bench_judge_names"]]
    lines = [
        f"orders in metadata: {len(rows)}",
        f"documents built:    {len(built)}",
        f"failed:             {len(errors)}",
        f"extraction:         {by_method}",
        f"low text (<200 chars): {len(low)}",
        f"no bench parsed:    {len(no_bench)}",
        "",
        "failures:", *[f"  {f}: {e}" for f, e in errors],
        "low text:", *[f"  {d['_id']} pages={d['page_count']} chars={d['char_count']}" for d in low],
        "unparsed bench:", *[f"  {d['_id']}: {d['bench']}" for d in no_bench],
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:7]))
    print(f"wrote {out_path} and {report_path}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
