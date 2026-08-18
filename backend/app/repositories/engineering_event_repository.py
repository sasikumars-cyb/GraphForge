"""Data access for `app.models.engineering_event.EngineeringEvent` —
the ONLY way anything in this codebase may append to or read the
Engineering State event log.

Mirrors `app.repositories.engineering_memory_repository`'s own stated
convention exactly: a plain class over an injected `AsyncSession`,
`append()` uses `db.add()` + `db.flush()` (never `commit()` — the
caller/transaction owner commits), reads use plain SQLAlchemy `select()`.

Deliberately exposes only two methods: `append` and `list_for_task`.
There is no `update`, no `delete`, and there never will be — the database
itself additionally refuses both (see this table's migration), so the
absence here is enforced twice, not once.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.engineering_state.events import (
    BELIEF_RECORDED,
    CAUSAL_PAYLOAD_REFERENCE_FIELD,
    CAUSAL_REQUIREMENTS,
    EVIDENCE_RECORDED,
    OBSERVATION_RECORDED,
    PLAN_CREATED,
    PLAN_STEP_CREATED,
    PLAN_STEP_INVALIDATED,
    validate_payload,
)
from app.models.engineering_event import EngineeringEvent


class ConcurrentAppendConflictError(RuntimeError):
    """Raised when two concurrent appends to the same task's stream
    somehow both computed the same `sequence_number` despite the
    advisory-lock serialization below (the final adversarial sequencing
    review's §12 finding: "the system must not silently overwrite event
    history" — this is the loud failure that guarantees it doesn't,
    rather than a promise that the race can't occur).

    The caller must retry the whole `append()` call, not assume the
    event it thought it was appending actually landed.
    """


class CausalOrderViolationError(ValueError):
    """Raised by `append()` — never by `materialize.fold()` — when an
    event that requires a causal parent (per
    `app.engineering_state.events.CAUSAL_REQUIREMENTS`, or `BeliefRecorded`'s
    `evidence_ids`) does not have one that actually exists, in the same
    task, of the expected type.

    Phase 1 Final Correctness Audit finding: a child event (e.g.
    `GoalUpdated`) could previously be durably appended with no real
    `GoalCreated` behind it — `materialize.fold()` silently dropped the
    update rather than rejecting the history that produced it. Per
    `ENGINEERING_STATE_ARCHITECTURE.md` §8 ("every event that arises as a
    consequence of another... MUST reference the causing record(s)") and
    inv. 13 (events are immutable once appended — a malformed reference
    can never be corrected, only superseded), the correct point to reject
    this is at append time, before the event becomes durable — not later,
    silently, at fold time.
    """


class EngineeringEventRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def append(
        self,
        *,
        task_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        actor: str,
        causation_event_id: uuid.UUID | None = None,
        execution_context: dict[str, Any] | None = None,
        schema_version: int = 1,
    ) -> EngineeringEvent:
        """Append one immutable event to `task_id`'s stream.

        Concurrency: two concurrent `append()` calls for the *same*
        `task_id` are serialized via a transaction-scoped Postgres
        advisory lock keyed on `task_id` (`pg_advisory_xact_lock`,
        released automatically at transaction end — no separate unlock
        call, no lock that can leak past this call's own transaction).
        Appends for *different* task_ids never block each other. The
        `(task_id, sequence_number)` UNIQUE constraint on the table is a
        second, independent backstop: if two transactions ever did
        compute the same next sequence_number (the lock should make this
        impossible, but "should" is not "does" — see the final
        adversarial sequencing review's own insistence on a hard
        backstop, not just the primary mechanism), the second INSERT
        raises `IntegrityError`, which this method re-raises as
        `ConcurrentAppendConflictError` — loud, not swallowed, not
        silently retried on the caller's behalf (retry policy is the
        caller's decision, not this repository's).

        Does not commit — matches `EngineeringMemoryRepository`'s
        convention exactly, so this call can participate in whatever
        transaction the caller already owns (see
        `app.orchestrator.run_coordinator`'s integration).

        Raises `CausalOrderViolationError` (Phase 1 correction) if
        `event_type` requires a causal parent — per
        `app.engineering_state.events.CAUSAL_REQUIREMENTS`, or
        `BeliefRecorded`'s `evidence_ids` — that does not exist, in this
        same `task_id`, with the expected `event_type`. Checked before
        the advisory lock below (a read-only check, independent of
        sequence-number allocation — no reason to hold the lock for it).
        """
        validate_payload(event_type, payload)
        await self._validate_causal_references(
            task_id=task_id,
            event_type=event_type,
            payload=payload,
            causation_event_id=causation_event_id,
        )

        # Postgres advisory locks take a bigint; hashtext() -> int4,
        # cast to bigint, is a stable, deterministic hash of the task_id
        # string — the same task_id always maps to the same lock key
        # within one Postgres instance, which is exactly what's needed
        # (no lock-key registry to maintain, no risk of two different
        # task_ids sharing hashtext()'s 32-bit space colliding in a way
        # that matters: a spurious collision only costs unrelated tasks
        # a moment of unnecessary serialization, never a correctness bug,
        # because the UNIQUE constraint below is scoped to the real
        # task_id, not the lock key).
        await self._db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:task_id)::bigint)"),
            {"task_id": str(task_id)},
        )

        current_max = await self._db.scalar(
            select(func.max(EngineeringEvent.sequence_number)).where(
                EngineeringEvent.task_id == task_id
            )
        )
        next_sequence = (current_max or 0) + 1

        event = EngineeringEvent(
            id=uuid.uuid4(),
            task_id=task_id,
            sequence_number=next_sequence,
            event_type=event_type,
            schema_version=schema_version,
            payload=payload,
            actor=actor,
            causation_event_id=causation_event_id,
            execution_context=execution_context,
        )
        self._db.add(event)
        try:
            await self._db.flush()
        except IntegrityError as exc:
            raise ConcurrentAppendConflictError(
                f"Concurrent append conflict on task_id={task_id} at "
                f"sequence_number={next_sequence}. The advisory lock "
                "should have prevented this — if it fired, something is "
                "seriously wrong upstream, not merely a race to retry "
                "past. Investigate before retrying."
            ) from exc
        return event

    async def list_for_task(self, task_id: uuid.UUID) -> list[EngineeringEvent]:
        """Every event for `task_id`, in append order. This is the only
        read path Phase 1 needs — `app.engineering_state.materialize.fold`
        consumes exactly this list."""
        result = await self._db.execute(
            select(EngineeringEvent)
            .where(EngineeringEvent.task_id == task_id)
            .order_by(EngineeringEvent.sequence_number)
        )
        return list(result.scalars().all())

    async def list_by_event_types(self, event_types: frozenset[str]) -> list[EngineeringEvent]:
        """Every event whose `event_type` is one of `event_types`, across
        ALL tasks — a genuinely new query shape, not `list_for_task`'s
        single-task scope. Added in Phase 4, deliberately narrow (not a
        general unscoped-query escape hatch): the one real need is the
        Workspace concurrency cap (Cap §19, "Policy MUST cap concurrent
        Workspaces per Role and per tenant"), which requires counting a
        Role's open Workspaces across potentially many different
        task_ids — something `list_for_task`'s per-task scope
        structurally cannot answer.

        Deliberately does NOT filter by owning Role/tenant here — that
        information lives inside `payload` (a Workspace event's outer
        `EngineeringEvent.actor` is always `"control_plane"`, the
        WRITER, never the owning Role; the OWNING Role is a payload
        field), and this repository stays a thin, typed-column query
        seam. Owner-scoped filtering is `WorkspaceLifecycleService`'s
        job, applied in Python to this method's result — the same
        division of labor `materialize.fold()` already has relative to
        this repository (repository fetches, a higher layer interprets).

        Order is by `recorded_at` — there is no single cross-task
        `sequence_number` to order by, since that column is only unique
        per `task_id`.
        """
        result = await self._db.execute(
            select(EngineeringEvent)
            .where(EngineeringEvent.event_type.in_(event_types))
            .order_by(EngineeringEvent.recorded_at)
        )
        return list(result.scalars().all())

    async def _validate_causal_references(
        self,
        *,
        task_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        causation_event_id: uuid.UUID | None,
    ) -> None:
        """The Phase 1 correction's whole implementation: fail closed,
        before this event becomes durable, if its declared causal parent
        does not exist in this task's valid prior history.

        Two distinct mechanisms, matching `app.engineering_state.events`'
        own split:

        - Single-parent event types (`CAUSAL_REQUIREMENTS`) — the parent
          MUST be named via `causation_event_id`, AND the corresponding
          payload reference field (e.g. `goal_event_id`) MUST agree with
          it, closing the exact gap the audit found: two independent
          "what caused this" fields that could silently point at
          different — or nonexistent — events.
        - `BeliefRecorded` — every id in `payload["evidence_ids"]` MUST
          exist, in this task, as an `EvidenceRecorded` event. Not
          expressible via a single `causation_event_id` column (a Belief
          may legitimately cite more than one Evidence item), so checked
          directly against the payload's own list instead.

        Every other event type (`GoalCreated`, `DecisionMade`,
        `EvidenceRecorded`, `ObservationRecorded`) has no causal
        requirement and is returned from immediately — they are either
        the base case for their own chain or cite nothing that must
        already exist for THIS Phase 1 correction's purposes.
        """
        if event_type in CAUSAL_REQUIREMENTS:
            required_parent_types = CAUSAL_REQUIREMENTS[event_type]
            reference_field = CAUSAL_PAYLOAD_REFERENCE_FIELD[event_type]

            if causation_event_id is None:
                raise CausalOrderViolationError(
                    f"{event_type} requires causation_event_id, referencing "
                    f"one of {sorted(required_parent_types)} in this task's "
                    "prior history — none was given."
                )

            payload_reference = payload.get(reference_field)
            if payload_reference is not None and str(payload_reference) != str(causation_event_id):
                raise CausalOrderViolationError(
                    f"{event_type}.{reference_field} ({payload_reference}) does "
                    f"not match causation_event_id ({causation_event_id}) — "
                    "an event must not carry two disagreeing claims about "
                    "what caused it."
                )

            parent = await self._db.get(EngineeringEvent, causation_event_id)
            if parent is None:
                raise CausalOrderViolationError(
                    f"{event_type} references causation_event_id="
                    f"{causation_event_id}, which does not exist."
                )
            if parent.task_id != task_id:
                raise CausalOrderViolationError(
                    f"{event_type} references causation_event_id="
                    f"{causation_event_id}, which belongs to a different "
                    f"task ({parent.task_id}, not {task_id})."
                )
            if parent.event_type not in required_parent_types:
                raise CausalOrderViolationError(
                    f"{event_type} references causation_event_id="
                    f"{causation_event_id}, whose event_type is "
                    f"{parent.event_type!r}, not one of "
                    f"{sorted(required_parent_types)}."
                )

        # From here on, checks are NOT mutually exclusive with the
        # CAUSAL_REQUIREMENTS block above (or with each other) — several
        # event types carry a SECOND reference that isn't expressible
        # through the single `causation_event_id` column, mirroring
        # BeliefRecorded.evidence_ids's own established precedent for
        # exactly that reason.

        if event_type == BELIEF_RECORDED:
            evidence_ids = payload.get("evidence_ids") or []
            for raw_id in evidence_ids:
                evidence_event = await self._db.get(EngineeringEvent, uuid.UUID(str(raw_id)))
                if evidence_event is None:
                    raise CausalOrderViolationError(
                        f"{BELIEF_RECORDED} cites evidence_ids entry "
                        f"{raw_id!r}, which does not exist."
                    )
                if evidence_event.task_id != task_id:
                    raise CausalOrderViolationError(
                        f"{BELIEF_RECORDED} cites evidence_ids entry "
                        f"{raw_id!r}, which belongs to a different task "
                        f"({evidence_event.task_id}, not {task_id})."
                    )
                if evidence_event.event_type != EVIDENCE_RECORDED:
                    raise CausalOrderViolationError(
                        f"{BELIEF_RECORDED} cites evidence_ids entry "
                        f"{raw_id!r}, whose event_type is "
                        f"{evidence_event.event_type!r}, not {EVIDENCE_RECORDED!r}."
                    )

        if event_type == PLAN_CREATED:
            # Phase 6, ES §11: when present, `supersedes_plan_event_id`
            # MUST reference a real, same-task PlanCreated event — closing
            # the exact "dangling or cross-task supersession claim" gap
            # this whole mechanism exists to prevent, mirroring every
            # other reference check in this method.
            supersedes = payload.get("supersedes_plan_event_id")
            if supersedes is not None:
                superseded_event = await self._db.get(EngineeringEvent, uuid.UUID(str(supersedes)))
                if superseded_event is None:
                    raise CausalOrderViolationError(
                        f"{PLAN_CREATED}.supersedes_plan_event_id={supersedes!r} "
                        "does not exist."
                    )
                if superseded_event.task_id != task_id:
                    raise CausalOrderViolationError(
                        f"{PLAN_CREATED}.supersedes_plan_event_id={supersedes!r} "
                        f"belongs to a different task ({superseded_event.task_id}, "
                        f"not {task_id})."
                    )
                if superseded_event.event_type != PLAN_CREATED:
                    raise CausalOrderViolationError(
                        f"{PLAN_CREATED}.supersedes_plan_event_id={supersedes!r} "
                        f"has event_type {superseded_event.event_type!r}, not "
                        f"{PLAN_CREATED!r}."
                    )

        if event_type == PLAN_STEP_CREATED:
            # Phase 6, ES §11: every id in `depends_on`, when present,
            # MUST reference a real, same-task PlanStepCreated event —
            # the minimum integrity check that makes
            # `materialize.transitively_dependent_plan_steps` trustworthy
            # (a dangling dependency edge would silently under-propagate
            # invalidation).
            depends_on = payload.get("depends_on") or []
            for raw_id in depends_on:
                dependency_event = await self._db.get(EngineeringEvent, uuid.UUID(str(raw_id)))
                if dependency_event is None:
                    raise CausalOrderViolationError(
                        f"{PLAN_STEP_CREATED}.depends_on entry {raw_id!r} does not exist."
                    )
                if dependency_event.task_id != task_id:
                    raise CausalOrderViolationError(
                        f"{PLAN_STEP_CREATED}.depends_on entry {raw_id!r} belongs to "
                        f"a different task ({dependency_event.task_id}, not {task_id})."
                    )
                if dependency_event.event_type != PLAN_STEP_CREATED:
                    raise CausalOrderViolationError(
                        f"{PLAN_STEP_CREATED}.depends_on entry {raw_id!r} has "
                        f"event_type {dependency_event.event_type!r}, not "
                        f"{PLAN_STEP_CREATED!r}."
                    )

        if event_type == PLAN_STEP_INVALIDATED:
            # ES §10: "Contradiction... is the ONLY classification that
            # may trigger Replan." Structurally enforced here, not merely
            # by convention: `contradiction_observation_event_id` MUST
            # reference a real, same-task ObservationRecorded event whose
            # `classification` is literally "contradiction" — the single
            # most contract-critical check this whole event type exists
            # to carry. Unlike every other reference check in this
            # method, this one additionally inspects the referenced
            # event's OWN payload content, not just its existence/task/
            # type — a deliberate, narrowly-scoped exception justified by
            # how explicit and repeated ES §10's "ONLY Contradiction"
            # rule is.
            contradiction_id = payload.get("contradiction_observation_event_id")
            if contradiction_id is not None:
                contradiction_event = await self._db.get(
                    EngineeringEvent, uuid.UUID(str(contradiction_id))
                )
                if contradiction_event is None:
                    raise CausalOrderViolationError(
                        f"{PLAN_STEP_INVALIDATED}.contradiction_observation_event_id="
                        f"{contradiction_id!r} does not exist."
                    )
                if contradiction_event.task_id != task_id:
                    raise CausalOrderViolationError(
                        f"{PLAN_STEP_INVALIDATED}.contradiction_observation_event_id="
                        f"{contradiction_id!r} belongs to a different task "
                        f"({contradiction_event.task_id}, not {task_id})."
                    )
                if contradiction_event.event_type != OBSERVATION_RECORDED:
                    raise CausalOrderViolationError(
                        f"{PLAN_STEP_INVALIDATED}.contradiction_observation_event_id="
                        f"{contradiction_id!r} has event_type "
                        f"{contradiction_event.event_type!r}, not {OBSERVATION_RECORDED!r}."
                    )
                if contradiction_event.payload.get("classification") != "contradiction":
                    raise CausalOrderViolationError(
                        f"{PLAN_STEP_INVALIDATED}.contradiction_observation_event_id="
                        f"{contradiction_id!r} has classification "
                        f"{contradiction_event.payload.get('classification')!r}, not "
                        "'contradiction' — ES §10: Contradiction is the ONLY "
                        "classification that may trigger Replan/PlanStep invalidation."
                    )
