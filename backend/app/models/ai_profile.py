"""AI Profiles and provider usage counters.

An **AI Profile** is the user-facing abstraction: a named, reusable intent
("Fast Planner", "Deep Architecture") that resolves to a provider, model and
generation parameters. Workflow stages map to profiles, so changing what
powers "Fast Planner" updates every workflow that uses it without touching a
single workflow definition.

`AIProviderUsage` is a per-provider aggregate — counters, not a request log.
The platform reports only what it actually observed; it never estimates quota
it cannot see.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AIProfile(Base):
    """A reusable, named AI behaviour."""

    __tablename__ = "ai_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Stable identifier referenced by stage mappings and fallback chains.
    # Renaming the display name must not break those references, which is why
    # the slug is separate from `name`.
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # What actually serves this profile. Null model means "the provider's
    # configured or default model", so a profile keeps working when an
    # operator upgrades a provider's model.
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Only meaningful where the provider advertises the capability; ignored
    # otherwise rather than rejected, so one profile can span providers.
    reasoning_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    structured_output: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    streaming: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Fallback policy: the profile to try when this one fails *recoverably*.
    # Expressed as a profile rather than a provider so the fallback carries
    # its own model and parameters.
    fallback_profile_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Seeded profiles are created automatically from environment config so a
    # fresh install has something usable; they can be edited or deleted.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AIProviderUsage(Base):
    """Aggregate counters for one provider.

    Deliberately a rolling aggregate rather than per-request rows: the goal is
    a lightweight "is this provider healthy and how much am I using it"
    dashboard, not a billing ledger.
    """

    __tablename__ = "ai_provider_usage"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rate_limit_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    auth_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Cumulative, so average latency = total_latency_ms / successes. Storing
    # the sum avoids recomputing an average from a log we do not keep.
    total_latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    last_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_rate_limit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
