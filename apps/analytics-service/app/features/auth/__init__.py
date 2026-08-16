"""Identity & authorization.

POC identity: callers pass ``X-User-Id`` (see ``deps.get_principal``); there is
no password/token handling. Team-scoped RBAC (``permissions.py``) is enforced
server-side regardless of how the principal was identified, so a real IdP
(SSO/JWT) can replace the header scheme later without touching the RBAC layer.
"""
