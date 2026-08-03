"""HTTP API layer — versioning, error envelope, and pagination conventions.

Everything the outside world touches goes through here so the contract stays
consistent: one ``/api/v1`` prefix, one RFC 7807 ``application/problem+json``
error shape, and one ``Page`` envelope for every list endpoint.
"""
