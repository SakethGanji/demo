"""Send Email node - sends emails via a configurable email service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import markdown

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


# =============================================================================
# EMAIL SERVICE - Implement send_email() below with your email provider
# =============================================================================


# Default email styling for HTML/Markdown emails
EMAIL_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }
h1 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
h2 { color: #34495e; margin-top: 24px; }
h3 { color: #7f8c8d; }
a { color: #3498db; }
code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-family: 'SF Mono', Monaco, monospace; font-size: 0.9em; }
pre { background: #2d2d2d; color: #f8f8f2; padding: 16px; border-radius: 6px; overflow-x: auto; }
pre code { background: none; padding: 0; color: inherit; }
blockquote { border-left: 4px solid #3498db; margin: 0; padding-left: 16px; color: #666; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; }
th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
th { background: #f8f9fa; font-weight: 600; }
tr:nth-child(even) { background: #f8f9fa; }
ul, ol { padding-left: 24px; }
li { margin: 4px 0; }
hr { border: none; border-top: 1px solid #eee; margin: 24px 0; }
"""


def render_markdown_to_html(markdown_text: str, wrap_in_template: bool = True) -> str:
    """Convert markdown to styled HTML for emails."""
    # Use markdown with useful extensions
    html_content = markdown.markdown(
        markdown_text,
        extensions=[
            'tables',           # | col1 | col2 |
            'fenced_code',      # ```code blocks```
            'codehilite',       # syntax highlighting
            'nl2br',            # newlines to <br>
            'sane_lists',       # better list handling
            'smarty',           # smart quotes, dashes
        ],
        extension_configs={
            'codehilite': {'css_class': 'highlight', 'guess_lang': False}
        }
    )

    if not wrap_in_template:
        return html_content

    # Wrap in email-friendly HTML template
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>{EMAIL_CSS}</style>
</head>
<body>
{html_content}
</body>
</html>"""


def wrap_html_in_template(html_content: str) -> str:
    """Wrap raw HTML in email template with styling."""
    # Check if already has html/body tags
    if '<html' in html_content.lower() or '<body' in html_content.lower():
        return html_content

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>{EMAIL_CSS}</style>
</head>
<body>
{html_content}
</body>
</html>"""


async def send_email(
    to: str,
    subject: str,
    body: str,
    body_format: Literal["plain", "html", "markdown"] = "plain",
    from_email: str | None = None,
) -> dict:
    """
    Send an email. Implement this with your email provider.

    Args:
        to: Recipient email address
        subject: Email subject line
        body: Email body (plain text, HTML, or Markdown based on body_format)
        body_format: "plain", "html", or "markdown"
        from_email: Optional sender email

    Returns:
        dict with: success, message_id, error (if failed), html_body (if applicable)

    Example SMTP implementation:

        import aiosmtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = from_email or SMTP_USERNAME
        msg["To"] = to
        msg["Subject"] = subject

        if body_format == "plain":
            msg.set_content(body)
        else:
            html = html_body
            msg.set_content(body)  # plain text fallback
            msg.add_alternative(html, subtype="html")

        await aiosmtplib.send(msg, hostname=SMTP_HOST, port=587,
                              username=SMTP_USERNAME, password=SMTP_PASSWORD, start_tls=True)
        return {"success": True, "message_id": msg["Message-ID"]}

    Example HTTP API implementation:

        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://api.your-email-service.com/send", json={
                "to": to, "subject": subject, "body": body, "html_body": html_body
            }, headers={"Authorization": "Bearer API_KEY"})
            return {"success": True, "message_id": resp.json().get("id")}
    """
    # Convert body to HTML if needed
    html_body = None
    if body_format == "markdown":
        html_body = render_markdown_to_html(body)
    elif body_format == "html":
        html_body = wrap_html_in_template(body)

    # TODO: Implement actual email sending here
    # For now, return stub response
    return {
        "success": True,
        "message_id": "stub-not-sent",
        "html_body": html_body,
    }


class SendEmailNode(BaseNode):
    """Send Email node - sends emails via configured email service."""

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
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "message_id": {"type": "string"},
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
                placeholder="recipient@example.com",
                description="Recipient email (supports expressions)",
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
                    NodePropertyOption(name="HTML", value="html", description="Raw HTML content"),
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

        results: list[ND] = []
        items = input_data if input_data else [ND(json={})]

        for idx, item in enumerate(items):
            expr_context = ExpressionEngine.create_context(
                input_data, context.node_states, context.execution_id, idx
            )

            to_email = str(expression_engine.resolve(to_email_raw, expr_context))
            subject = str(expression_engine.resolve(subject_raw, expr_context))
            body = str(expression_engine.resolve(body_raw, expr_context))

            if not to_email:
                raise ValueError("To Email is required")
            if not subject:
                raise ValueError("Subject is required")

            try:
                result = await send_email(
                    to=to_email,
                    subject=subject,
                    body=body,
                    body_format=body_format,
                )
                results.append(ND(json={
                    "success": result.get("success", False),
                    "to": to_email,
                    "subject": subject,
                    "body": body,
                    "message_id": result.get("message_id"),
                }))
            except Exception as e:
                results.append(ND(json={
                    "success": False,
                    "to": to_email,
                    "subject": subject,
                    "error": str(e),
                }))

        return self.output(results)
