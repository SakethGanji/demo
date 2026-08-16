"""Pure classification rules behind the upload API's 4xx/5xx split.

The upload routes decide between "your file, fix it" (400 with a code a UI can
branch on) and "our fault" (500) purely from these two helpers. Both used to
have exactly one branch, so every failure that was not a parse error was
reported as a server fault.
"""

from __future__ import annotations

import base64

import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.features.files.services.processing import (
    SheetSelectionError,
    UploadRejectedError,
    classify_processing_error,
)
from app.features.files.services.tus import check_disk_space, parse_tus_metadata
from app.shared.data_io import InvalidFileError


def test_every_user_caused_ingest_failure_gets_its_own_kind():
    """`processing-error` is the ONLY kind the API turns into a 500, so any
    failure the uploader could fix that lands there is reported as an outage.
    A scanner rejection and an include_sheets typo are both the uploader's,
    and both used to fall through to it."""
    assert classify_processing_error(UploadRejectedError("infected")) == "upload-rejected"
    assert classify_processing_error(SheetSelectionError("no match")) == "sheet-not-found"
    assert classify_processing_error(InvalidFileError("bad zip")) == "invalid-file"


def test_a_genuine_server_fault_is_still_a_server_fault():
    """The split only means something if the 500 side is preserved: a bug in
    our own code must not be relabelled as the uploader's mistake."""
    assert classify_processing_error(RuntimeError("connection reset")) == "processing-error"
    assert classify_processing_error(ValueError("something else")) == "processing-error"


def test_malformed_upload_metadata_is_a_client_error_naming_the_key():
    """Upload-Metadata values are base64. Un-padded values raise
    binascii.Error and non-UTF-8 payloads raise UnicodeDecodeError; neither was
    caught, so both escaped the handler and rendered as an opaque 500 that told
    a client with a broken encoder nothing about which key was at fault."""
    for bad in ("filename YS5jc3Z", "filename /w==", "filename !!!!"):
        with pytest.raises(StarletteHTTPException) as err:
            parse_tus_metadata(bad)
        assert err.value.status_code == 400
        assert err.value.code == "invalid-upload-metadata"
        assert err.value.extra["key"] == "filename"


def test_running_out_of_disk_says_so_in_the_machine_readable_code():
    """507 is outside the service's status→title map, so the problem body came
    back with title "Error" and code "error". "Out of disk, try a smaller file
    or come back later" and "something broke" call for completely different UI,
    and only the `code` distinguishes them without parsing prose."""
    with pytest.raises(StarletteHTTPException) as err:
        check_disk_space(2 ** 62)  # more free space than any real volume has

    assert err.value.status_code == 507
    assert err.value.code == "insufficient-storage"


def test_well_formed_upload_metadata_still_parses():
    """Guard on the above: the header's normal shape — comma-separated
    key/base64 pairs, plus valueless keys — must keep working, non-ASCII
    filenames included."""
    header = ", ".join([
        f"filename {base64.b64encode('rapport_été.csv'.encode()).decode()}",
        f"dataset_id {base64.b64encode(b'abc-123').decode()}",
        "is_final",
    ])
    assert parse_tus_metadata(header) == {
        "filename": "rapport_été.csv",
        "dataset_id": "abc-123",
        "is_final": "",
    }
    assert parse_tus_metadata("") == {}
