"""Pydantic request/response schemas — the API's wire format.

Kept separate from `app.models` (the ORM layer) so the wire format can
evolve independently of how data is persisted.
"""
