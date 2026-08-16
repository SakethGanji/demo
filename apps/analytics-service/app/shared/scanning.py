"""Pluggable upload malware/content scanning.

The default is a no-op that accepts everything. A deployment wires in a real
scanner (ClamAV via clamd, an ICAP proxy, a cloud AV API) by implementing
``FileScanner.scan`` and registering it with ``set_scanner``. The upload path
calls ``scan_upload`` before a file is accepted for processing.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ScanResult:
    ok: bool
    reason: str | None = None


class FileScanner(ABC):
    @abstractmethod
    async def scan(self, path: Path, *, filename: str | None = None) -> ScanResult:
        ...


class NoopScanner(FileScanner):
    """Accepts everything. Logs at debug so it's visible that no AV is active."""

    async def scan(self, path: Path, *, filename: str | None = None) -> ScanResult:
        logger.debug("NoopScanner: accepting %s (no malware scanning configured)", filename or path)
        return ScanResult(ok=True)


_scanner: FileScanner = NoopScanner()


def set_scanner(scanner: FileScanner) -> None:
    """Register the active scanner (call at startup to enable real AV)."""
    global _scanner
    _scanner = scanner


async def scan_upload(path: Path, *, filename: str | None = None) -> ScanResult:
    """Scan an uploaded file. Returns a ScanResult; callers reject on ``not ok``."""
    return await _scanner.scan(path, filename=filename)
