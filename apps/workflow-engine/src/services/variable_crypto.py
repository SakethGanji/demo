"""Tiny encrypt/decrypt for variables.value when type='secret'.

Reuses the Fernet key from credential_service so we only manage one secret.
Plaintext values (legacy rows or non-secret rows) pass through untouched on read.
"""

from __future__ import annotations

from .credential_service import _get_fernet


def encrypt_secret(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_secret(stored: str) -> str:
    """Decrypt a stored secret. Returns input unchanged on any failure
    (legacy plaintext rows, wrong key, corrupted ciphertext) so a single
    bad value doesn't break a workflow run."""
    try:
        return _get_fernet().decrypt(stored.encode()).decode()
    except Exception:
        return stored
