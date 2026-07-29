"""Phase 6 — LLM-assisted discovery.

Runs only when deterministic reference detection (`reference_detection.
detect_references`) found nothing: a prompt like "fix the login issue
from yesterday" has no Jira key, no URL, no repository name — nothing
for a provider to resolve. In that situation an LLM call can recognize
that more context *would help* and suggest where to look, in a way no
regex ever could.

What this deliberately does NOT do: call a provider. It returns a
recommendation (`AdditionalContextRecommendation`) — which capability to
search and why — for the pipeline (or, today, a human/operator reading
the run's evidence) to act on. None of GraphForge's existing provider
tools expose a free-text search API (Jira/GitHub here fetch by
key/URL/number; Confluence's own MCP server has no search primitive at
all — see confluence_context.py's docstring), so actually executing a
"search for context about X" step is future work, not something this
pass can wire up without inventing a transport-level capability that
doesn't exist yet. See this module's own docstring in the deliverable's
"Future Extensibility" section for what a real search-and-retrieve
implementation would need.
"""

from __future__ import annotations

import logging

from app.agents.llm import invoke_llm_json
from app.agents.prompt_utils import parse_json_response
from app.context_pipeline.models import AdditionalContextRecommendation, ProviderCapability
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You help a planning pipeline decide whether it has enough context to "
    "plan a software change, or whether it should look for more before "
    "planning begins. Respond ONLY with valid JSON: "
    '{"should_search": boolean, "capability": one of '
    '["issue_tracker", "documentation", "source_control", null], '
    '"reasoning": a one-sentence explanation}. '
    'Recommend a capability only when the request is genuinely ambiguous '
    "about what it refers to (e.g. no ticket, PR, or repository named) "
    "and more context would change how it should be planned. If the "
    "request is already concrete and self-contained, set should_search "
    "to false."
)


class DiscoveryLLMError(AppError):
    status_code = 502
    error_code = "context_discovery_llm_error"


_CAPABILITY_BY_KEY = {
    "issue_tracker": ProviderCapability.ISSUE_TRACKER,
    "documentation": ProviderCapability.DOCUMENTATION,
    "source_control": ProviderCapability.SOURCE_CONTROL,
}


async def recommend_additional_context(
    task_description: str,
    *,
    model: str | None,
    stage: str,
) -> AdditionalContextRecommendation | None:
    """Ask whether more context should be sought, and from where.

    Returns None on any LLM failure — this is optional, best-effort
    guidance, never a required step (same policy as the Jira/GitHub/
    Confluence enrichment this pipeline also treats as best-effort).
    """
    try:
        raw = await invoke_llm_json(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=f"Request: {task_description}",
            stage=stage,
            model=model,
            error_cls=DiscoveryLLMError,
        )
        data = parse_json_response(raw, DiscoveryLLMError)
    except DiscoveryLLMError:
        logger.info("context_pipeline_discovery_skipped reason=llm_unavailable")
        return None

    should_search = bool(data.get("should_search", False))
    capability_key = data.get("capability")
    capability = _CAPABILITY_BY_KEY.get(capability_key) if isinstance(capability_key, str) else None
    reasoning = str(data.get("reasoning") or "").strip()

    if should_search and capability is None:
        # Model said "yes, search" but didn't name a capability we recognize
        # — treat as no actionable recommendation rather than guessing one.
        should_search = False

    logger.info(
        "context_pipeline_discovery_decision should_search=%s capability=%s",
        should_search,
        capability.value if capability else None,
    )
    return AdditionalContextRecommendation(
        should_search=should_search,
        capability=capability,
        reasoning=reasoning or "No additional context recommended.",
    )
