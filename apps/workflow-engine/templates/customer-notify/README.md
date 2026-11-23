# Customer notification

A sub-workflow other templates call to tell a customer what happened. It looks a message up in the
`notify_templates` table, fills the `{placeholders}` from `vars`, sends it through the notify API and returns
`{delivered, channel}` (or `{delivered: false, error}` when the service refuses).

Inputs: `to`, `channel` (email or sms), `template` (a row name of `notify_templates`) and optional `vars`.
Add your own rows to `notify_templates`; the other starter templates use
`refund_issued`, `refund_declined`, `refund_failed`, `kyc_approved`, `kyc_declined` and `dispute_received`.
