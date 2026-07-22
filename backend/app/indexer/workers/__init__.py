"""The background-task entrypoint the API triggers indexing through.

A thin wrapper around `services.indexing_service` — FastAPI's
`BackgroundTasks` stands in for a real task queue in this phase; see ADR
0007 for why, and what a real queue would replace here.
"""
