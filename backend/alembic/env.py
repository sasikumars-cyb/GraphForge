"""Alembic environment configured for SQLAlchemy's async engine.

Migrations still run synchronously under the hood (Alembic itself is sync);
`asyncio.run` bridges the async engine created by the application code so we
only ever define one engine configuration, not two.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import get_settings
from app.database.base import Base
from app.models.agent_step import AgentStep  # noqa: F401
from app.models.ai_profile import AIProfile, AIProviderUsage  # noqa: F401
from app.models.ai_provider_config import AIProviderConfig, AISettings  # noqa: F401
from app.models.background_job import BackgroundJob  # noqa: F401
from app.models.belief import Belief, Hypothesis  # noqa: F401
from app.models.confidence_calibration import ConfidenceCalibration  # noqa: F401
from app.models.contradiction import Contradiction, ContradictionParty  # noqa: F401
from app.models.decision import Decision, Recommendation  # noqa: F401
from app.models.engineering_artifact import EngineeringArtifact  # noqa: F401
from app.models.engineering_evidence_pack import EngineeringEvidencePackRecord  # noqa: F401
from app.models.engineering_session import EngineeringSession, TimelineEntry  # noqa: F401
from app.models.evidence import Evidence  # noqa: F401
from app.models.github_connection import GitHubConnection  # noqa: F401
from app.models.google_drive_connection import GoogleDriveConnection  # noqa: F401
from app.models.indexing_job import IndexingJob  # noqa: F401
from app.models.investigation_intelligence import (  # noqa: F401
    InvestigationOutcomeRecord,
    InvestigationProviderEventRecord,
)
from app.models.knowledge_relationship import KnowledgeRelationshipRecord  # noqa: F401
from app.models.llm_invocation import LLMInvocation  # noqa: F401
from app.models.oauth_app_credential import OAuthAppCredential  # noqa: F401
from app.models.participant import Participant  # noqa: F401
from app.models.pull_request import PullRequest  # noqa: F401
from app.models.pull_request_ai_analysis import PullRequestAIAnalysis  # noqa: F401
from app.models.pull_request_analysis import PullRequestAnalysis  # noqa: F401
from app.models.repository import Repository  # noqa: F401
from app.models.run import Run  # noqa: F401
from app.models.test_case_upload import TestCaseUpload  # noqa: F401
from app.models.testrail_sync_job import TestRailSyncJob  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.user_correction import UserCorrectionRecord  # noqa: F401
from app.models.workflow import Workflow  # noqa: F401 - all eleven register with Base.metadata
from app.models.workflow_report import WorkflowReport  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(settings.database_url)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
