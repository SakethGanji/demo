"""Un-escape code that arrives with LITERAL backslash-n / backslash-t as line separators (LLM tool-call JSON), without
touching escapes that sit inside string literals or comments.

The old rewrite was ``code.replace("\\n", "\n")`` over the whole text, which turned ``x = "a\\nb"`` into a string
literal broken across two lines. A backslash-n OUTSIDE a string is never valid Python, so rewriting only there is safe.

``sandbox/runtime/runner.py`` carries a verbatim copy of this function (it runs alone inside bwrap and cannot import the
package); ``tests/unit/test_stage6_b3_product_fixes.py`` checks both copies agree.
"""

from __future__ import annotations


def unescape_code(code: str) -> str:
    if "\\n" not in code and "\\t" not in code:
        return code
    out: list[str] = []
    i, n = 0, len(code)
    quote: str | None = None  # the open string's delimiter (', ", ''' or """)
    comment = False
    while i < n:
        c = code[i]
        if quote is not None:
            if c == "\\" and i + 1 < n:  # an escape inside a string stays exactly as written
                out.append(code[i:i + 2])
                i += 2
                continue
            if code.startswith(quote, i):
                out.append(quote)
                i += len(quote)
                quote = None
                continue
            out.append(c)
            i += 1
            continue
        if comment:
            if c == "\n":
                comment = False
            elif c == "\\" and code[i + 1:i + 2] in ("n", "t"):
                comment = False  # a literal separator ends the comment, as a real newline would
            else:
                out.append(c)
                i += 1
                continue
        if c == "\\" and i + 1 < n and code[i + 1] in ("n", "t"):
            out.append("\n" if code[i + 1] == "n" else "\t")
            i += 2
            continue
        if c == "#":
            comment = True
        elif c in ("'", '"'):
            quote = c * 3 if code.startswith(c * 3, i) else c
            out.append(quote)
            i += len(quote)
            continue
        out.append(c)
        i += 1
    return "".join(out)
