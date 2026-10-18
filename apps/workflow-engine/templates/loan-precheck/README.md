# Loan pre-check

A customer asks for a loan amount, term and monthly income. The workflow gets the credit risk band from the risk
service, looks up the band's policy in `loan_policy` (largest amount and payment-to-income ratio), and answers
"pre-approved" with a fee quote or "not pre-approved" with the reasons. If the risk service is down the answer is
"not pre-approved": the check fails closed.

Needs the secrets `RISK_API_KEY` and `FEES_API_KEY`.
