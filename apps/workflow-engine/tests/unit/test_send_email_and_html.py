"""SendEmail over SMTP (against a real in-process SMTP server), its not-configured path, the HTML node, the shared report
renderer and the ``dataset-report-email`` template."""

from __future__ import annotations

import base64
import email
import email.policy
import json
import socket
import socketserver
import threading
from datetime import datetime

import pytest

from src.core import config
from src.engine.effects import effect_of
from src.engine.expression_engine import ExpressionEngine, expression_engine
from src.engine.node_registry import node_registry, register_all_nodes
from src.engine.types import ExecutionContext, NodeData, NodeDefinition, Workflow
from src.nodes.integrations.send_email import NOT_CONFIGURED_NOTE, EmailSendError, parse_recipients
from src.services.template_bundle import TEMPLATES_DIR, load_catalog
from src.utils.report_html import html_to_text, is_full_document, markdown_to_html, render_markdown_report, report_document

register_all_nodes()

REPORT_MD = """## Overview
The dataset has **120 rows** and 3 columns. See [the docs](https://docs.example.com/profile).
**Notes:**
- email has gaps
- 2 duplicate rows

## Column highlights
| Column | Type | Missing % |
|---|---|---:|
| email | text | 5.83 |
| amount | numeric | 0 |

```
max > mean + 3 * std
```
"""


# ── a real SMTP server, in process ───────────────────────────────────────────────────────────


class _SmtpHandler(socketserver.StreamRequestHandler):
    """Just enough RFC 5321 for smtplib: EHLO, AUTH PLAIN, MAIL, RCPT, DATA, RSET, NOOP, QUIT."""

    def reply(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self) -> None:
        srv = self.server
        mail_from, rcpts = None, []
        self.reply("220 test-smtp ready")
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode().rstrip("\r\n")
            cmd = line.split(" ", 1)[0].upper()
            arg = line[len(cmd):].strip()
            srv.commands.append(cmd)
            if cmd in ("EHLO", "HELO"):
                self.reply("250-test-smtp")
                self.reply("250 AUTH PLAIN" if srv.require_auth else "250 8BITMIME")
            elif cmd == "AUTH":
                _, _, b64 = arg.partition(" ")
                _, user, pw = base64.b64decode(b64).decode().split("\0")
                if (user, pw) == srv.credentials:
                    srv.authed = True
                    self.reply("235 2.7.0 Authentication successful")
                else:
                    self.reply("535 5.7.8 Authentication credentials invalid")
            elif cmd == "MAIL":
                if srv.require_auth and not srv.authed:
                    self.reply("530 5.7.0 Authentication required")
                    continue
                mail_from = arg.split(":", 1)[1].strip().strip("<>").split(">")[0]
                self.reply("250 OK")
            elif cmd == "RCPT":
                addr = arg.split(":", 1)[1].strip().strip("<>").split(">")[0]
                if addr in srv.refuse:
                    self.reply("550 5.1.1 mailbox unavailable")
                else:
                    rcpts.append(addr)
                    self.reply("250 OK")
            elif cmd == "DATA":
                self.reply("354 End data with <CR><LF>.<CR><LF>")
                chunks = []
                while True:
                    data_line = self.rfile.readline()
                    if data_line in (b".\r\n", b""):
                        break
                    chunks.append(data_line[1:] if data_line.startswith(b"..") else data_line)
                srv.messages.append({"from": mail_from, "rcpts": list(rcpts), "data": b"".join(chunks)})
                self.reply("250 OK queued")
            elif cmd == "QUIT":
                self.reply("221 Bye")
                return
            else:
                self.reply("250 OK")


class _SmtpServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _SmtpHandler)
        self.messages: list[dict] = []
        self.commands: list[str] = []
        self.refuse: set[str] = set()
        self.require_auth = False
        self.credentials = ("", "")
        self.authed = False


# ``config.settings`` is read at call time (as the node does): another test reloads ``src.core.config``, which replaces it.


@pytest.fixture
def smtp(monkeypatch):
    srv = _SmtpServer()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(config.settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(config.settings, "smtp_port", srv.server_address[1])
    monkeypatch.setattr(config.settings, "smtp_from", "studio@example.com")
    monkeypatch.setattr(config.settings, "smtp_username", None)
    monkeypatch.setattr(config.settings, "smtp_password", None)
    monkeypatch.setattr(config.settings, "smtp_starttls", False)
    monkeypatch.setattr(config.settings, "smtp_ssl", False)
    monkeypatch.setattr(config.settings, "smtp_timeout_s", 5.0)
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def no_smtp(monkeypatch):
    monkeypatch.setattr(config.settings, "smtp_host", None)


# ── running a node ───────────────────────────────────────────────────────────────────────────


def _ctx() -> ExecutionContext:
    return ExecutionContext(workflow=Workflow(name="t", nodes=[], connections=[]), execution_id="e", start_time=datetime.now(), mode="manual")


async def run_node(node_type: str, params: dict, items: list[dict] | None = None) -> list[dict]:
    node = node_registry.get(node_type)
    nd = NodeDefinition(name="n", type=node_type, parameters=params)
    res = await node.execute(_ctx(), nd, [NodeData(json=j) for j in (items if items is not None else [{}])])
    return [i.json for i in res.outputs["main"]]


def parse(raw: bytes) -> email.message.EmailMessage:
    return email.message_from_bytes(raw, policy=email.policy.default)


# ── SMTP ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_markdown_email_is_sent_as_multipart_alternative_to_every_recipient(smtp):
    out = await run_node("SendEmail", {"toEmail": "{{ $json.to }}", "subject": "Report for {{ $json.team }}",
                                       "bodyFormat": "markdown", "body": REPORT_MD},
                         [{"to": "a@example.com, b@example.com", "team": "risk"}])
    assert len(smtp.messages) == 1
    m = smtp.messages[0]
    assert m["from"] == "studio@example.com" and m["rcpts"] == ["a@example.com", "b@example.com"]
    msg = parse(m["data"])
    assert msg["Subject"] == "Report for risk" and msg["To"] == "a@example.com, b@example.com" and msg["From"] == "studio@example.com"
    assert msg.get_content_type() == "multipart/alternative"
    parts = {p.get_content_type(): p.get_content() for p in msg.iter_parts()}
    assert set(parts) == {"text/plain", "text/html"}
    assert "<table" in parts["text/html"] and "<h2" in parts["text/html"] and "Column highlights" in parts["text/html"]
    assert "| email | text | 5.83 |" in parts["text/plain"] and "<" not in parts["text/plain"].replace("max > mean", "")
    row = out[0]
    assert row["sent"] is True and row["success"] is True and "note" not in row
    assert row["to"] == "a@example.com, b@example.com" and row["subject"] == "Report for risk"
    assert row["message_id"] == msg["Message-ID"] and row["message_id"].startswith("<") and row["message_id"].endswith("@example.com>")
    assert parts["text/html"].replace("\r\n", "\n").strip() == row["html"].strip()
    assert is_full_document(row["html"])


@pytest.mark.anyio
async def test_a_full_html_document_body_is_sent_as_it_is(smtp):
    doc = render_markdown_report("# Weekly\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", "Weekly")
    out = await run_node("SendEmail", {"toEmail": "ops@example.com", "subject": "Weekly", "bodyFormat": "html", "body": "{{ $json.html }}"},
                         [{"html": doc}])
    assert out[0]["html"] == doc
    html_part = parse(smtp.messages[0]["data"]).get_body(("html",)).get_content()
    assert html_part.replace("\r\n", "\n").strip() == doc.strip()
    assert "| 1 | 2 |" in parse(smtp.messages[0]["data"]).get_body(("plain",)).get_content()


@pytest.mark.anyio
async def test_an_html_fragment_and_plain_text_get_the_document_shell(smtp):
    out = await run_node("SendEmail", {"toEmail": "ops@example.com", "subject": "S", "bodyFormat": "html", "body": "<p>Hi <b>there</b></p>"})
    assert is_full_document(out[0]["html"]) and "<p style=" in out[0]["html"] and "Hi <b>there</b>" in out[0]["html"]
    out = await run_node("SendEmail", {"toEmail": "ops@example.com", "subject": "S", "bodyFormat": "plain", "body": "line 1\n<line 2>"})
    assert "&lt;line 2&gt;" in out[0]["html"]
    plain = parse(smtp.messages[1]["data"]).get_body(("plain",)).get_content()
    assert plain.replace("\r\n", "\n").strip() == "line 1\n<line 2>"


@pytest.mark.anyio
async def test_login_is_used_when_a_username_is_set(smtp, monkeypatch):
    smtp.require_auth, smtp.credentials = True, ("mailer", "pa55-word")
    monkeypatch.setattr(config.settings, "smtp_username", "mailer")
    monkeypatch.setattr(config.settings, "smtp_password", "pa55-word")
    out = await run_node("SendEmail", {"toEmail": "ops@example.com", "subject": "S", "bodyFormat": "plain", "body": "x"})
    assert out[0]["sent"] is True and "AUTH" in smtp.commands and len(smtp.messages) == 1


@pytest.mark.anyio
async def test_a_refused_login_is_a_node_error_with_the_server_text_and_never_the_password(smtp, monkeypatch):
    smtp.require_auth, smtp.credentials = True, ("mailer", "right")
    monkeypatch.setattr(config.settings, "smtp_username", "mailer")
    monkeypatch.setattr(config.settings, "smtp_password", "wrong-secret")
    with pytest.raises(EmailSendError) as e:
        await run_node("SendEmail", {"toEmail": "ops@example.com", "subject": "S", "bodyFormat": "plain", "body": "x"})
    assert "535" in str(e.value) and "Authentication credentials invalid" in str(e.value)
    assert "wrong-secret" not in str(e.value) and e.value.code == "EMAIL_SEND_FAILED"
    assert smtp.messages == []


@pytest.mark.anyio
async def test_a_refused_recipient_is_a_node_error_naming_it(smtp):
    smtp.refuse = {"gone@example.com"}
    with pytest.raises(EmailSendError) as e:
        await run_node("SendEmail", {"toEmail": "gone@example.com", "subject": "S", "bodyFormat": "plain", "body": "x"})
    assert "gone@example.com" in str(e.value) and "550" in str(e.value) and "mailbox unavailable" in str(e.value)


@pytest.mark.anyio
async def test_an_unreachable_server_is_a_node_error(monkeypatch):
    with socket.socket() as s:  # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(config.settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(config.settings, "smtp_port", port)
    monkeypatch.setattr(config.settings, "smtp_ssl", False)
    monkeypatch.setattr(config.settings, "smtp_password", None)
    monkeypatch.setattr(config.settings, "smtp_timeout_s", 2.0)
    with pytest.raises(EmailSendError) as e:
        await run_node("SendEmail", {"toEmail": "ops@example.com", "subject": "S", "bodyFormat": "plain", "body": "x"})
    assert f"127.0.0.1:{port}" in str(e.value)


def test_recipients_are_split_on_commas_and_checked():
    assert parse_recipients(" a@example.com,b@example.com ; c@example.com,") == ["a@example.com", "b@example.com", "c@example.com"]
    with pytest.raises(ValueError, match="not-an-address"):
        parse_recipients("a@example.com, not-an-address")
    with pytest.raises(ValueError, match="required"):
        parse_recipients(" , ")


# ── not configured ───────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_without_smtp_the_email_is_rendered_but_not_sent_and_the_node_succeeds(no_smtp):
    out = await run_node("SendEmail", {"toEmail": "a@example.com,b@example.com", "subject": "Dataset report",
                                       "bodyFormat": "markdown", "body": REPORT_MD})
    row = out[0]
    assert row["sent"] is False and row["success"] is False and row["message_id"] is None
    assert row["note"] == NOT_CONFIGURED_NOTE == "SMTP isn't configured (WORKFLOW_SMTP_HOST), so the email was rendered but not sent."
    assert row["to"] == "a@example.com, b@example.com" and row["subject"] == "Dataset report"
    assert is_full_document(row["html"]) and "<table" in row["html"] and "<title>Dataset report</title>" in row["html"]
    assert "stub-not-sent" not in json.dumps(row)


def test_settings_read_the_workflow_smtp_env_names(monkeypatch):
    from src.core.config import Settings

    for k, v in {"WORKFLOW_SMTP_HOST": "mail.example.com", "WORKFLOW_SMTP_PORT": "587", "WORKFLOW_SMTP_USERNAME": "u",
                 "WORKFLOW_SMTP_PASSWORD": "p", "WORKFLOW_SMTP_FROM": "reports@example.com", "WORKFLOW_SMTP_STARTTLS": "true",
                 "WORKFLOW_SMTP_SSL": "false"}.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert (s.smtp_host, s.smtp_port, s.smtp_username, s.smtp_password, s.smtp_from, s.smtp_starttls, s.smtp_ssl) == (
        "mail.example.com", 587, "u", "p", "reports@example.com", True, False)
    for k in ("WORKFLOW_SMTP_HOST", "WORKFLOW_SMTP_PORT", "WORKFLOW_SMTP_FROM", "WORKFLOW_SMTP_STARTTLS", "WORKFLOW_SMTP_SSL"):
        monkeypatch.delenv(k)
    d = Settings(_env_file=None)
    assert (d.smtp_host, d.smtp_port, d.smtp_from, d.smtp_starttls, d.smtp_ssl) == (None, 1025, "studio@localhost", False, False)


def test_send_email_stays_a_network_effect_and_html_is_pure():
    assert effect_of("SendEmail") == "network"
    assert effect_of("Html") == "none"
    from src.broker.policy import EGRESS_NONE

    assert "Html" in EGRESS_NONE and "SendEmail" not in EGRESS_NONE


# ── the HTML node ────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_html_node_markdown_mode_renders_headings_tables_lists_code_and_links():
    out = await run_node("Html", {"content": "{{ $json.response }}", "title": "Dataset report"}, [{"response": REPORT_MD}])
    row = out[0]
    assert set(row) == {"html", "text", "title"} and row["title"] == "Dataset report"
    h = row["html"]
    assert h.startswith("<!DOCTYPE html>") and "<title>Dataset report</title>" in h and "max-width: 760px" in h and "<style>" in h
    assert "<h1" in h and ">Dataset report</h1>" in h  # the title heads a body without its own h1
    assert ">Overview</h2>" in h and ">Column highlights</h2>" in h
    assert "<table" in h and "<th" in h and ">email</td>" in h and "text-align: right;" in h
    assert "<ul" in h and ">email has gaps</li>" in h  # a list right under a line of text still parses as a list
    assert "<strong" in h and ">120 rows</strong>" in h
    assert '<a href="https://docs.example.com/profile"' in h and "<pre" in h and "max &gt; mean + 3 * std" in h
    t = row["text"]
    assert t.startswith("Dataset report\n\nOverview") and "| Column | Type | Missing % |" in t and "| email | text | 5.83 |" in t
    assert "- email has gaps" in t and "the docs (https://docs.example.com/profile)" in t and "<" not in t.replace("max > mean", "")


@pytest.mark.anyio
async def test_html_node_html_mode_wraps_a_fragment_and_keeps_a_full_document():
    out = await run_node("Html", {"mode": "html", "content": "<h2>Totals</h2><table><tr><td>1</td></tr></table>", "title": "T"})
    h = out[0]["html"]
    assert h.startswith("<!DOCTYPE html>") and "<title>T</title>" in h and "<h2 style=" in h and "<td style=" in h
    assert out[0]["text"] == "T\n\nTotals\n\n| 1 |"
    doc = "<!doctype html><html><head><title>Mine</title></head><body><p>As is</p></body></html>"
    out = await run_node("Html", {"mode": "html", "content": doc})
    assert out[0]["html"] == doc and out[0]["text"] == "As is" and out[0]["title"] == ""


@pytest.mark.anyio
async def test_html_node_runs_once_per_item_and_an_h1_in_the_content_is_not_doubled():
    out = await run_node("Html", {"content": "# {{ $json.name }}\n\nBody", "title": "Ignored as heading"}, [{"name": "A"}, {"name": "B"}])
    assert [r["text"] for r in out] == ["A\n\nBody", "B\n\nBody"]
    assert all(r["html"].count("<h1") == 1 for r in out)


@pytest.mark.anyio
async def test_html_node_output_feeds_send_email_unchanged(smtp):
    report = (await run_node("Html", {"content": REPORT_MD, "title": "Dataset report"}))[0]
    sent = (await run_node("SendEmail", {"toEmail": "ops@example.com", "subject": "Dataset report", "bodyFormat": "html",
                                         "body": "{{ $json.html }}"}, [report]))[0]
    assert sent["html"] == report["html"] and sent["sent"] is True


def test_html_node_contract_is_declared():
    from src.engine.contracts import node_contract

    o = node_contract(NodeDefinition(name="n", type="Html"), {"main": {"type": "unknown"}})["outputs"]["main"]
    assert set(o["properties"]) == {"html", "text", "title"} and set(o["required"]) == {"html", "text", "title"}
    desc = node_registry.get("Html").node_description
    assert desc.display_name == "HTML" and [p.name for p in desc.properties] == ["mode", "content", "title"]
    assert [o.value for o in desc.properties[0].options] == ["markdown", "html"] and desc.properties[0].default == "markdown"


def test_renderer_inlines_styles_and_keeps_a_tags_own_style_last():
    h = markdown_to_html("| a |\n|:-:|\n| 1 |\n| 2 |\n")
    assert 'style="border: 1px solid #d1d5db; padding: 8px 10px; text-align: left; background: #f3f4f6; font-weight: 600; text-align: center;"' in h
    assert h.count('<tr style="background: #f9fafb;">') == 1  # the second body row is striped
    assert "`x`" not in markdown_to_html("use `x`") and "<code style=" in markdown_to_html("use `x`")
    assert report_document("<html><body>x</body></html>") == "<html><body>x</body></html>"
    assert html_to_text("<ol><li>one</li><li>two</li></ol><p>a<br>b</p>") == "1. one\n2. two\n\na\nb"


# ── the template ─────────────────────────────────────────────────────────────────────────────


def test_the_dataset_report_template_validates_and_chains_the_five_steps():
    lt = load_catalog(TEMPLATES_DIR, strict=True)["dataset-report-email"]
    b = lt.bundle
    assert b.name == "Email a dataset report" and b.version == "1.0.0" and b.category == "data"
    assert [v.name for v in b.requires.variables] == ["REPORT_TO"] and b.requires.variables[0].default.endswith("@example.com")
    (w,) = b.workflows
    assert w.role == "entry" and w.publish is False
    d = w.definition
    nodes = {n["name"]: n for n in d["nodes"]}
    assert [n["type"] for n in d["nodes"]] == ["Start", "Profile", "LLMChat", "Html", "SendEmail"]
    order = [n["name"] for n in d["nodes"]]
    assert [(c["source_node"], c["target_node"]) for c in d["connections"]] == list(zip(order, order[1:]))
    prof, llm, html, mail = (nodes[x]["parameters"] for x in order[1:])
    assert prof["sourceType"] == "dataset" and prof["datasetId"] == ""
    llm_models = [o.value for o in node_registry.get("LLMChat").node_description.properties[0].options]
    assert llm["model"] == "gemini-3.8-flash" and llm["model"] in llm_models and llm["maxTokens"] >= 4096
    for section in ("Overview", "Data quality", "Column highlights", "Recommendations", "pipe table", "Markdown"):
        assert section in llm["systemPrompt"]
    assert html == {"mode": "markdown", "content": "{{ $json.response }}", "title": "Dataset report"}
    assert mail == {"toEmail": "{{ $vars.REPORT_TO }}", "subject": "Dataset report", "bodyFormat": "html", "body": "{{ $json.html }}"}
    assert len(d["tests"]) >= 3 and {t["category"] for t in d["tests"]} >= {"normal", "ext_error"}


def test_the_template_user_message_carries_the_profile_json():
    lt = load_catalog(TEMPLATES_DIR, strict=True)["dataset-report-email"]
    llm = next(n for n in lt.bundle.workflows[0].definition["nodes"] if n["type"] == "LLMChat")["parameters"]
    profile = {"row_count": 3, "columns": [{"name": "a", "null_count": 1}]}
    ctx = ExpressionEngine.create_context([NodeData(json=profile)], {}, "e", 0)
    msg = expression_engine.resolve(llm["userMessage"], ctx)
    assert "[Expression Error" not in msg
    assert json.loads(msg.split("\n\n", 1)[1]) == profile
