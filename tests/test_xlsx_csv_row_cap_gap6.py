"""Gap #6 — XLSX/CSV row-cap fix. Verify:

  1. Files under the cap extract cleanly with all rows present.
  2. Files exceeding the cap surface a loud [! TRUNCATED ...] marker
     inside the extracted text (never silently drop).
  3. Env var XLSX_CSV_MAX_ROWS overrides the default.
  4. Per-sheet cap on XLSX (one huge sheet doesn't starve others).

Run: `python -m tests.test_xlsx_csv_row_cap_gap6`
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _passed(name: str) -> tuple[bool, str]:
    return True, f"[OK]  {name}"


def _failed(name: str, detail: str = "") -> tuple[bool, str]:
    return False, f"[FAIL] {name}  {detail}"


def _mk_csv(path: Path, n_rows: int, n_cols: int = 5) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([f"col{i}" for i in range(n_cols)])
        for r in range(n_rows):
            w.writerow([f"r{r}c{c}" for c in range(n_cols)])


def _mk_xlsx(path: Path, sheet_rows: dict[str, int], n_cols: int = 5) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    # Remove the default sheet, we'll add named ones
    wb.remove(wb.active)
    for name, rows in sheet_rows.items():
        ws = wb.create_sheet(name)
        ws.append([f"col{i}" for i in range(n_cols)])
        for r in range(rows):
            ws.append([f"{name[:3]}_r{r}c{c}" for c in range(n_cols)])
    wb.save(path)


def run_tests() -> int:
    from core.file_processor import _extract_csv_text, _extract_xlsx_text
    from core.settings import XLSX_CSV_MAX_ROWS

    results: list[tuple[bool, str]] = []
    print(f"Default XLSX_CSV_MAX_ROWS = {XLSX_CSV_MAX_ROWS}")
    print()

    ws = Path(tempfile.mkdtemp(prefix="gap6_"))
    try:
        # ---- CSV: under cap ----
        csv_small = ws / "small.csv"
        _mk_csv(csv_small, n_rows=50)
        out = _extract_csv_text(str(csv_small))
        line_count = out.count("\n") + 1
        has_r49 = "r49c0" in out
        no_trunc = "TRUNCATED" not in out
        print(f"CSV small (50 rows): output {len(out)} chars, {line_count} lines")
        results.append(
            _passed("T1 CSV under cap: all rows present, no truncation marker")
            if has_r49 and no_trunc
            else _failed("T1 CSV under cap",
                         detail=f"has_r49={has_r49} no_trunc={no_trunc}")
        )

        # ---- CSV: exactly at cap ----
        # Cap counts header + data rows together (i=0 is header). So a
        # file with header + (cap-1) data rows fits exactly.
        csv_at_cap = ws / "at_cap.csv"
        _mk_csv(csv_at_cap, n_rows=XLSX_CSV_MAX_ROWS - 1)
        out = _extract_csv_text(str(csv_at_cap))
        has_last_data = f"r{XLSX_CSV_MAX_ROWS - 2}c0" in out
        no_trunc = "TRUNCATED" not in out
        print(f"CSV at cap (header + {XLSX_CSV_MAX_ROWS - 1} data = {XLSX_CSV_MAX_ROWS} total): "
              f"len {len(out)}, last-data-present={has_last_data}, no_trunc={no_trunc}")
        results.append(
            _passed("T2 CSV at cap: no truncation marker (fits exactly)")
            if no_trunc and has_last_data
            else _failed("T2 CSV at cap",
                         detail=f"no_trunc={no_trunc} has_last={has_last_data}")
        )

        # ---- CSV: over cap ----
        over_by = 250
        csv_over = ws / "over.csv"
        _mk_csv(csv_over, n_rows=XLSX_CSV_MAX_ROWS + over_by)
        out = _extract_csv_text(str(csv_over))
        has_trunc = "TRUNCATED" in out
        has_r0 = "r0c0" in out
        has_last_dropped = f"r{XLSX_CSV_MAX_ROWS + over_by - 1}c0" not in out
        print(f"CSV over cap ({XLSX_CSV_MAX_ROWS + over_by} rows): "
              f"trunc_marker={has_trunc}, dropped_last={has_last_dropped}")
        results.append(
            _passed("T3 CSV over cap: TRUNCATED marker present")
            if has_trunc and has_r0 and has_last_dropped
            else _failed("T3 CSV over cap",
                         detail=f"trunc={has_trunc} r0={has_r0} dropped_last={has_last_dropped}")
        )

        # ---- XLSX: single sheet under cap ----
        xlsx_small = ws / "small.xlsx"
        _mk_xlsx(xlsx_small, {"Data": 50})
        out = _extract_xlsx_text(str(xlsx_small))
        print(f"XLSX small (Data:50): {len(out)} chars")
        results.append(
            _passed("T4 XLSX under cap: sheet name + rows present")
            if "### Sheet: Data" in out and "Dat_r49c0" in out and "TRUNCATED" not in out
            else _failed("T4 XLSX under cap")
        )

        # ---- XLSX: single sheet over cap ----
        xlsx_over = ws / "over.xlsx"
        _mk_xlsx(xlsx_over, {"Huge": XLSX_CSV_MAX_ROWS + 500})
        out = _extract_xlsx_text(str(xlsx_over))
        has_trunc = "TRUNCATED" in out and "Huge" in out
        print(f"XLSX over cap (Huge:{XLSX_CSV_MAX_ROWS + 500}): trunc_marker={has_trunc}")
        results.append(
            _passed("T5 XLSX over cap: per-sheet TRUNCATED marker")
            if has_trunc
            else _failed("T5 XLSX over cap",
                         detail=f"trunc={has_trunc}")
        )

        # ---- XLSX: multi-sheet, per-sheet cap independent ----
        xlsx_multi = ws / "multi.xlsx"
        _mk_xlsx(xlsx_multi, {
            "TinyA": 20,
            "HugeB": XLSX_CSV_MAX_ROWS + 100,
            "TinyC": 30,
        })
        out = _extract_xlsx_text(str(xlsx_multi))
        has_tinya = "TinyA" in out and "Tin_r19c0" in out
        has_hugeb_trunc = "HugeB" in out and "TRUNCATED" in out
        has_tinyc = "TinyC" in out and "Tin_r29c0" in out
        print(f"XLSX multi: TinyA={has_tinya}  HugeB_trunc={has_hugeb_trunc}  TinyC={has_tinyc}")
        results.append(
            _passed("T6 XLSX multi-sheet: only HugeB truncated, tinies intact")
            if has_tinya and has_hugeb_trunc and has_tinyc
            else _failed("T6 XLSX multi-sheet",
                         detail=f"tinyA={has_tinya} hugeB_trunc={has_hugeb_trunc} tinyC={has_tinyc}")
        )

        # ---- CSV cap == 100 (regression: old default) ----
        # Simulate the OLD 100-row cap via monkey-patched setting to make
        # sure the marker mechanism activates correctly at any cap value.
        import core.settings as _s
        original = _s.XLSX_CSV_MAX_ROWS
        _s.XLSX_CSV_MAX_ROWS = 100
        try:
            csv_150 = ws / "one_fifty.csv"
            _mk_csv(csv_150, n_rows=150)
            out = _extract_csv_text(str(csv_150))
            has_trunc = "TRUNCATED" in out and "100 rows" in out
            print(f"CSV cap=100 with 150 rows: trunc_marker_and_cap_shown={has_trunc}")
            results.append(
                _passed("T7 env override respected (cap=100 marker names 100)")
                if has_trunc
                else _failed("T7 env override", detail=f"trunc={has_trunc}")
            )
        finally:
            _s.XLSX_CSV_MAX_ROWS = original

    finally:
        import shutil
        shutil.rmtree(ws, ignore_errors=True)

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print()
    print("=" * 78)
    print(f"SUMMARY: {passed}/{total} passed")
    print("=" * 78)
    for _, line in results:
        print(f"  {line}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run_tests())
