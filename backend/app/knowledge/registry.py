"""Knowledge Source Registry — static catalog of source types.

Follows the same declarative pattern as the AI Provider Registry:
each source is described once, the API serves the catalog to the UI,
and adding a source is one entry in _SOURCES — no changes to the
router, resolver, or frontend.

A Knowledge Source is a *template*. It declares what transports and
capabilities are available. A Knowledge Connection is a configured
*instance* — the actual endpoint, credentials, and scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Transport(StrEnum):
    """How a connection communicates with the source system."""

    REST = "rest"
    GRAPHQL = "graphql"
    MCP = "mcp"
    SDK = "sdk"
    FILESYSTEM = "filesystem"
    DATABASE = "database"


class AuthMethod(StrEnum):
    """Authentication mechanisms a transport may require."""

    NONE = "none"
    PAT = "pat"  # Personal Access Token
    OAUTH = "oauth"
    API_KEY = "api_key"
    APP_CREDENTIALS = "app_credentials"  # e.g. GitHub App (app_id + private key)
    AWS_CREDENTIALS = "aws_credentials"
    BASIC = "basic"
    CONNECTION_STRING = "connection_string"


@dataclass(frozen=True)
class TransportSpec:
    """One supported transport for a knowledge source."""

    transport: Transport
    label: str
    auth_methods: tuple[AuthMethod, ...] = ()
    # Fields the UI should render for each auth method.
    # Key: AuthMethod value → list of field descriptors.
    auth_fields: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeSourceSpec:
    """Declarative description of one knowledge source type.

    Analogous to ProviderSpec in the AI provider registry — the UI and
    API read this to render source-type information without hardcoding
    any source-specific knowledge.
    """

    key: str
    label: str
    icon: str  # Lucide icon name for the frontend
    description: str
    capabilities: tuple[str, ...] = ()
    transports: tuple[TransportSpec, ...] = ()
    available: bool = True  # False = "coming soon"


# ---------------------------------------------------------------------------
# The Registry
# ---------------------------------------------------------------------------

_SOURCES: tuple[KnowledgeSourceSpec, ...] = (
    KnowledgeSourceSpec(
        key="github",
        label="GitHub",
        icon="GitBranch",
        description="Pull requests, commits, code, and repository metadata",
        capabilities=(
            "Repository Discovery",
            "Pull Request Metadata",
            "Commit History",
            "Code Search",
            "Repository Search",
        ),
        transports=(
            TransportSpec(
                transport=Transport.REST,
                label="REST API (api.github.com)",
                auth_methods=(AuthMethod.OAUTH, AuthMethod.PAT),
                auth_fields={
                    "oauth": [],  # handled by OAuth flow
                    "pat": ["base_url", "token"],
                },
            ),
            TransportSpec(
                transport=Transport.MCP,
                label="MCP Server",
                auth_methods=(AuthMethod.PAT,),
                auth_fields={"pat": ["server_url", "token"]},
            ),
            TransportSpec(
                transport=Transport.GRAPHQL,
                label="GraphQL API",
                auth_methods=(AuthMethod.PAT,),
                auth_fields={"pat": ["endpoint", "token"]},
            ),
        ),
    ),
    KnowledgeSourceSpec(
        key="jira",
        label="Jira",
        icon="FileText",
        description="Issues, epics, sprint context, and project metadata",
        capabilities=(
            "Issue Search",
            "Sprint Context",
            "Epic Dependencies",
            "Project Metadata",
        ),
        transports=(
            TransportSpec(
                transport=Transport.REST,
                label="REST API (Jira Cloud / Server)",
                auth_methods=(AuthMethod.BASIC, AuthMethod.PAT, AuthMethod.OAUTH),
                auth_fields={
                    "basic": ["base_url", "email", "api_token"],
                    "pat": ["base_url", "token"],
                    "oauth": ["base_url"],
                },
            ),
            TransportSpec(
                transport=Transport.MCP,
                label="MCP Server",
                auth_methods=(AuthMethod.API_KEY,),
                auth_fields={"api_key": ["server_url", "api_key"]},
            ),
        ),
    ),
    KnowledgeSourceSpec(
        key="confluence",
        label="Confluence",
        icon="MessageSquare",
        description="Technical documentation, ADRs, runbooks, and design docs",
        capabilities=(
            "Documentation Search",
            "ADR Search",
            "Runbooks",
            "Architecture Docs",
        ),
        transports=(
            TransportSpec(
                transport=Transport.REST,
                label="REST API (Confluence Cloud / Server)",
                auth_methods=(AuthMethod.BASIC, AuthMethod.PAT),
                auth_fields={
                    "basic": ["base_url", "email", "api_token"],
                    "pat": ["base_url", "token"],
                },
            ),
            TransportSpec(
                transport=Transport.MCP,
                label="MCP Server",
                auth_methods=(AuthMethod.API_KEY,),
                auth_fields={"api_key": ["server_url", "api_key"]},
            ),
        ),
    ),
    KnowledgeSourceSpec(
        key="neo4j",
        label="Neo4j Knowledge Graph",
        icon="Database",
        description="Architecture relationships, dependency graphs, cross-repo links",
        capabilities=(
            "Dependency Graph",
            "Architecture Context",
            "Repository Graph",
            "Cross-Repository Links",
        ),
        transports=(
            TransportSpec(
                transport=Transport.DATABASE,
                label="Bolt Driver",
                auth_methods=(AuthMethod.BASIC,),
                auth_fields={"basic": ["uri", "username", "password"]},
            ),
        ),
    ),
    KnowledgeSourceSpec(
        key="filesystem",
        label="Filesystem",
        icon="FolderOpen",
        description="Local repository clones and workspace files",
        capabilities=(
            "Code Indexing",
            "File Discovery",
            "AST Parsing",
            "Language Detection",
        ),
        transports=(
            TransportSpec(
                transport=Transport.FILESYSTEM,
                label="Local Path",
                auth_methods=(AuthMethod.NONE,),
                auth_fields={"none": ["root_path"]},
            ),
        ),
    ),
    KnowledgeSourceSpec(
        key="s3",
        label="S3 / Object Storage",
        icon="Cloud",
        description="Artifacts, build outputs, and large file storage",
        capabilities=(
            "Artifact Storage",
            "Build Output Search",
            "Configuration Archive",
        ),
        transports=(
            TransportSpec(
                transport=Transport.SDK,
                label="AWS SDK (boto3)",
                auth_methods=(AuthMethod.AWS_CREDENTIALS,),
                auth_fields={"aws_credentials": ["region", "bucket"]},
            ),
        ),
        available=False,
    ),
    KnowledgeSourceSpec(
        key="gitlab",
        label="GitLab",
        icon="GitBranch",
        description="Repositories, merge requests, CI/CD pipelines",
        capabilities=(
            "Repository Discovery",
            "Merge Request Metadata",
            "Pipeline Status",
            "Code Search",
        ),
        transports=(
            TransportSpec(
                transport=Transport.REST,
                label="REST API v4",
                auth_methods=(AuthMethod.PAT, AuthMethod.OAUTH),
                auth_fields={
                    "pat": ["base_url", "token"],
                    "oauth": ["base_url"],
                },
            ),
        ),
        available=False,
    ),
    KnowledgeSourceSpec(
        key="azure_devops",
        label="Azure DevOps",
        icon="Cloud",
        description="Repos, work items, boards, and pipelines",
        capabilities=(
            "Repository Discovery",
            "Work Item Search",
            "Pipeline Status",
            "Board Context",
        ),
        transports=(
            TransportSpec(
                transport=Transport.REST,
                label="REST API",
                auth_methods=(AuthMethod.PAT,),
                auth_fields={"pat": ["organization_url", "token"]},
            ),
        ),
        available=False,
    ),
)

_BY_KEY: dict[str, KnowledgeSourceSpec] = {s.key: s for s in _SOURCES}


def all_sources() -> tuple[KnowledgeSourceSpec, ...]:
    """Every declared source, available or not."""
    return _SOURCES


def get_source(key: str) -> KnowledgeSourceSpec | None:
    return _BY_KEY.get(key)


def require_source(key: str) -> KnowledgeSourceSpec:
    spec = get_source(key)
    if spec is None:
        raise ValueError(f"Unknown knowledge source: '{key}'")
    return spec
