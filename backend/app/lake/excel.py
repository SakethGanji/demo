"""P12 (plan §5.1 step 2, R15): an Excel workbook as tables, one per non-empty sheet.

    sheets = read_workbook(path, out_dir, prefix)   # [{sheet, index, hidden, empty, n_rows, n_cols, csv, reader}]

Each sheet is read with **python-calamine** (typed cells; the fallback is **openpyxl** when calamine is not installed
or cannot open the file) and written as a UTF-8 CSV in staging, so the sheet goes through the same typed import as an
uploaded CSV (§5.1 step 3: leading zeros kept as text, failed casts as text with a warning, ambiguous dates as text).
Cells become text the way a person reads them in Excel: a whole number without ".0", a date as ISO `YYYY-MM-DD`, a
date-time as `YYYY-MM-DD HH:MM:SS`, a boolean as true / false, an error cell as empty. Text cells keep their exact
characters ("00123" stays "00123").

The header is the first row with a value; rows above it are skipped (a title row is not detected: the person can
delete it). Fully empty rows at the end and fully empty columns at the right are dropped. Hidden sheets are listed
and left out unless the plan names them. Nothing here returns 500: a file that is not a workbook, a password-protected
workbook or a broken one is `400 excel_unreadable`.
"""

from __future__ import annotations

import csv
import datetime as _dt
import logging
import math
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .writer import LakeError

log = logging.getLogger("ensemble.excel")

MAX_WORKBOOK_BYTES = 200 * 1024 ** 2  # §5.1: ≤ 200 MB per workbook


def reader_name() -> str:
    try:
        import python_calamine  # noqa: F401

        return "calamine"
    except ImportError:
        return "openpyxl"


def cell_text(v: Any) -> str:
    """One cell as the text a CSV of the sheet would hold."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        if v.is_integer() and abs(v) < 2 ** 53:
            return str(int(v))
        return repr(v)
    if isinstance(v, _dt.datetime):
        if v.hour == v.minute == v.second == v.microsecond == 0:
            return v.date().isoformat()
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, _dt.time):
        return v.strftime("%H:%M:%S")
    if isinstance(v, _dt.timedelta):
        return str(v)
    s = str(v)
    if s.startswith("#") and s.rstrip("!?0/") in ("#DIV", "#N/A", "#NAME", "#NULL", "#NUM", "#REF", "#VALUE"):
        return ""  # an Excel error cell (#N/A, #DIV/0!, …) is a missing value
    return s


def _calamine_sheets(path: Path) -> Iterable[tuple[str, int, bool, Iterable[list[Any]]]]:
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_path(str(path))
    metas = getattr(wb, "sheets_metadata", None)
    names = list(wb.sheet_names)
    for i, name in enumerate(names):
        hidden = False
        if metas is not None and i < len(metas):
            vis = str(getattr(metas[i], "visible", "visible")).lower()
            hidden = "visible" not in vis or "veryhidden" in vis or vis.endswith("hidden")
            typ = str(getattr(metas[i], "typ", "worksheet")).lower()
            if "chart" in typ:
                continue
        sh = wb.get_sheet_by_index(i)
        rows = sh.iter_rows() if hasattr(sh, "iter_rows") else iter(sh.to_python(skip_empty_area=False))
        yield name, i, hidden, rows


def _openpyxl_sheets(path: Path) -> Iterable[tuple[str, int, bool, Iterable[list[Any]]]]:
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        for i, ws in enumerate(wb.worksheets):
            hidden = getattr(ws, "sheet_state", "visible") != "visible"
            yield ws.title, i, hidden, (list(r) for r in ws.iter_rows(values_only=True))
    finally:
        wb.close()


def _write_sheet(rows: Iterable[list[Any]], out: Path) -> tuple[int, int, list[str]]:
    """Stream the sheet's rows into `out` (CSV): header = the first row with a value; trailing empty columns and rows
    dropped. Returns (data rows, columns, header)."""
    header: list[str] | None = None
    width = 0
    n = 0
    pending_empty = 0
    with open(out, "w", encoding="utf-8", newline="") as fo:
        w = csv.writer(fo, lineterminator="\n")
        for r in rows:
            vals = [cell_text(v) for v in (r or [])]
            if header is None:
                if not any(v.strip() for v in vals):
                    continue
                while vals and not vals[-1].strip():
                    vals.pop()
                header = [v.strip() for v in vals]
                width = len(header)
                w.writerow(header)
                continue
            vals = (vals + [""] * width)[:width]
            if not any(v.strip() for v in vals):
                pending_empty += 1  # an empty row is kept only when a non-empty one follows (a gap, not the end)
                continue
            for _ in range(pending_empty):
                w.writerow([""] * width)
                n += 1
            pending_empty = 0
            w.writerow(vals)
            n += 1
    return n, width, header or []


_F_RE = re.compile(rb"<(?:\w+:)?f[\s>/]")
_MERGE_RE = re.compile(rb'<(?:\w+:)?mergeCell\s[^>]*ref="([A-Z]+)([0-9]+)(?::([A-Z]+)([0-9]+))?"')


def _col_n(s: bytes) -> int:
    n = 0
    for ch in s.decode():
        n = n * 26 + (ord(ch) - 64)
    return n


def _sheet_parts(z: zipfile.ZipFile) -> dict[str, str]:
    """sheet name → its worksheet part in the package (xl/workbook.xml + its relationships)."""
    from xml.etree import ElementTree as ET

    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    tgt = {r.get("Id"): r.get("Target") or "" for r in rels}
    out: dict[str, str] = {}
    for el in wb.iter():
        if el.tag.rsplit("}", 1)[-1] != "sheet":
            continue
        rid = next((v for k, v in el.attrib.items() if k.rsplit("}", 1)[-1] == "id"), None)
        t = tgt.get(rid or "", "")
        if t:
            out[str(el.get("name"))] = t.lstrip("/") if t.startswith("/") else "xl/" + t
    return out


def sheet_notes(path: Path) -> dict[str, dict[str, int]]:
    """F-X3a (X3 m14): per sheet, the cells that come in empty without being empty in Excel: formula cells with no saved
    value (a workbook written by a script that does not compute formulas) and the cells a merged range hides (only its
    top-left cell holds the value). One streaming pass per sheet; the XML is parsed only when a formula is present."""
    from xml.etree import ElementTree as ET

    out: dict[str, dict[str, int]] = {}
    try:
        with zipfile.ZipFile(path) as z:
            for name, part in _sheet_parts(z).items():
                has_f, merges, tail = False, set(), b""
                try:
                    with z.open(part) as fh:
                        while True:
                            chunk = fh.read(1 << 20)
                            if not chunk:
                                break
                            buf = tail + chunk
                            has_f = has_f or bool(_F_RE.search(buf))
                            merges.update(m.groups() for m in _MERGE_RE.finditer(buf))
                            tail = buf[-256:]
                except KeyError:
                    continue
                hidden = 0
                for a, r1, b, r2 in merges:
                    if b is not None:
                        hidden += (abs(_col_n(b) - _col_n(a)) + 1) * (abs(int(r2) - int(r1)) + 1) - 1
                no_value = 0
                if has_f:
                    with z.open(part) as fh:
                        for _, el in ET.iterparse(fh, events=("end",)):
                            if el.tag.rsplit("}", 1)[-1] != "c":
                                continue
                            kids = {k.tag.rsplit("}", 1)[-1]: k for k in el}
                            if "f" in kids and (kids.get("v") is None or not (kids["v"].text or "")) and "is" not in kids:
                                no_value += 1
                            el.clear()
                if no_value or merges:
                    out[name] = {"formulas_without_value": no_value, "merged_ranges": len(merges), "merged_hidden_cells": hidden}
    except Exception as e:  # noqa: BLE001 - notes never fail the import
        log.info("excel sheet notes for %s: %s", path.name, str(e)[:300])
    return out


def note_warnings(sheet: str, n: dict[str, int] | None) -> list[dict[str, Any]]:
    """The check warnings for one sheet's notes."""
    out: list[dict[str, Any]] = []
    if not n:
        return out
    if n.get("formulas_without_value"):
        k = n["formulas_without_value"]
        out.append({"code": "excel_formula_no_value", "sheet": sheet, "n": k,
                    "message": f"Sheet '{sheet}': {k:,} formula cell(s) have no saved value (the workbook was written by a "
                               "program that does not compute formulas), so they come in empty. Open and save the workbook "
                               "in Excel, or upload the values."})
    if n.get("merged_ranges"):
        out.append({"code": "excel_merged_cells", "sheet": sheet, "n": n["merged_ranges"], "cells": n["merged_hidden_cells"],
                    "message": f"Sheet '{sheet}': {n['merged_ranges']:,} merged range(s). Only the top-left cell of a merged "
                               f"range holds its value, so {n['merged_hidden_cells']:,} other cell(s) come in empty. Unmerge "
                               "them (and fill the value down) to keep it in every row."})
    return out


def sheet_table_name(sheet: str, index: int) -> tuple[str, str | None]:
    """F-X3a (X3 m14): a sheet name as a table name. Accents are folded (`Données €` → `donnees`); a name with no
    letter or digit left (`收入`) becomes `sheet_<n>` (its position), so two such sheets never collide as `data`.
    Returns (table name, why it differs from the sheet name, or None)."""
    folded = unicodedata.normalize("NFKD", sheet).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9_]+", "_", folded).strip("_").lower()[:64]
    if not t:
        return f"sheet_{index + 1}", "no letter or digit in A–Z, 0–9: named by its position"
    if t.startswith("__") or t[0].isdigit():
        t = f"t_{t}".strip("_")
    if folded != sheet:
        return t, "letters outside A–Z folded or left out"
    return t, None


def read_workbook(path: Path, out_dir: Path, prefix: str, *, filename: str = "") -> list[dict[str, Any]]:
    """Every sheet of the workbook at `path`, each non-empty one written to `out_dir/<prefix>.s<i>.csv`."""
    nm = filename or path.name
    size = path.stat().st_size
    if size > MAX_WORKBOOK_BYTES:
        raise LakeError(400, "excel_too_large", f"{nm} is {size / 1024 ** 2:.0f} MB; a workbook can be at most "
                                                f"{MAX_WORKBOOK_BYTES // 1024 ** 2} MB. Save its sheets as CSV files.")
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head[:2] != b"PK":
        if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise LakeError(400, "excel_unreadable", f"{nm} is an old .xls workbook or a password-protected one. Save it "
                                                     "as .xlsx (or its sheets as CSV) and upload again.")
        raise LakeError(400, "excel_unreadable", f"{nm} is not an Excel workbook (.xlsx). Upload a .xlsx file, or the "
                                                 "sheets as CSV files.")
    readers = []
    try:
        import python_calamine  # noqa: F401

        readers.append(("calamine", _calamine_sheets))
    except ImportError:
        pass
    readers.append(("openpyxl", _openpyxl_sheets))
    last: Exception | None = None
    for rname, fn in readers:
        out: list[dict[str, Any]] = []
        try:
            for name, i, hidden, rows in fn(path):
                csv_path = out_dir / f"{prefix}.s{i}.csv"
                n, width, header = _write_sheet(rows, csv_path)
                out.append({"sheet": str(name), "index": i, "hidden": bool(hidden), "empty": width == 0,
                            "n_rows": n, "n_cols": width, "header": header, "csv": csv_path.name, "reader": rname})
            notes = sheet_notes(path)
            for o in out:
                o["notes"] = notes.get(o["sheet"]) or {}
            return out
        except LakeError:
            raise
        except Exception as e:  # noqa: BLE001 - the engine text goes to the log, never to the response
            log.info("excel read with %s failed for %s: %s", rname, nm, str(e)[:500])
            last = e
            continue
    raise LakeError(400, "excel_unreadable", f"Could not read {nm} as an Excel workbook (it may be damaged or "
                                             "password-protected). Open it in Excel, save it as .xlsx and upload again, "
                                             "or upload the sheets as CSV files.") from last
