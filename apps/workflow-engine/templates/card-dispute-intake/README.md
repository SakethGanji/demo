# Card dispute intake

Your card system posts a dispute to the webhook (`customer_id`, `card_last4`, `merchant`, `amount`, `description`,
`reason_code`, `email`). The workflow opens a case, looks the reason up in `dispute_reason_codes` and, if the code
means fraud, blocks the card and gives a provisional credit. The customer gets a message through `customer-notify`.
When the dispute service fails or refuses the case, a ticket goes to the cards queue and the caller gets 202.

Needs the secrets `DISPUTES_API_KEY`, `CARDS_API_KEY` and `QUEUES_API_KEY`. The webhook is not live until you
publish the workflow.
