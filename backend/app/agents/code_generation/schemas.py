"""Code Generation Agent output schema — the T in AgentOutput[T].

Structured execution artifact: a list of files to create/modify/delete,
a proposed commit message, and a summary of what was generated.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GeneratedFile(BaseModel):
    """A single file operation in the generated code artifact."""

    path: str
    operation: str  # "create" | "modify" | "delete"
    content: str = ""  # empty for "delete" operations


class GeneratedCodeResult(BaseModel):
    """Structured output from the Code Generation Agent.

    Stored as the AgentStep result — consumed by later stages
    (create_branch, commit_changes) when those agents are implemented.

    `confidence` is always the deterministic score computed by
    `app.agents.code_generation.confidence.calculate_confidence` from this
    run's own verification evidence — never the LLM's self-reported value.
    See `CodeGenerationAgent.run` for where it is overwritten.
    """

    goal: str
    executive_summary: str
    repository: str  # e.g. "owner/repo-name"
    commit_message: str
    files: list[GeneratedFile] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)

    prompt_version: str = "1.0"
