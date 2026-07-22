"""The AI analysis layer: reasons about a proposed change against the
dependency graph and explains the impact.

This package is a **consumer** of deterministic analysis produced by
``app.analysis``.  It never duplicates deterministic logic.

Subpackages
-----------
interfaces/   Ports (ABCs) consumed by the rest of the application.
providers/    Concrete LLM provider adapters (OpenAI, Anthropic, …).
services/     Orchestration services that compose providers + deterministic data.
schemas/      Pydantic request/response schemas for the AI layer.
models/       SQLAlchemy models specific to AI operations (prompt logs, etc.).
prompts/      Prompt templates and builders.
"""
