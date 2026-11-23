# Email a dataset report after approval

The dataset report from **Email a dataset report**, with a person in the loop: the report is profiled, written by an LLM
and rendered as HTML, then waits in Approvals until a reviewer approves it. Only an approved report is emailed.

1. Import a CSV, Parquet or Excel file on the project's Datasets page.
2. Open the **Profile dataset** step and pick it under **Dataset**.
3. Set the project variables **REPORT_TO** (installed as `reports@example.com`), who receives the report, and
   **APPROVER_EMAIL** (installed as `approver@example.com`), who is asked to review it. Several addresses are separated by
   commas.
4. Run the workflow. **Review report** pauses the run and creates a task, "Review the data report before it's sent", that
   shows the report title and a short text preview. The approver is emailed a link to it.
5. Approve the task in Approvals: **Email report** sends the report. Rejecting it, or leaving it for two days (`due_in: 2d`,
   the **Timed out** port), ends the run at **Report not sent** without an email.

The approver email is a convenience: when it can't be sent (no SMTP, a refused send, a bad address) the task is still created
and the run still waits. The task detail shows what happened under `notify`. The link points at `WORKFLOW_STUDIO_URL`
(default `http://localhost:5174`).

Sending needs SMTP (`WORKFLOW_SMTP_HOST`, `WORKFLOW_SMTP_PORT`, `WORKFLOW_SMTP_USERNAME`, `WORKFLOW_SMTP_PASSWORD`,
`WORKFLOW_SMTP_FROM`, `WORKFLOW_SMTP_STARTTLS`, `WORKFLOW_SMTP_SSL`). For local development run `docker compose up -d mailpit`
and set `WORKFLOW_SMTP_HOST=localhost`; both emails show up at http://localhost:8025.

**Analyse profile** uses `gemini-3.8-flash`; change the model on the step if you use another provider.
