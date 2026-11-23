# AML alert triage

On a schedule (every 15 minutes) the workflow lists the open AML alerts and scores each one with the
sub-workflow **Score alert** (run once per alert). **Score alert** adds the weights of the keyword rules in `aml_rules`
to the customer's risk score, fails closed (score 100) when the risk service is down, and asks an AI step for a
one-line summary. Alerts scoring at least `AML_HIGH_SCORE` (default 70) go to the compliance queue; the others are
closed with an "auto-closed" note. An alert that cannot be scored is flagged for a person.

Needs the secrets `AML_API_KEY` and `QUEUES_API_KEY`. The schedule starts only when you publish the workflow.
