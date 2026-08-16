"""Outbound notifications on dataset lifecycle events.

What turns a passive store into a platform: a validation failure or a promotion
reaches a channel instead of waiting for someone to poll for it.

Payloads are deliberately thin — event type, resource ids, counts. They carry
no dataset rows, both because that is the standing control-plane rule and
because a webhook body is the last place sensitive values should end up.
Receivers fetch what they need through the authorized API.
"""
