"""Send Email node - sends an email over SMTP (multipart: a text part and an HTML part).

SMTP comes from the settings (``WORKFLOW_SMTP_HOST``, ``_PORT``, ``_USERNAME``, ``_PASSWORD``, ``_FROM``, ``_STARTTLS``, ``_SSL``).
With no host configured the email is rendered but not sent, and the item says so (``sent: false`` and a ``note``). For local
development the mailpit service in docker-compose.yml catches everything on port 1025 and shows it at http://localhost:8025.
"""

from __future__ import annotations

import asyncio
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import TYPE_CHECKING, Any, Literal

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)
from ...utils.report_html import html_to_text, plain_to_html, render_markdown_report, report_document

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


NOT_CONFIGURED_NOTE = "SMTP isn't configured (WORKFLOW_SMTP_HOST), so the email was rendered but not sent."
_ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+$")


class EmailSendError(RuntimeError):
    """The SMTP server refused or could not be reached — surfaced as the node's error with code ``EMAIL_SEND_FAILED``."""

    def __init__(self, message: str, code: str = "EMAIL_SEND_FAILED") -> None:
        super().__init__(message)
        self.code = code


# ── rendering ────────────────────────────────────────────────────────────────────────────────


def render_markdown_to_html(markdown_text: str, wrap_in_template: bool = True) -> str:
    """Markdown → the styled report document (the same renderer as the HTML node)."""
    if not wrap_in_template:
        from ...utils.report_html import markdown_to_html

        return markdown_to_html(markdown_text)
    return render_markdown_report(markdown_text)


def wrap_html_in_template(html_content: str) -> str:
    """An HTML fragment in the report document; a full document (e.g. an HTML node's ``html``) is kept as it is."""
    return report_document(html_content)


def render_email(body: str, body_format: str, subject: str = "") -> tuple[str, str]:
    """``(html, text)`` of an email body: the HTML part and its plain-text alternative. The subject titles the document but
    does not head the body (the mail client already shows it)."""
    if body_format == "markdown":
        html = render_markdown_report(body, subject, heading=False)
    elif body_format == "html":
        html = report_document(body, subject, heading=False)
    else:
        return report_document(plain_to_html(body), subject, style_fragment=False, heading=False), body
    return html, html_to_text(html)


def parse_recipients(to: str) -> list[str]:
    """``a@x.example, b@x.example`` → the addresses; a malformed one is an error naming it."""
    out = [a.strip() for a in re.split(r"[,;]", to or "") if a.strip()]
    if not out:
        raise ValueError("To Email is required")
    bad = [a for a in out if not _ADDRESS.match(a)]
    if bad:
        raise ValueError(f"Not an email address: {', '.join(repr(b) for b in bad)}")
    return out


# ── sending ──────────────────────────────────────────────────────────────────────────────────


def _settings() -> Any:
    from ...core.config import settings

    return settings


def build_message(sender: str, recipients: list[str], subject: str, text: str, html: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    domain = sender.rsplit("@", 1)[-1].strip("> ") if "@" in sender else None
    msg["Message-ID"] = make_msgid(domain=domain or None)
    msg.set_content(text or " ")
    msg.add_alternative(html, subtype="html")
    return msg


def _deliver(cfg: Any, msg: EmailMessage, recipients: list[str]) -> None:
    """Blocking SMTP delivery (run in a worker thread)."""
    host, port, timeout = cfg.smtp_host, int(cfg.smtp_port), float(getattr(cfg, "smtp_timeout_s", 30.0) or 30.0)
    # The EHLO name is the sender's domain: smtplib's default (socket.getfqdn()) is a reverse DNS lookup that can stall each send.
    sender = str(cfg.smtp_from or "")
    ehlo_name = sender.rsplit("@", 1)[-1].strip("> ") if "@" in sender else "localhost"
    if cfg.smtp_ssl:
        client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, local_hostname=ehlo_name or "localhost", timeout=timeout,
                                                context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(host, port, local_hostname=ehlo_name or "localhost", timeout=timeout)
    try:
        client.ehlo()
        if cfg.smtp_starttls and not cfg.smtp_ssl:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if cfg.smtp_username:
            client.login(cfg.smtp_username, cfg.smtp_password or "")
        client.send_message(msg, to_addrs=recipients)  # the envelope sender is the message's From
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def _smtp_error_text(e: BaseException, password: str | None) -> str:
    if isinstance(e, smtplib.SMTPResponseException):
        detail = e.smtp_error.decode("utf-8", "replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        text = f"{e.smtp_code} {detail}".strip()
    elif isinstance(e, smtplib.SMTPRecipientsRefused):
        text = "; ".join(
            f"{addr}: {code} {(m.decode('utf-8', 'replace') if isinstance(m, bytes) else m)}" for addr, (code, m) in e.recipients.items()
        )
    else:
        text = str(e) or type(e).__name__
    if password:
        text = text.replace(password, "***")
    return text


async def send_email(
    to: str,
    subject: str,
    body: str,
    body_format: Literal["plain", "html", "markdown"] = "plain",
    from_email: str | None = None,
) -> dict[str, Any]:
    """Render the email and send it over SMTP when ``WORKFLOW_SMTP_HOST`` is set.

    Returns ``{sent, to (list), subject, html, text, message_id, note?}``. Raises ``ValueError`` for a bad recipient and
    ``EmailSendError`` (with the SMTP error text, never the password) when the server refuses or cannot be reached.
    """
    recipients = parse_recipients(to)
    html, text = render_email(body, body_format, subject)
    cfg = _settings()
    out: dict[str, Any] = {"to": recipients, "subject": subject, "html": html, "text": text}
    if not cfg.smtp_host:
        return {**out, "sent": False, "message_id": None, "note": NOT_CONFIGURED_NOTE}

    message_id = await deliver_email(recipients, subject, text, html, from_email=from_email)
    return {**out, "sent": True, "message_id": message_id}


async def deliver_email(
    recipients: list[str], subject: str, text: str, html: str, from_email: str | None = None,
) -> str:
    """Send an already rendered email over the configured SMTP server (``WORKFLOW_SMTP_HOST`` must be set). Returns the
    Message-ID; raises ``EmailSendError`` (the SMTP error text, never the password). Shared by SendEmail and the Approval
    node's approver notification."""
    cfg = _settings()
    sender = from_email or cfg.smtp_from
    msg = build_message(sender, recipients, subject, text, html)
    try:
        await asyncio.to_thread(_deliver, cfg, msg, recipients)
    except (smtplib.SMTPException, OSError) as e:
        raise EmailSendError(
            f"Couldn't send the email via {cfg.smtp_host}:{cfg.smtp_port}: {_smtp_error_text(e, cfg.smtp_password)}"
        ) from None
    return msg["Message-ID"]


from ...engine import shapes as sh

class SendEmailNode(BaseNode):
    """Send Email node - sends emails over SMTP."""

    node_description = NodeTypeDescription(
        name="SendEmail",
        display_name="Send Email",
        description="Sends an email (plain text, HTML, or Markdown)",
        icon="fa:envelope",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "sent": {"type": "boolean"},
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "html": {"type": "string"},
                        "message_id": {"type": "string"},
                        "note": {"type": "string"},
                    },
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="To",
                name="toEmail",
                type="string",
                default="",
                required=True,
                placeholder="recipient@example.com, other@example.com",
                description="Recipient email; several separated by commas (supports expressions)",
            ),
            NodeProperty(
                display_name="Subject",
                name="subject",
                type="string",
                default="",
                required=True,
                placeholder="Email subject",
                description="Subject line (supports expressions)",
            ),
            NodeProperty(
                display_name="Format",
                name="bodyFormat",
                type="options",
                default="markdown",
                required=True,
                options=[
                    NodePropertyOption(name="Markdown", value="markdown", description="Write in Markdown, sends as styled HTML"),
                    NodePropertyOption(name="HTML", value="html", description="An HTML fragment, or a full document such as an HTML step's {{ $json.html }}"),
                    NodePropertyOption(name="Plain Text", value="plain", description="Plain text only"),
                ],
            ),
            NodeProperty(
                display_name="Body",
                name="body",
                type="string",
                default="",
                required=True,
                placeholder="# Hello\n\nYour email content here...",
                description="Email content (supports expressions)",
                type_options={"rows": 10},
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "SendEmail"

    @property
    def description(self) -> str:
        return "Sends an email (plain text, HTML, or Markdown)"

    def contract(self, params, inputs, lookups=None):
        """Declared: ``{success, sent, to, subject, html, message_id}`` always (``note`` when SMTP isn't configured);
        ``error`` is what an ``on_error: continue`` item carries."""
        s = sh.scalar
        shape = sh.object_of(
            {
                "success": s("boolean", "declared"),
                "sent": s("boolean", "declared"),
                "to": s("string", "declared"),
                "subject": s("string", "declared"),
                "body": s("string", "declared"),
                "html": s("string", "declared"),
                "message_id": s("string", "declared", nullable=True),
                "note": s("string", "declared"),
                "error": s("string", "declared"),
            },
            ["success", "sent", "to", "subject", "html"], "declared")
        return sh.make_contract(inputs, {"main": shape})

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData as ND
        from ...engine.expression_engine import ExpressionEngine, expression_engine

        to_email_raw = self.get_parameter(node_definition, "toEmail", "")
        subject_raw = self.get_parameter(node_definition, "subject", "")
        body_format = self.get_parameter(node_definition, "bodyFormat", "markdown")
        body_raw = self.get_parameter(node_definition, "body", "")
        if body_format not in ("markdown", "html", "plain"):
            raise ValueError(f"Unknown format '{body_format}': use 'markdown', 'html' or 'plain'")

        results: list[ND] = []
        items = input_data if input_data else [ND(json={})]

        for idx, item in enumerate(items):
            expr_context = ExpressionEngine.create_context(
                input_data, context.node_states, context.execution_id, idx
            )

            to_value = expression_engine.resolve(to_email_raw, expr_context)
            to_email = ", ".join(str(a) for a in to_value) if isinstance(to_value, list) else str(to_value or "")
            subject = str(expression_engine.resolve(subject_raw, expr_context) or "")
            body = expression_engine.resolve(body_raw, expr_context)
            body = "" if body is None else str(body)

            if not to_email.strip():
                raise ValueError("To Email is required")
            if not subject:
                raise ValueError("Subject is required")

            result = await send_email(to=to_email, subject=subject, body=body, body_format=body_format)
            row: dict[str, Any] = {
                "success": result["sent"],
                "sent": result["sent"],
                "to": ", ".join(result["to"]),
                "subject": subject,
                "body": body,
                "html": result["html"],
                "message_id": result["message_id"],
            }
            if result.get("note"):
                row["note"] = result["note"]
            results.append(ND(json=row))

        return self.output(results)
