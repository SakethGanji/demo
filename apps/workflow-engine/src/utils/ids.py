"""Prefixed, time-sorted ID generation using UUIDv7.

UUIDv7 embeds a Unix-ms timestamp in the high bits, so IDs are
naturally time-ordered and B-tree friendly (append-only inserts).
The prefix makes IDs instantly recognisable in logs and debug output.

    wf_019539a1-2b3c-7def-8012-3456789abcde
    exec_019539a1-2b3c-7def-8012-3456789abcde
    cred_019539a1-2b3c-7def-8012-3456789abcde
"""

from __future__ import annotations

import os
import struct
import time
import uuid


def _uuid7() -> uuid.UUID:
    """Generate a UUIDv7 (RFC 9562) — millisecond-precision, time-sorted."""
    timestamp_ms = int(time.time() * 1000)
    rand_bytes = os.urandom(10)

    # Bytes 0-5: 48-bit timestamp (big-endian)
    time_bytes = struct.pack(">Q", timestamp_ms)[2:]  # last 6 bytes of 8

    # Bytes 6-7: version (0111) + 12 bits random
    rand_a = rand_bytes[0:2]
    byte6 = 0x70 | (rand_a[0] & 0x0F)  # version 7
    byte7 = rand_a[1]

    # Bytes 8-15: variant (10) + 62 bits random
    rand_b = bytearray(rand_bytes[2:10])
    rand_b[0] = 0x80 | (rand_b[0] & 0x3F)  # variant 10

    raw = time_bytes + bytes([byte6, byte7]) + bytes(rand_b)
    return uuid.UUID(bytes=raw)


def generate_id(prefix: str) -> str:
    """Generate a prefixed UUIDv7 string.

    >>> generate_id("wf")
    'wf_019539a1-2b3c-7def-8012-3456789abcde'
    """
    return f"{prefix}_{_uuid7()}"


def workflow_id() -> str:
    return generate_id("wf")


def execution_id() -> str:
    return generate_id("exec")


def credential_id() -> str:
    return generate_id("cred")


def folder_id() -> str:
    return generate_id("fold")


def tag_id() -> str:
    return generate_id("tag")


def app_id() -> str:
    return generate_id("app")


def data_table_id() -> str:
    return generate_id("dt")
