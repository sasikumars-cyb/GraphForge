"""Builds the `PromptSpec` for the Repository Understanding Agent.

Summarizes an already-computed `RepositoryProfile`
(`app.services.engineering_intelligence.contracts.RepositoryProfile`) —
never retrieves, never inspects a repository. Every fact in the prompt
came from `RepositoryProfileService.get_profile`, already run by
`ServiceExecutor` before `build_prompt` is ever called (see
`BaseFrontierAgent.run`).
"""

from __future__ import annotations

import json

from app.agents.frontier.prompt_builder import PromptSpec
from app.agents.llm import STAGE_REPOSITORY_UNDERSTANDING
from app.services.engineering_intelligence.contracts import RepositoryProfile

_SYSTEM_PROMPT = (
    "You are writing a Repository Understanding Report for a software "
    "repository, given its already-computed structural profile: exposed "
    "APIs, owned databases, message queues used, external integrations, "
    "and dependencies. These are FACTS. Do not recompute, dispute, or "
    "invent them, and do not invent APIs, databases, queues, integrations, "
    "or dependencies that are not in the input.\n\n"
    "Respond as JSON matching exactly this shape:\n"
    "{\n"
    '  "executive_summary": "2-3 sentences: what this repository is and why it matters",\n'
    '  "purpose": "1-2 sentences on what problem this repository solves",\n'
    '  "architecture": "1-2 sentences on the architectural role this repository plays '
    '(e.g. API gateway, data-owning service, integration adapter)",\n'
    '  "apis": "1-2 sentences characterizing the exposed API surface",\n'
    '  "data_stores": "1-2 sentences characterizing the databases this repository owns",\n'
    '  "messaging": "1-2 sentences characterizing its queue/topic usage",\n'
    '  "external_integrations": "1-2 sentences characterizing outbound integrations",\n'
    '  "dependencies": "1-2 sentences on which dependencies matter most and why",\n'
    '  "interesting_findings": ["notable observations a reviewer would want to know"]\n'
    "}\n\n"
    "Rules:\n"
    "- Ground every statement in the supplied profile.\n"
    "- If a category is empty (e.g. no databases), say so plainly rather than omitting the "
    "section or inventing content.\n"
    "- interesting_findings should be specific and non-obvious (e.g. an unusually high "
    "dependency count, a repository with APIs but no owned data store), not generic advice."
)


def build_repository_understanding_prompt(profile: RepositoryProfile) -> PromptSpec:
    user_prompt = json.dumps(
        {
            "repository_id": profile.repository_id,
            "apis": list(profile.apis),
            "databases": list(profile.databases),
            "queues": list(profile.queues),
            "integrations": list(profile.integrations),
            "dependencies": list(profile.dependencies),
            "architecture_summary": profile.architecture_summary,
        }
    )
    return PromptSpec(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        stage=STAGE_REPOSITORY_UNDERSTANDING,
    )
