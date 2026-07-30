"""The shapes provider adapters exchange.

`Reference` is a deterministically-recognized pointer at something external;
`ResolvedArtifact` is what a provider returns after resolving one. Both are
provider-agnostic on purpose — nothing consuming them can tell whether the
content came from Jira, Confluence, GitHub, the knowledge graph, or a provider
that doesn't exist yet.

The reasoning engine converts these into `Fact`s and `EvidenceRecord`s the
moment it receives them (see `reasoning.investigators`), so these types exist
only at the transport boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.agents._contract import Evidence


class ReferenceType(StrEnum):
    """What kind of external reference was detected in the raw prompt."""

    JIRA_ISSUE = "jira_issue"
    CONFLUENCE_PAGE = "confluence_page"
    GITHUB_REPOSITORY = "github_repository"
    GITHUB_ISSUE = "github_issue"
    GITHUB_PULL_REQUEST = "github_pull_request"
    GITHUB_FILE = "github_file"
    LOCAL_REPOSITORY = "local_repository"


class ProviderCapability(StrEnum):
    """What a registered provider is able to resolve. A provider declares
    one or more of these; the pipeline asks "which registered provider
    can resolve this reference/need" by capability, never by a
    hardcoded provider name."""

    ISSUE_TRACKER = "issue_tracker"
    DOCUMENTATION = "documentation"
    SOURCE_CONTROL = "source_control"
    REPOSITORY_METADATA = "repository_metadata"
    KNOWLEDGE_SOURCE = "knowledge_source"
    GRAPH = "graph"


@dataclass(frozen=True)
class Reference:
    """A structured, deterministically-detected reference to something
    external — a Jira key, a GitHub PR URL, and so on. Detection never
    touches the network; it only recognizes a shape in text (see
    `reference_detection.detect_references`)."""

    type: ReferenceType
    provider: str
    confidence: float
    raw_value: str
    normalized_value: str


@dataclass
class ResolvedArtifact:
    """One normalized piece of retrieved context, regardless of which
    provider or transport produced it. `text` is what's safe to fold
    into the planning prompt (already wrapped as untrusted content and
    secret-redacted by the provider that produced it); `evidence` is
    the contract-shaped record of the retrieval itself."""

    provider: str
    capability: ProviderCapability
    reference: Reference | None
    title: str
    text: str
    evidence: Evidence
    raw: dict[str, Any] = field(default_factory=dict)
