"""
Local extraction of grade-sheet tables from vector PDFs (before Gemini vision).

Why this exists:
    Vision OCR of a born-digital PDF is wasteful (slow, costs quota, and sometimes
    misreads typed marks). For vector PDFs we can reconstruct the table from the
    text layer in milliseconds. This module is the "fast path" — the caller in
    ``gemini_analyzer.analyze_pdf`` only falls back to vision when this module
    returns ``None``.

Three increasingly tolerant extractors are tried in order:

    1. ``find_tables()`` — ideal for PDFs with explicit ruling lines.
    2. Tab-delimited text — ideal for spreadsheet exports.
    3. Word-gap grid — a conservative grid reconstruction for borderless tables,
       gated by a 72%+ column-stability check to avoid emitting garbage.

Confidence gates (header looks like grade columns, first column looks like
names, >=2 students, etc.) are deliberately strict so a bad fast-path read
falls through to vision instead of importing wrong grades.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from app.models import Homework

logger = logging.getLogger(__name__)

try:
    import fitz  # type: ignore

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False


def _looks_like_name_cell(s: str) -> bool:
    t = str(s).strip()
    if len(t) < 2:
        return False
    if re.match(r"^-?\d+(\.\d+)?$", t):
        return False
    return True


def _header_tokens_reasonable(headers: List[str]) -> bool:
    """At least one column looks like HW, exam, LO code, or generic text header."""
    if not headers:
        return False
    hits = 0
    for h in headers:
        raw = str(h).strip()
        if not raw:
            continue
        c = Homework.canonicalize_import_sheet_header(raw)
        if Homework.is_import_sheet_hw_column(raw) or Homework.is_import_sheet_hw_column(c):
            hits += 1
        elif Homework.is_import_sheet_exam_score_column(
            raw
        ) or Homework.is_import_sheet_exam_score_column(c):
            hits += 1
        else:
            ch = re.sub(r"\s+", "", (c or raw).upper())
            if re.match(r"^(LO|EX|FEX)", ch):
                hits += 1
            elif re.match(r"^[A-Z]{1,4}\d{1,4}$", ch):
                hits += 1
            elif len(raw) >= 2 and not re.match(r"^-?\d+", raw):
                hits += 1
    return hits >= 1


def _data_rows_name_like(body_rows: List[List[str]], ncols: int) -> bool:
    if not body_rows or ncols < 2:
        return False
    sample = body_rows[: min(20, len(body_rows))]
    ok = sum(1 for row in sample if row and _looks_like_name_cell(row[0]))
    return ok >= max(1, len(sample) // 2)


def _same_header_row(a: List[str], b: List[str]) -> bool:
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if str(x).strip().lower() != str(y).strip().lower():
            return False
    return True


def _best_find_tables_grid(page: "fitz.Page") -> Optional[List[List[str]]]:
    tf = page.find_tables()
    if not tf or not tf.tables:
        return None

    def score(tab: Any) -> int:
        try:
            g = tab.extract()
        except Exception:
            return 0
        if not g or not g[0]:
            return 0
        return len(g) * len(g[0])

    best = max(tf.tables, key=score)
    try:
        grid = best.extract()
    except Exception:
        return None
    if not grid:
        return None
    out: List[List[str]] = []
    for row in grid:
        out.append([str(c).strip() if c is not None else "" for c in row])
    return out


def _try_tab_grid(page: "fitz.Page") -> Optional[List[List[str]]]:
    raw = page.get_text() or ""
    if "\t" not in raw:
        return None
    lines = [ln.rstrip("\r") for ln in raw.splitlines() if "\t" in ln and ln.strip()]
    if len(lines) < 2:
        return None
    rows = [[c.strip() for c in ln.split("\t")] for ln in lines]
    n0 = len(rows[0])
    if n0 < 3:
        return None
    if not all(len(r) == n0 for r in rows):
        return None
    return rows


def _try_gap_word_grid(page: "fitz.Page") -> Optional[List[List[str]]]:
    # Reconstructs a grid from raw word boxes by clustering words with the
    # same y-midpoint into rows and splitting on horizontal whitespace gaps.
    # This is the last-resort fallback for borderless tables where neither
    # `find_tables` nor tab-delimited text works.
    words = page.get_text("words")
    if not words or len(words) < 8:
        return None
    row_tol = 4.0
    rows_map: Dict[float, List[Any]] = defaultdict(list)
    for w in words:
        ymid = (float(w[1]) + float(w[3])) / 2.0
        ykey = round(ymid / row_tol) * row_tol
        rows_map[ykey].append(w)
    grid: List[List[str]] = []
    for ykey in sorted(rows_map.keys()):
        roww = sorted(rows_map[ykey], key=lambda t: float(t[0]))
        width = float(roww[-1][2]) - float(roww[0][0])
        # gap_tol scales with row density so dense rows don't false-split on
        # normal letter spacing and sparse rows don't merge distinct cells.
        gap_tol = max(10.0, min(36.0, width / max(len(roww), 4)))
        cells: List[str] = []
        cur: List[Any] = [roww[0]]
        for w in roww[1:]:
            if float(w[0]) - float(cur[-1][2]) > gap_tol:
                cells.append(" ".join(str(t[4]) for t in cur).strip())
                cur = [w]
            else:
                cur.append(w)
        cells.append(" ".join(str(t[4]) for t in cur).strip())
        if len(cells) >= 3:
            grid.append(cells)
    if len(grid) < 2:
        return None
    ncols = max(len(r) for r in grid)
    if ncols < 3:
        return None
    # 72% column-stability gate: in a real grade table almost every row has
    # the same column count. Anything lower is most likely a paragraph or
    # multi-column layout that would produce garbage if accepted.
    stable = sum(1 for r in grid if len(r) == ncols)
    if stable < max(1, int(len(grid) * 0.72)):
        return None
    padded: List[List[str]] = []
    for r in grid:
        padded.append((r + [""] * ncols)[:ncols])
    return padded


def _grid_to_raw_payload(grid: List[List[str]], extraction_path: str) -> Optional[Dict[str, Any]]:
    if not grid or len(grid) < 2:
        return None
    hdr = [str(c or "").strip() for c in grid[0]]
    if len(hdr) < 3:
        return None
    lo_headers = hdr[1:]
    if not _header_tokens_reasonable(lo_headers):
        return None
    body: List[List[str]] = []
    for row in grid[1:]:
        r = [str(c or "").strip() for c in row]
        if len(r) < len(hdr):
            r = r + [""] * (len(hdr) - len(r))
        else:
            r = r[: len(hdr)]
        body.append(r)
    if len(body) < 2:
        return None
    if not _data_rows_name_like(body, len(hdr)):
        return None
    students: List[Dict[str, Any]] = []
    for r in body:
        name = r[0].strip()
        if len(name) < 2:
            continue
        grades: Dict[str, str] = {}
        for i, lab in enumerate(lo_headers):
            if i + 1 >= len(r):
                break
            cell = r[i + 1].strip()
            if cell:
                grades[str(lab)] = cell
        students.append({"name": name, "grades": grades})
    if len(students) < 2:
        return None
    return {
        "learning_objectives": lo_headers,
        "students": students,
        "extraction_path": extraction_path,
    }


def _merge_multipage_grids(
    per_page: List[Tuple[str, List[List[str]]]],
) -> Optional[Tuple[str, List[List[str]]]]:
    if not per_page:
        return None
    path0, grid0 = per_page[0]
    merged: List[List[str]] = [list(row) for row in grid0]
    master_hdr = merged[0]
    merged_path = path0
    for path_i, grid in per_page[1:]:
        if not grid or len(grid[0]) != len(master_hdr):
            logger.info(
                "[grade_sheet_pdf] skip page merge: col mismatch %s vs %s",
                len(grid[0]) if grid else 0,
                len(master_hdr),
            )
            continue
        if _same_header_row(grid[0], master_hdr):
            merged.extend([list(r) for r in grid[1:]])
        else:
            merged.extend([list(r) for r in grid])
        if path_i != merged_path:
            merged_path = "pdf_text"
    return merged_path, merged


def try_extract_grade_sheet_from_pdf_bytes(file_bytes: bytes) -> Optional[Dict[str, Any]]:
    """
    Try structured local extraction. Returns a dict suitable for GradeSheetGeminiAnalyzer._normalize_data
    (keys: learning_objectives, students) plus extraction_path in {"pdf_table", "pdf_text"},
    or None to fall back to vision.
    """
    if not HAS_PYMUPDF or not file_bytes or file_bytes[:4] != b"%PDF":
        return None
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    per_page: List[Tuple[str, List[List[str]]]] = []
    try:
        for page in doc:
            grid: Optional[List[List[str]]] = None
            path = "pdf_table"
            g1 = _best_find_tables_grid(page)
            if g1:
                grid, path = g1, "pdf_table"
            if not grid:
                g2 = _try_tab_grid(page)
                if g2:
                    grid, path = g2, "pdf_text"
            if not grid:
                g3 = _try_gap_word_grid(page)
                if g3:
                    grid, path = g3, "pdf_text"
            if grid:
                per_page.append((path, grid))
    finally:
        doc.close()

    if not per_page:
        return None
    merged_pair = _merge_multipage_grids(per_page)
    if not merged_pair:
        return None
    path_final, merged_grid = merged_pair
    payload = _grid_to_raw_payload(merged_grid, path_final)
    if payload:
        logger.info(
            "[grade_sheet_pdf] extraction_path=%s rows=%d cols=%d",
            path_final,
            len(merged_grid),
            len(merged_grid[0]) if merged_grid else 0,
        )
    return payload
