"""Backend-builder support code: patch edits, domain gap questions, the scratch self-test and retrievable templates.

``module_generator`` is the only caller; everything here is pure (no database, no LLM) unless a function says otherwise,
which keeps it unit-testable.
"""
