"""``studio.secret("KEY")`` — a reference to a secret; the value never enters user code."""

from __future__ import annotations


class SecretRef(str):
    """A ``str`` whose value is ``{{ $vars.KEY }}``; the broker substitutes the real value."""

    key: str

    def __new__(cls, key: str) -> "SecretRef":
        if not key or not (key[0].isalpha() or key[0] == "_") or not all(c.isalnum() or c == "_" for c in key):
            raise ValueError(f"invalid secret key {key!r}")
        obj = super().__new__(cls, "{{ $vars.%s }}" % key)
        obj.key = key
        return obj

    def __repr__(self) -> str:
        return f"studio.secret({self.key!r})"


def secret(key: str) -> SecretRef:
    return SecretRef(key)
