"""Code Generation Agent — produces structured execution artifacts.

Consumes an approved Planning workflow's blueprint (via cross-workflow
context from build_stage_context) and produces a GeneratedCodeResult
containing the files to be created/modified, a commit message, and a
summary. Does NOT write to any external system — the result is stored
as an AgentStep artifact only.
"""
