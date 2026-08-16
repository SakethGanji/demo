"""Unit tests — convert_to_parquet rejects unparseable files as user errors.

InvalidFileError is the upload API's 400 (``invalid-file``) contract: it must
stay a ValueError subclass and carry a format-specific message.
"""

from __future__ import annotations

import pytest

from app.shared.data_io import InvalidFileError, convert_to_parquet


def test_not_a_zip_xlsx_raises_invalid_file(tmp_path):
    bad = tmp_path / "report.xlsx"
    bad.write_bytes(b"this is definitely not a zip archive")
    with pytest.raises(InvalidFileError) as exc:
        convert_to_parquet(bad, tmp_path / "out.parquet")
    assert "Excel" in str(exc.value)


def test_garbage_parquet_raises_invalid_file(tmp_path):
    bad = tmp_path / "data.parquet"
    bad.write_bytes(b"\x00\x01garbage bytes, no PAR1 magic\xff")
    with pytest.raises(InvalidFileError) as exc:
        convert_to_parquet(bad, tmp_path / "out.parquet")
    assert "parquet" in str(exc.value)


def test_valid_csv_still_converts(tmp_path):
    src = tmp_path / "tiny.csv"
    src.write_text("id,label\n1,a\n2,b\n")
    dest = tmp_path / "out.parquet"
    result = convert_to_parquet(src, dest)
    try:
        assert dest.exists()
        rows = result.conn.execute("SELECT COUNT(*) FROM df").fetchone()[0]
        assert rows == 2
    finally:
        result.conn.close()


def test_invalid_file_error_is_a_value_error():
    # The API layer maps ValueError → 400; breaking this silently turns
    # bad uploads into 500s.
    assert issubclass(InvalidFileError, ValueError)
