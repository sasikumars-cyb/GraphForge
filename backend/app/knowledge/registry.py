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

from app.context_pipeline.models import ProviderCapability
from app.core.config import get_settings


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
    """One supported transport for a knowledge source.

    Fields below the UI-facing block are the Tool Registry integration
    metadata (Part 2 of the MCP platform refactor): everything
    `app.tools.setup` needs to activate the matching `ITool` for a
    Knowledge Connection using this transport, without a second,
    hand-maintained map keyed by (source_type, transport). This
    `TransportSpec` — reached via `get_source(source_type).transports` —
    is the single source of truth for that mapping.
    """

    transport: Transport
    label: str
    auth_methods: tuple[AuthMethod, ...] = ()
    # Fields the UI should render for each auth method.
    # Key: AuthMethod value → list of field descriptors.
    auth_fields: dict[str, list[str]] = field(default_factory=dict)
    # The vendor's official hosted MCP server, when one exists and is known
    # to be compatible with this app's bearer-token MCP client. None means
    # "no default" - MCP for that source stays REST-only until an operator
    # supplies one. This is *not* a user-facing field: the UI never renders
    # an MCP transport separately, it just reuses whatever credential the
    # user already entered for REST as the MCP bearer token (see
    # app.tools.setup.sync_knowledge_connection_to_tool).
    known_mcp_endpoint: str | None = None

    # Which `ProviderCapability` values this transport can actually satisfy
    # for this source, e.g. Confluence's MCP transport declares DOCUMENTATION
    # (Teamwork Graph traversal) while its REST transport declares none (CQL
    # search has no live consumer — see ConfluenceTool's removal). This is
    # what `app.knowledge.access_resolver.derive_access_methods` filters on
    # when a caller asks "how do I reach `capability`" rather than "give me
    # every configured transport regardless of what it's for" (the Tool
    # Registry sync's own use, which passes capability=None to skip this
    # filter). Empty means "this transport doesn't currently back any
    # capability a caller resolves by" — not "misconfigured."
    capabilities: tuple[ProviderCapability, ...] = ()

    # --- Tool Registry integration metadata ---
    #
    # The Tool Registry `tool_id` this transport activates when a Knowledge
    # Connection using it is created, updated, or replayed at startup. None
    # means this transport has no corresponding ITool at all (Neo4j's Bolt
    # driver, the local filesystem indexer) — `app.tools.setup` skips it
    # entirely rather than guessing.
    tool_id: str | None = None
    # KnowledgeConnection's generic config/credential field name -> this
    # tool's own (prefixed) config key, e.g.
    # {"base_url": "jira_base_url", "api_token": "jira_api_token"}. Empty
    # means "no fields to translate" (same as tool_id=None, kept separate
    # so a future transport can declare a tool_id with an intentionally
    # empty mapping without that reading as a data-entry mistake).
    credential_field_map: dict[str, str] = field(default_factory=dict)
    # REST TransportSpecs only: the KEY (not tool config key) in this
    # transport's own `credential_field_map` whose value should be reused
    # as the MCP bearer token when auto-wiring this source's REST
    # connection to its sibling MCP TransportSpec's `known_mcp_endpoint` —
    # see app.tools.setup's registration-metadata-driven auto-wire step.
    # None disables auto-wire for this source (or is simply irrelevant on
    # a non-REST TransportSpec).
    auto_wire_credential: str | None = None


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

_settings = get_settings()

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
                known_mcp_endpoint=_settings.github_mcp_default_server_url,
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
                tool_id="jira",
                credential_field_map={
                    "base_url": "jira_base_url",
                    "email": "jira_email",
                    "api_token": "jira_api_token",
                },
                auto_wire_credential="api_token",
                capabilities=(ProviderCapability.ISSUE_TRACKER,),
            ),
            TransportSpec(
                transport=Transport.MCP,
                label="MCP Server",
                auth_methods=(AuthMethod.API_KEY,),
                auth_fields={"api_key": ["server_url", "api_key"]},
                known_mcp_endpoint=_settings.jira_mcp_default_server_url,
                tool_id="jira",
                credential_field_map={
                    "server_url": "jira_mcp_server_url",
                    "api_key": "jira_mcp_api_key",
                },
                capabilities=(ProviderCapability.ISSUE_TRACKER,),
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
                # No tool_id: the REST-transport tool that used to exist for
                # this (ConfluenceTool, CQL search) had zero production call
                # sites — audited and removed (see access_resolver.py's
                # module docstring). credential_field_map is kept, unused by
                # any tool now, purely because KnowledgeAccessResolver's
                # REST->MCP auto-wire (auto_wire_credential below) still
                # needs its keys to know which fields make a REST connection
                # "complete enough" to synthesize MCP access from.
                credential_field_map={
                    "base_url": "confluence_base_url",
                    "email": "confluence_email",
                    "api_token": "confluence_api_token",
                },
                auto_wire_credential="api_token",
                # Deliberately empty: REST search was never wired to the
                # DOCUMENTATION capability (see access_resolver.py) and
                # nothing else in the app asks Confluence for anything REST
                # could satisfy today.
                capabilities=(),
            ),
            TransportSpec(
                transport=Transport.MCP,
                label="MCP Server",
                auth_methods=(AuthMethod.API_KEY,),
                auth_fields={"api_key": ["server_url", "api_key"]},
                known_mcp_endpoint=_settings.confluence_mcp_default_server_url,
                # No tool_id — see the REST TransportSpec's comment above;
                # this source has no ITool at all anymore. Document
                # discovery reaches this transport through
                # KnowledgeAccessResolver directly, not the Tool Registry.
                credential_field_map={
                    "server_url": "confluence_mcp_server_url",
                    "api_key": "confluence_mcp_api_key",
                },
                capabilities=(ProviderCapability.DOCUMENTATION,),
            ),
        ),
    ),
    KnowledgeSourceSpec(
        key="google_drive",
        label="Google Drive",
        icon="Cloud",
        description="Design docs, specs, and files linked from Google Drive",
        capabilities=(
            "Document Retrieval",
            "Folder Listing",
        ),
        transports=(
            TransportSpec(
                transport=Transport.REST,
                label="OAuth (Google Drive API v3)",
                auth_methods=(AuthMethod.OAUTH,),
                # No form fields — same as GitHub, connecting is a real
                # OAuth redirect handled by dedicated /google-drive/*
                # endpoints, not the generic paste-credentials form. No
                # tool_id/credential_field_map for the same reason: the
                # per-user GoogleDriveTool is never registered on the
                # shared Tool Registry (see google_drive_tool.py's
                # docstring), so there's no install-wide config to sync.
                auth_fields={"oauth": []},
            ),
        ),
    ),
    KnowledgeSourceSpec(
        key="testrail",
        label="TestRail",
        icon="FileText",
        description="Test cases, suites, and sections — synced into the Knowledge Graph",
        capabilities=(
            "Test Case Sync",
            "Test Suite Discovery",
            "Existing Coverage Lookup",
        ),
        transports=(
            TransportSpec(
                transport=Transport.REST,
                label="REST API v2",
                # TestRail's API is HTTP Basic Auth with the account email as
                # username and the API key as password - not a bare API key
                # despite how "connect with an API key" is often described.
                # Same shape as Jira's REST auth (see that TransportSpec
                # above), which app.tools.implementations.testrail_tool
                # mirrors directly.
                auth_methods=(AuthMethod.BASIC,),
                auth_fields={"basic": ["base_url", "email", "api_token"]},
                tool_id="testrail",
                credential_field_map={
                    "base_url": "testrail_base_url",
                    "email": "testrail_email",
                    "api_token": "testrail_api_token",
                },
                # No known MCP server for TestRail - no auto_wire_credential
                # (that only matters when a sibling MCP TransportSpec exists).
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
