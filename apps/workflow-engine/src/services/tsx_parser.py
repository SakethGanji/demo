"""TSX/TS parser — uses tree-sitter to extract structural information.

Parses TSX files and returns definitions (components, functions, hooks,
types, interfaces), exports, imports, and leading doc comments.
"""

from __future__ import annotations

import re
from typing import Any

import tree_sitter_typescript as ts_typescript
from tree_sitter import Language, Parser

TSX_LANGUAGE = Language(ts_typescript.language_tsx())
_parser = Parser(TSX_LANGUAGE)

# Wrapper function names that still produce a "component"
_WRAPPER_NAMES = {"memo", "forwardRef", "React.memo", "React.forwardRef"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_tsx_file(content: str) -> dict[str, Any]:
    """Parse a TSX/TS file and extract structural information.

    Never raises — always returns a result dict with ``parse_status``.
    """
    empty: dict[str, Any] = {
        "file_comment": None,
        "definitions": [],
        "exports": [],
        "imports": [],
        "parse_status": "error",
    }

    try:
        tree = _parser.parse(content.encode("utf-8"))
    except Exception:
        return empty

    root = tree.root_node
    if root is None:
        return empty

    partial = False
    file_comment = _extract_file_comment(root)
    definitions: list[dict[str, Any]] = []
    exports: list[str] = []
    imports: list[str] = []

    for child in root.children:
        try:
            _process_top_level(child, definitions, exports, imports, root)
        except Exception:
            partial = True

    return {
        "file_comment": file_comment,
        "definitions": definitions,
        "exports": exports,
        "imports": imports,
        "parse_status": "partial" if partial else "ok",
    }


# ---------------------------------------------------------------------------
# File comment
# ---------------------------------------------------------------------------

def _extract_file_comment(root) -> str | None:
    """Return the first comment in the file if it precedes any code."""
    for child in root.children:
        if child.type == "comment":
            return _clean_comment(child.text.decode("utf-8"))
        # Skip blank lines / whitespace; stop at first non-comment node
        break
    return None


# ---------------------------------------------------------------------------
# Top-level node processing
# ---------------------------------------------------------------------------

def _process_top_level(node, definitions, exports, imports, root):
    ntype = node.type

    if ntype == "import_statement":
        src = _import_source(node)
        if src:
            imports.append(src)
        return

    if ntype == "export_statement":
        _process_export(node, definitions, exports, root)
        return

    defn = _extract_definition(node, exported=False, root=root)
    if defn:
        definitions.append(defn)


def _process_export(node, definitions, exports, root):
    """Handle an export_statement and its inner declaration."""
    is_default = any(
        c.type == "default" or (c.type == "export" and False)
        for c in node.children
    ) or b"default" in node.text[:30]

    # export { Foo, Bar }  — named re-exports
    for child in node.children:
        if child.type == "export_clause":
            for spec in child.children:
                if spec.type == "export_specifier":
                    name_node = spec.child_by_field_name("name")
                    if name_node:
                        exports.append(name_node.text.decode("utf-8"))
            return

    # export function / export const / export type / export interface
    inner = _find_declaration_in_export(node)
    if inner:
        defn = _extract_definition(inner, exported=True, root=root)
        if defn:
            definitions.append(defn)
            exports.append(defn["name"])
            if is_default and defn["name"] != "default":
                exports.append("default")
        elif is_default:
            exports.append("default")
        return

    # export default <expression>
    if is_default:
        exports.append("default")


def _find_declaration_in_export(node):
    """Find the actual declaration node inside an export_statement."""
    for child in node.children:
        if child.type in (
            "function_declaration",
            "lexical_declaration",
            "type_alias_declaration",
            "interface_declaration",
            "class_declaration",
        ):
            return child
    return None


# ---------------------------------------------------------------------------
# Definition extraction
# ---------------------------------------------------------------------------

def _extract_definition(node, *, exported: bool, root) -> dict[str, Any] | None:
    ntype = node.type

    if ntype == "function_declaration":
        return _extract_function_decl(node, exported=exported, root=root)

    if ntype == "lexical_declaration":
        return _extract_lexical_decl(node, exported=exported, root=root)

    if ntype == "type_alias_declaration":
        return _extract_type_decl(node, exported=exported, root=root, kind="type")

    if ntype == "interface_declaration":
        return _extract_type_decl(node, exported=exported, root=root, kind="interface")

    return None


def _extract_function_decl(node, *, exported, root) -> dict[str, Any] | None:
    name_node = node.child_by_field_name("name")
    if not name_node:
        return None
    name = name_node.text.decode("utf-8")
    kind = _classify_name(name)
    sig = _build_function_signature(node)
    doc = _leading_doc(node, root)
    return {
        "name": name,
        "kind": kind,
        "line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
        "doc": doc,
        "signature": sig,
        "exported": exported,
    }


def _extract_lexical_decl(node, *, exported, root) -> dict[str, Any] | None:
    """Handle ``const Foo = ...`` or ``let bar = ...``."""
    for child in node.children:
        if child.type == "variable_declarator":
            name_node = child.child_by_field_name("name")
            if not name_node:
                continue
            name = name_node.text.decode("utf-8")
            value_node = child.child_by_field_name("value")

            kind: str
            if value_node and _is_arrow_or_function(value_node):
                kind = _classify_name(name)
            elif value_node and _is_wrapper_call(value_node):
                kind = "component"
            else:
                kind = "const"

            sig = _build_lexical_signature(node, child, name, value_node)
            doc = _leading_doc(node, root)
            return {
                "name": name,
                "kind": kind,
                "line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "doc": doc,
                "signature": sig,
                "exported": exported,
            }
    return None


def _extract_type_decl(node, *, exported, root, kind) -> dict[str, Any] | None:
    name_node = node.child_by_field_name("name")
    if not name_node:
        return None
    name = name_node.text.decode("utf-8")
    sig = _build_type_signature(node)
    doc = _leading_doc(node, root)
    return {
        "name": name,
        "kind": kind,
        "line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
        "doc": doc,
        "signature": sig,
        "exported": exported,
    }


# ---------------------------------------------------------------------------
# Signature builders
# ---------------------------------------------------------------------------

def _build_function_signature(node) -> str:
    """``function Name(params)`` without the body."""
    text = node.text.decode("utf-8")
    # Take everything up to the first `{` (body start)
    body = node.child_by_field_name("body")
    if body:
        end = body.start_point[0] - node.start_point[0]
        lines = text.split("\n")
        if end == 0:
            # Body on same line
            col = body.start_point[1] - node.start_point[1]
            sig = lines[0][:col].rstrip()
        else:
            sig = " ".join(l.strip() for l in lines[: end + 1])
            # Cut at the body column
            brace_idx = sig.rfind("{")
            if brace_idx > 0:
                sig = sig[:brace_idx].rstrip()
    else:
        sig = text.split("\n")[0]
    return _collapse_whitespace(sig)


def _build_lexical_signature(node, declarator, name, value_node) -> str:
    """``const name = async (params) =>``."""
    keyword = "const"
    for c in node.children:
        if c.type in ("const", "let", "var"):
            keyword = c.type
            break

    if value_node is None:
        return f"{keyword} {name}"

    if _is_arrow_or_function(value_node):
        # Build from the arrow function params
        prefix = f"{keyword} {name} = "
        vtext = value_node.text.decode("utf-8")
        # For arrow functions, take up to `=>`
        arrow_idx = vtext.find("=>")
        if arrow_idx >= 0:
            before_arrow = vtext[:arrow_idx + 2].split("\n")
            sig_part = " ".join(l.strip() for l in before_arrow)
            # Check for async
            if value_node.type == "arrow_function":
                # async may be outside — check parent text
                full = node.text.decode("utf-8")
                if "async" in full.split("=>")[0] and "async" not in sig_part:
                    sig_part = "async " + sig_part
            return _collapse_whitespace(prefix + sig_part).rstrip(" =>") + " =>"
        # function expression
        body = value_node.child_by_field_name("body")
        if body:
            rel = body.start_point[1] - value_node.start_point[1]
            first_line = vtext.split("\n")[0]
            brace = first_line.find("{")
            if brace >= 0:
                return _collapse_whitespace(prefix + first_line[:brace].rstrip())
        return _collapse_whitespace(prefix + vtext.split("\n")[0])

    if _is_wrapper_call(value_node):
        return _collapse_whitespace(f"{keyword} {name} = " + _wrapper_sig(value_node))

    # Plain const
    full = _collapse_whitespace(node.text.decode("utf-8"))
    return full[:120] if len(full) > 120 else full


def _build_type_signature(node) -> str:
    full = _collapse_whitespace(node.text.decode("utf-8"))
    return full[:120] if len(full) > 120 else full


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_arrow_or_function(node) -> bool:
    return node.type in ("arrow_function", "function_expression", "function")


def _is_wrapper_call(node) -> bool:
    """Check if node is memo(...) / forwardRef(...) etc."""
    if node.type != "call_expression":
        return False
    fn = node.child_by_field_name("function")
    if fn is None:
        return False
    fn_text = fn.text.decode("utf-8")
    return fn_text in _WRAPPER_NAMES


def _wrapper_sig(node) -> str:
    fn = node.child_by_field_name("function")
    fn_text = fn.text.decode("utf-8") if fn else "memo"
    return f"{fn_text}(...)"


def _classify_name(name: str) -> str:
    if re.match(r"^use[A-Z]", name):
        return "hook"
    if name[0:1].isupper():
        return "component"
    return "function"


def _leading_doc(node, root) -> str | None:
    """Get the doc comment immediately preceding *node*."""
    prev = node.prev_sibling
    # If prev sibling is not a comment and we're inside an export_statement,
    # check the export_statement's own prev sibling instead.
    if (prev is None or prev.type != "comment") and node.parent and node.parent.type == "export_statement":
        prev = node.parent.prev_sibling
        # Use the export_statement's start line for gap detection
        ref_line = node.parent.start_point[0]
    else:
        ref_line = node.start_point[0]
    if prev is None or prev.type != "comment":
        return None
    # Ensure no blank-line gap
    if prev.end_point[0] < ref_line - 1:
        return None
    return _clean_comment(prev.text.decode("utf-8"))


def _clean_comment(text: str) -> str:
    """Strip comment delimiters and tidy whitespace."""
    # JSDoc block comment
    if text.startswith("/*"):
        text = re.sub(r"^/\*\*?\s*", "", text)
        text = re.sub(r"\s*\*/$", "", text)
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            line = re.sub(r"^\s*\*\s?", "", line)
            cleaned.append(line)
        text = "\n".join(cleaned).strip()
        return text

    # Line comments (// ...)
    lines = text.split("\n")
    cleaned = [re.sub(r"^\s*//\s?", "", l) for l in lines]
    return "\n".join(cleaned).strip()


def _import_source(node) -> str | None:
    """Extract the source string from an import statement."""
    source = node.child_by_field_name("source")
    if source:
        raw = source.text.decode("utf-8")
        # Strip quotes
        return raw.strip("'\"")
    return None


def _collapse_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
