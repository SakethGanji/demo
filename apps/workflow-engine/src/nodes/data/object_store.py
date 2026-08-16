"""Shared in-memory object storage for workflow data persistence."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


# Global in-memory store: namespace -> key -> value
_object_store: dict[str, dict[str, Any]] = defaultdict(dict)


def get_value(namespace: str, key: str | None = None) -> Any:
    """
    Get value from store.

    Args:
        namespace: Storage namespace for isolation
        key: Key to retrieve. If None, returns entire namespace dict.

    Returns:
        The stored value, entire namespace dict, or None if not found.
    """
    if key:
        return _object_store[namespace].get(key)
    return dict(_object_store[namespace])


def set_value(namespace: str, key: str, value: Any) -> None:
    """
    Set a single key-value pair in the store.

    Args:
        namespace: Storage namespace for isolation
        key: Key to store
        value: Value to store
    """
    _object_store[namespace][key] = value


def merge_values(namespace: str, data: dict[str, Any]) -> None:
    """
    Merge a dictionary into the namespace.

    Args:
        namespace: Storage namespace for isolation
        data: Dictionary to merge into the namespace
    """
    _object_store[namespace].update(data)


def clear_namespace(namespace: str) -> None:
    """
    Clear all data in a namespace.

    Args:
        namespace: Storage namespace to clear
    """
    _object_store[namespace] = {}


def get_all_namespaces() -> list[str]:
    """Get list of all namespaces with data."""
    return list(_object_store.keys())
