"""``include_sheets`` selecting nothing is a typed error, not a bare ValueError.

The converter is shared code with two callers (upload and sheet-replace) and
one MCP surface. Raising a bare ``ValueError`` meant every one of them had to
recognise the failure by *guessing* — the upload path did it by catching
``ValueError`` and re-typing it only when it had itself passed
``include_sheets`` — and any caller that did not think of it rendered a user's
misspelled sheet name as a 500 "An unexpected error occurred.".

Typing it at the raise site also carries the workbook's real sheet names on the
exception, which is the list a sheet picker needs and which a string message
cannot be parsed for safely.
"""

from __future__ import annotations

import pytest
from openpyxl import Workbook

from app.shared.data_io import (
    InvalidFileError,
    NoMatchingSheetsError,
    convert_to_parquet,
)


def _workbook(path):
    wb = Workbook()
    wb.active.title = "Revenue"
    wb["Revenue"].append(["id", "amount"])
    wb["Revenue"].append([1, 10])
    ws = wb.create_sheet("Expenses")
    ws.append(["id", "cost"])
    ws.append([1, 5])
    wb.save(path)
    return path


def test_an_unmatched_sheet_selection_raises_the_typed_error(tmp_path):
    """A misspelled sheet name must be distinguishable from a corrupt file."""
    src = _workbook(tmp_path / "book.xlsx")
    with pytest.raises(NoMatchingSheetsError) as exc:
        convert_to_parquet(src, tmp_path / "out.parquet", include_sheets={"Revenu"})

    # Still a ValueError, so existing ValueError handling keeps working.
    assert isinstance(exc.value, ValueError)
    # Not an InvalidFileError: the file parsed, the selection was wrong, and
    # the two map to different problem codes.
    assert not isinstance(exc.value, InvalidFileError)
    assert exc.value.sheets == ["Revenue", "Expenses"]
    assert "Revenue" in str(exc.value)


def test_a_selection_that_matches_still_converts(tmp_path):
    """The guard must only reject selections that really match nothing."""
    src = _workbook(tmp_path / "book.xlsx")
    result = convert_to_parquet(src, tmp_path / "out.parquet",
                                include_sheets={"Expenses"})
    try:
        assert [s.name for s in result.sheets] == ["Expenses"]
    finally:
        result.conn.close()
