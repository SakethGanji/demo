# KYC review with compliance approval

The sub-workflow **Screen customer** calls the sanctions, identity and credit-risk services and turns the results
into one rating (`low`, `medium` or `high`) using the score ranges in `kyc_risk_thresholds`. A check that fails
never lowers the rating: the customer goes to review. The top-level **KYC review** verifies low risk customers
at once and sends everyone else to a compliance officer (the Approval step), then updates the customer's KYC status
and tells the customer through the `customer-notify` template.

Needs the secret `KYC_API_KEY` (used by the top-level workflow; the sub-workflow calls the internal checks without a key).
