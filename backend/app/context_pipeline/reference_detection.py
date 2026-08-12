"""Deterministic reference detection.

Every pattern here is a shape match (a regex, a URL structure, a bare
identifier convention) — never an LLM call. LLM-assisted discovery only
runs afterward, and only for context detection couldn't find any
reference for at all (see `discovery.py`).

Reuses the exact same extraction functions the Planning Agent used to
call directly (`jira_tool.extract_issue_key`,
`github_tool.extract_pr_or_issue_ref`, `google_drive_tool.
extract_drive_file_id`) plus a Confluence URL pattern and a
local-repository heuristic — nothing here reimplements detection that
already existed, it only gives the result a uniform `Reference` shape.
"""

from __future__ import annotations

import re

from app.context_pipeline.models import Reference, ReferenceType
from app.tools.implementations.github_tool import extract_pr_or_issue_ref
from app.tools.implementations.google_drive_tool import extract_drive_file_id
from app.tools.implementations.jira_tool import extract_issue_key

# A Confluence page URL: "<site>/wiki/spaces/<SPACE>/pages/<id>/<title>" or
# the older "/display/<SPACE>/<title>" form. Only used to detect that a
# Confluence reference exists in the text — actual retrieval is anchored on
# the Jira issue (see confluence provider), since Atlassian's MCP server has
# no free-text search, only graph traversal from a known entity.
#
# `[\w~-]` in the space segment, not just `[\w-]`: Atlassian personal spaces
# (Settings -> Personal Space, created automatically for every user) are
# always keyed "~<accountId>" — a leading tilde `\w` alone can never match,
# so a URL like ".../wiki/spaces/~712020.../pages/123/Some+Page" silently
# failed to match at all before this, not just matched the wrong group.
# Confirmed against a real one: this was reference detection dropping a
# personal-space Confluence link entirely, indistinguishable from "the text
# contains no Confluence link" (KAN-46).
CONFLUENCE_URL_RE = re.compile(
    r"https?://[\w.-]+\.atlassian\.net/wiki/(?:spaces/[\w~-]+/pages/(\d+)|display/[\w-]+/[\w%-]+)"
)

# "owner/repo" with no leading github.com and no trailing #<number> — the
# shorthand form GitHub itself displays and that engineers paste directly,
# distinct from `_SHORTHAND_PATTERN` in github_tool.py which requires the
# trailing issue/PR number. Deliberately excludes dots from both halves —
# repo/owner names essentially never contain one, while file paths
# ("app/main.py") always do, so this is what keeps a source-file reference
# from being misdetected as a repository.
_BARE_REPO_RE = re.compile(r"(?<![\w/-])([A-Za-z0-9][\w-]*)/([A-Za-z0-9][\w-]*)(?![\w/#.-])")

# A local repository is referenced by its bare name (no owner, no slash) —
# e.g. "in ds-databricks-soco-gpc" — matched against names already indexed
# for this user by the caller (see `detect_references`'s `known_repo_names`
# argument), not by shape alone: a bare word has no distinguishing shape,
# so without a known-names list this would false-positive on every noun in
# the prompt.


def detect_references(
    text: str, *, known_repo_names: frozenset[str] = frozenset()
) -> list[Reference]:
    """Return every deterministically-detected reference in `text`.

    Confidence is 1.0 for a structural match (a regex/URL that cannot
    mean anything else) and 0.6 for a heuristic match (a bare repository
    name, which could coincidentally collide with an unrelated word) —
    the pipeline currently treats every reference the same regardless of
    confidence, but the field is populated now so a future consumer
    (e.g. the discovery phase, or a UI that shows "detected references")
    can weight or filter on it without a schema change.
    """
    references: list[Reference] = []

    issue_key = extract_issue_key(text)
    if issue_key is not None:
        references.append(
            Reference(
                type=ReferenceType.JIRA_ISSUE,
                provider="jira",
                confidence=1.0,
                raw_value=issue_key,
                normalized_value=issue_key,
            )
        )

    confluence_match = CONFLUENCE_URL_RE.search(text)
    if confluence_match is not None:
        references.append(
            Reference(
                type=ReferenceType.CONFLUENCE_PAGE,
                provider="confluence",
                confidence=1.0,
                raw_value=confluence_match.group(0),
                normalized_value=confluence_match.group(1) or confluence_match.group(0),
            )
        )

    gh_ref = extract_pr_or_issue_ref(text)
    if gh_ref is not None:
        owner, repo, number = gh_ref
        references.append(
            Reference(
                type=ReferenceType.GITHUB_PULL_REQUEST,
                provider="github",
                confidence=1.0,
                raw_value=f"{owner}/{repo}#{number}",
                normalized_value=f"{owner}/{repo}#{number}",
            )
        )
    else:
        bare_match = _BARE_REPO_RE.search(text)
        if bare_match is not None:
            owner, repo = bare_match.groups()
            references.append(
                Reference(
                    type=ReferenceType.GITHUB_REPOSITORY,
                    provider="github",
                    confidence=0.7,
                    raw_value=bare_match.group(0),
                    normalized_value=f"{owner}/{repo}",
                )
            )

    drive_file_id = extract_drive_file_id(text)
    if drive_file_id is not None:
        references.append(
            Reference(
                type=ReferenceType.GOOGLE_DRIVE_FILE,
                provider="google_drive",
                confidence=1.0,
                raw_value=drive_file_id,
                normalized_value=drive_file_id,
            )
        )

    for name in known_repo_names:
        if name and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text):
            references.append(
                Reference(
                    type=ReferenceType.LOCAL_REPOSITORY,
                    provider="graph",
                    confidence=0.6,
                    raw_value=name,
                    normalized_value=name,
                )
            )

    return references
