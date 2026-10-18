# Refund triage with manager approval

A customer fills in a refund form. The sub-workflow **Refund eligibility** classifies the reason with an AI step and
compares the amount with the automatic limit of the customer's segment (`refund_limits`). Eligible refunds are paid
straight away; the rest wait for a manager in the **Manager approval** step, which lives in the top-level workflow
because approvals never run inside a sub-workflow. The customer is told the outcome through the
`customer-notify` template, which is installed together with this one.

Needs the secret `REFUNDS_API_KEY`. The entry workflow is installed as a draft: publish it when you are ready.
