"""Report rendering shared by the HTML node and SendEmail: Markdown → a standalone, email-safe HTML document, and HTML → text.

One renderer, so a report looks the same in the HTML node's output, in a browser and in a mail client:

* ``markdown_to_html(md)``   a body fragment (headings, lists, bold, pipe tables, code, links), every tag inline-styled
* ``report_document(body)``  the fragment in the document shell (title, embedded stylesheet, a centred 760px column);
                             a body that is already a full document is returned unchanged
* ``html_to_text(html)``     the plain-text version (the email's text part, the HTML node's ``text``)

Mail clients drop ``<style>`` blocks unevenly, so the look lives in ``style=""`` attributes; the embedded stylesheet repeats it for
browsers. Pure: no I/O.
"""

from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser

import markdown as _markdown

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

#: inline style per tag (``pre > code`` and ``tbody`` even rows are handled separately)
TAG_STYLES: dict[str, str] = {
    "h1": "font-size: 26px; line-height: 1.25; margin: 0 0 16px; padding-bottom: 8px; border-bottom: 2px solid #e5e7eb; color: #111827;",
    "h2": "font-size: 20px; line-height: 1.3; margin: 28px 0 12px; color: #111827;",
    "h3": "font-size: 17px; line-height: 1.35; margin: 22px 0 8px; color: #1f2937;",
    "h4": "font-size: 15px; line-height: 1.4; margin: 18px 0 6px; color: #374151;",
    "p": "margin: 0 0 14px;",
    "ul": "margin: 0 0 14px; padding-left: 24px;",
    "ol": "margin: 0 0 14px; padding-left: 24px;",
    "li": "margin: 4px 0;",
    "a": "color: #2563eb; text-decoration: underline;",
    "strong": "font-weight: 600; color: #111827;",
    "blockquote": "margin: 0 0 14px; padding: 4px 0 4px 14px; border-left: 4px solid #d1d5db; color: #4b5563;",
    "hr": "border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;",
    "table": "border-collapse: collapse; width: 100%; margin: 0 0 16px; font-size: 14px;",
    "th": "border: 1px solid #d1d5db; padding: 8px 10px; text-align: left; background: #f3f4f6; font-weight: 600;",
    "td": "border: 1px solid #d1d5db; padding: 8px 10px; text-align: left; vertical-align: top;",
    "code": f"font-family: {MONO}; font-size: 0.9em; background: #f3f4f6; padding: 1px 5px; border-radius: 4px;",
    "pre": f"font-family: {MONO}; font-size: 13px; line-height: 1.5; background: #f3f4f6; padding: 12px 14px; border-radius: 6px; "
           "overflow-x: auto; margin: 0 0 14px; white-space: pre-wrap;",
}
PRE_CODE_STYLE = "font-family: inherit; font-size: inherit; background: none; padding: 0;"
EVEN_ROW_STYLE = "background: #f9fafb;"
BODY_STYLE = f"margin: 0; padding: 0; background: #ffffff; color: #1f2937; font-family: {FONT}; font-size: 15px; line-height: 1.6;"
COLUMN_STYLE = "max-width: 760px; margin: 0 auto; padding: 24px 20px;"

STYLESHEET = f"""
body {{ {BODY_STYLE} }}
.report {{ {COLUMN_STYLE} }}
{chr(10).join(f"{tag} {{ {style} }}" for tag, style in TAG_STYLES.items())}
pre code {{ {PRE_CODE_STYLE} }}
tbody tr:nth-child(even) {{ {EVEN_ROW_STYLE} }}
img {{ max-width: 100%; }}
""".strip()

_FULL_DOCUMENT = re.compile(r"^\s*(<!doctype\s|<html[\s>])", re.I)
_LIST_LINE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+\S")
_FENCE = re.compile(r"^\s*(```|~~~)")


def is_full_document(html_text: str) -> bool:
    """True for ``<!DOCTYPE ...>`` / ``<html>`` documents (and anything with a ``<body>``): those are sent as they are."""
    return bool(_FULL_DOCUMENT.match(html_text or "")) or "<body" in (html_text or "").lower()


def _normalize_markdown(md: str) -> str:
    """Make LLM-style Markdown parse the way it reads.

    Python-Markdown wants a blank line before a list or a table and four-space nesting; models write ``**Notes:**`` straight
    above ``- item`` and nest with two spaces. Code fences are left alone.
    """
    out: list[str] = []
    prev_kind = "blank"
    in_fence = False
    for line in (md or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE.match(line):
            if not in_fence and prev_kind not in ("blank",):
                out.append("")
            in_fence = not in_fence
            out.append(line)
            prev_kind = "text"
            continue
        if in_fence:
            out.append(line)
            continue
        stripped = line.strip()
        if not stripped:
            kind = "blank"
        elif stripped.startswith("|"):
            kind = "table"
        elif _LIST_LINE.match(line):
            kind = "list"
            indent = len(line) - len(line.lstrip(" "))
            if indent and indent % 4:
                line = " " * (indent * 2) + line.lstrip(" ")
        else:
            kind = "text"
        if kind in ("table", "list") and prev_kind not in ("blank", kind) and not (kind == "list" and prev_kind == "list"):
            out.append("")
        if kind == "text" and prev_kind == "table":
            out.append("")  # a table ends at the first non-table line
        out.append(line)
        prev_kind = kind
    return "\n".join(out)


_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)(\s[^<>]*?)?(/?)>")
_STYLE_ATTR = re.compile(r"""\sstyle\s*=\s*(["'])(.*?)\1""", re.I | re.S)


def inline_styles(fragment: str) -> str:
    """Add the report's inline style to every styled tag; a tag's own ``style`` stays last, so it wins."""
    stack: list[str] = []
    row = {"in_tbody": False, "n": 0}

    def sub(m: re.Match[str]) -> str:
        closing, tag, attrs, self_close = m.group(1), m.group(2).lower(), m.group(3) or "", m.group(4)
        if closing:
            if tag in stack:
                while stack and stack.pop() != tag:
                    pass
            if tag == "tbody":
                row["in_tbody"] = False
            return m.group(0)
        if tag == "tbody":
            row.update(in_tbody=True, n=0)
        style = TAG_STYLES.get(tag, "")
        if tag == "code" and "pre" in stack:
            style = PRE_CODE_STYLE
        if tag == "tr" and row["in_tbody"]:
            row["n"] += 1
            style = EVEN_ROW_STYLE if row["n"] % 2 == 0 else ""
        if not self_close and tag not in ("br", "hr", "img", "meta", "link", "input"):
            stack.append(tag)
        if not style:
            return m.group(0)
        own = _STYLE_ATTR.search(attrs)
        if own:
            merged = f"{style} {own.group(2).strip()}"
            attrs = attrs[:own.start()] + f' style="{merged}"' + attrs[own.end():]
        else:
            attrs = f'{attrs} style="{style}"'
        return f"<{tag}{attrs}{self_close}>"

    return _TAG.sub(sub, fragment)


def markdown_to_html(md: str) -> str:
    """Markdown → an inline-styled HTML fragment (headings, lists, bold/italic, pipe tables, fenced and inline code, links)."""
    fragment = _markdown.markdown(_normalize_markdown(md), extensions=["tables", "fenced_code", "sane_lists"], output_format="html")
    return inline_styles(fragment)


def plain_to_html(text: str) -> str:
    """Plain text → an escaped fragment that keeps its line breaks."""
    return f'<div style="white-space: pre-wrap;">{_html.escape(text or "")}</div>'


def report_document(body_html: str, title: str | None = None, *, style_fragment: bool = True, heading: bool = True) -> str:
    """The standalone document around ``body_html``. A full document is returned unchanged. ``title`` names the document and,
    with ``heading`` and when the body has no ``<h1>`` of its own, heads it."""
    if is_full_document(body_html):
        return body_html
    body = inline_styles(body_html) if style_fragment else body_html
    title = (title or "").strip()
    if heading and title and not re.search(r"<h1[\s>]", body, re.I):
        body = f'<h1 style="{TAG_STYLES["h1"]}">{_html.escape(title)}</h1>\n{body}'
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{_html.escape(title or 'Report')}</title>\n"
        f"<style>\n{STYLESHEET}\n</style>\n"
        "</head>\n"
        f'<body style="{BODY_STYLE}">\n<div class="report" style="{COLUMN_STYLE}">\n{body}\n</div>\n</body>\n</html>'
    )


def render_markdown_report(md: str, title: str | None = None, *, heading: bool = True) -> str:
    """Markdown → the full report document."""
    return report_document(markdown_to_html(md), title, style_fragment=False, heading=heading)


# ── HTML → text ──────────────────────────────────────────────────────────────────────────────

_BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "blockquote", "pre", "hr", "section", "article"}
_SKIP = {"style", "script", "head", "title"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.pre = 0
        self.lists: list[list[int | str]] = []  # [kind, counter]
        self.href: list[str | None] = []
        self.cells: list[str] | None = None
        self.cell: list[str] | None = None

    def _emit(self, s: str) -> None:
        if self.cell is not None:
            self.cell.append(s)
        else:
            self.parts.append(s)

    def _newline(self, n: int = 1) -> None:
        if self.cell is not None:
            self.cell.append(" ")
            return
        if not "".join(self.parts).strip():
            return  # nothing written yet: no leading blank lines
        tail = "".join(self.parts[-4:])
        have = len(tail) - len(tail.rstrip("\n"))
        if have < n:
            self.parts.append("\n" * (n - have))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self.skip += 1
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "table", "blockquote", "pre", "hr") or (tag in ("ul", "ol") and not self.lists):
            self._newline(2)
        if tag == "pre":
            self.pre += 1
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0])
        elif tag == "li":
            self._newline(1)
            depth = max(len(self.lists) - 1, 0)
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] = int(self.lists[-1][1]) + 1
                self._emit("  " * depth + f"{self.lists[-1][1]}. ")
            else:
                self._emit("  " * depth + "- ")
        elif tag == "br":
            self._emit("\n")
        elif tag == "hr":
            self._emit("-" * 40)
            self._newline(2)
        elif tag == "tr":
            self.cells = []
        elif tag in ("td", "th"):
            self.cell = []
        elif tag == "a":
            self.href.append(dict(attrs).get("href"))

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self.skip = max(self.skip - 1, 0)
            return
        if tag == "pre":
            self.pre = max(self.pre - 1, 0)
        if tag in ("ul", "ol") and self.lists:
            self.lists.pop()
            if not self.lists:
                self._newline(2)
        elif tag in ("td", "th") and self.cell is not None:
            if self.cells is not None:
                self.cells.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.cells is not None:
            self.parts.append("| " + " | ".join(self.cells) + " |\n")
            self.cells = None
        elif tag == "a" and self.href:
            href = self.href.pop()
            if href and not href.startswith("#") and not href.startswith("mailto:"):
                self._emit(f" ({href})")
        elif tag in _BLOCKS:
            self._newline(2)

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.pre:
            self._emit(data)
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip() and (not self.parts or self.parts[-1].endswith("\n")):
            return
        if self.parts and self.parts[-1].endswith("\n"):
            text = text.lstrip()
        self._emit(text)


def html_to_text(html_text: str) -> str:
    """A readable plain-text version: headings and paragraphs on their own lines, ``- `` / ``1. `` lists, ``| a | b |`` table
    rows, ``text (url)`` links."""
    p = _TextExtractor()
    p.feed(html_text or "")
    p.close()
    text = "".join(p.parts)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()
