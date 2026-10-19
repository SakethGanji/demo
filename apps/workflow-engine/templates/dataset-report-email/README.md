# Email a dataset report

Profiles one of the project's datasets, has an LLM write a short data-quality report in Markdown (Overview, Data quality,
Column highlights as a table, Recommendations), renders it as a styled HTML document and emails it.

1. Import a CSV, Parquet or Excel file on the project's Datasets page.
2. Open the **Profile dataset** step and pick it under **Dataset**.
3. Set the project variable **REPORT_TO** (installed with the placeholder `reports@example.com`) to the recipient;
   several addresses are separated by commas.
4. Run the workflow. **Build report** returns `{html, text, title}`; **Email report** sends it and returns
   `{sent, to, subject, html, message_id}`.

Sending needs SMTP (`WORKFLOW_SMTP_HOST`, `WORKFLOW_SMTP_PORT`, `WORKFLOW_SMTP_USERNAME`, `WORKFLOW_SMTP_PASSWORD`,
`WORKFLOW_SMTP_FROM`, `WORKFLOW_SMTP_STARTTLS`, `WORKFLOW_SMTP_SSL`). Without it the email is rendered but not sent:
**Email report** returns `sent: false` with a note. For local development run `docker compose up -d mailpit` and set
`WORKFLOW_SMTP_HOST=localhost`; the mail shows up at http://localhost:8025.

**Analyse profile** uses `gemini-3.8-flash`; change the model on the step if you use another provider.
