"""Deterministic verification for the Code Generation Agent's git-facing
claims — repository identity and file operations.

The LLM chooses a `repository` string and a list of file operations
(schemas.GeneratedCodeResult). Both are free-generated text: nothing stops
the model from naming a repository it was never shown, or claiming to
modify a file that doesn't exist. Every check below is grounded in data
this workflow's own earlier stages already produced via real tool calls /
graph traversal — never in what the code_generation LLM itself asserts.

Repository verification deliberately does not call the Knowledge Graph
(`IGraphRepository.has_graph`) directly — `CODE_GENERATION_MANIFEST` sets
`max_graph_hops=0` (this agent must not query the graph at all). Instead
it reuses the `repositories_consulted` list the Planning/Development
agents already populated *from their own graph traversal* (see
`RepositoryDiscoveryTool` — it only ever lists repositories that pass
`graph_repository.has_graph`). Membership in that set is therefore proof
of both "referenced by this workflow" and "indexed", without a second
graph call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Evidence
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.normalization import normalize_path
from app.agents.verification import build_evidence_pool, verify_claims
from app.models.repository import Repository
from app.models.workflow import Workflow

# ---------------------------------------------------------------------------
# Repository verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepositoryVerification:
    """Result of verifying an LLM-claimed repository name against
    deterministic ground truth. `passed` is the single gate the agent
    checks before returning a result."""

    repository: str
    well_formed: bool = False
    tracked: bool = False  # a `repositories` row exists for this user
    in_workflow_scope: bool = False  # appears in this workflow's own
    # Planning/Development `repositories_consulted` (graph-verified, see
    # module docstring) — doubles as the "indexed" check.
    errors: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def indexed(self) -> bool:
        """Alias for `in_workflow_scope` — see module docstring for why
        the two checks share one graph-traversal-derived source."""
        return self.in_workflow_scope

    @property
    def passed(self) -> bool:
        return self.well_formed and self.tracked and self.in_workflow_scope and not self.errors


def _collect_known_repositories(
    workflow: Workflow | None, source_workflow: Workflow | None
) -> set[str]:
    """Repository full_names ("owner/repo") this workflow's own
    Planning/Development stages actually traversed — pulled from either
    the workflow itself (legacy_sdlc/planning workflows) or its
    source_workflow (auto_execution workflows executing an approved
    blueprint; see workflows.py's `source_workflow` extras key)."""
    names: list[str] = []
    for wf in (source_workflow, workflow):
        if wf is None:
            continue
        for stage in ("planning", "development"):
            result = get_stage_result(wf, stage)
            if not result:
                continue
            names.extend(result.get("repositories_consulted", []) or [])
            for repo in result.get("repositories", []) or []:
                owner = repo.get("owner", "")
                name = repo.get("name", "")
                if owner and name:
                    names.append(f"{owner}/{name}")
                elif name:
                    names.append(name)
    return {n for n in names if n}


def _collect_known_file_paths(
    workflow: Workflow | None, source_workflow: Workflow | None
) -> dict[str, set[str]]:
    """repository full_name -> file_paths this run's Development stage
    deterministically verified against that exact repository
    (`AffectedComponent.file_path_verification == "verified"`, ADR 0027).

    Only `"verified"` entries are included — `"not_checked"` and
    `"unverified"` (including a component flagged
    `component_repository_mismatch`) are excluded identically, and a
    Development result predating this field (no `file_path_verification`
    key at all) defaults to absent/excluded, never included (ADR 0027
    §11 — fails closed for legacy data, never silently trusted).

    The returned mapping stays repository-partitioned
    (`dict[repository, set[file_path]]`) deliberately — never flatten
    this to a single `set[file_path]` for convenience; `repository` is
    looked up by the caller's own already-verified target repository
    (see `validate_file_operations` below), and collapsing this shape
    would reintroduce, on this side, the exact repository-attribution gap
    ADR 0027 closes on the Development side (Invariant G).
    """
    by_repo: dict[str, set[str]] = {}
    for wf in (source_workflow, workflow):
        if wf is None:
            continue
        result = get_stage_result(wf, "development")
        if not result:
            continue
        for component in result.get("components", []) or []:
            repo = component.get("repository", "")
            path = component.get("file_path", "")
            verification_status = component.get("file_path_verification")
            if repo and path and verification_status == "verified":
                by_repo.setdefault(repo, set()).add(path)
    return by_repo


def _is_well_formed(repository: str) -> bool:
    if not repository or "/" not in repository:
        return False
    owner, _, name = repository.partition("/")
    return bool(owner) and bool(name) and "/" not in name


async def verify_repository(
    repository: str,
    *,
    db: AsyncSession,
    user_id: uuid.UUID | str | None,
    workflow: Workflow | None,
    source_workflow: Workflow | None,
) -> RepositoryVerification:
    """Verify an LLM-claimed repository name before any git operation may
    use it. Never substitutes another repository — only confirms or
    rejects the one given."""
    errors: list[str] = []
    evidence: list[Evidence] = []

    well_formed = _is_well_formed(repository)
    evidence.append(
        Evidence(
            kind="tool_call",
            reference="repository_format_check",
            summary=(
                f"Repository '{repository}' is in 'owner/repo' format."
                if well_formed
                else f"Repository '{repository}' is not a valid 'owner/repo' name — rejected."
            ),
        )
    )
    if not well_formed:
        errors.append(f"Repository '{repository}' is not in 'owner/repo' format.")
        return RepositoryVerification(
            repository=repository, well_formed=False, errors=errors, evidence=evidence
        )

    # --- Tracked by this user (the only "permitted for modification" gate
    # this codebase has — see Repository model docstring: only repos a
    # user has explicitly selected are ever persisted here) ---
    tracked = False
    if user_id is not None:
        row_result = await db.execute(
            select(Repository.id).where(
                Repository.user_id == user_id, Repository.full_name == repository
            )
        )
        tracked = row_result.scalar_one_or_none() is not None
    evidence.append(
        Evidence(
            kind="tool_call",
            reference="repositories_lookup",
            summary=(
                f"Repository '{repository}' is tracked and permitted for this user."
                if tracked
                else f"Repository '{repository}' is not tracked/selected by this user — rejected."
            ),
        )
    )
    if not tracked:
        errors.append(f"Repository '{repository}' is not tracked/selected by this user.")

    # --- In scope for this workflow (and, transitively, indexed — see
    # module docstring) ---
    known_repos = _collect_known_repositories(workflow, source_workflow)
    pool = build_evidence_pool(list(known_repos))
    in_scope = bool(known_repos) and verify_claims([repository], pool).all_verified
    evidence.append(
        Evidence(
            kind="tool_call",
            reference="workflow_repository_scope_check",
            summary=(
                f"Repository '{repository}' matches a repository the Planning/"
                "Development stages of this workflow actually consulted."
                if in_scope
                else f"Repository '{repository}' was not referenced by any prior "
                "Planning/Development stage in this workflow — rejected."
            ),
        )
    )
    if not in_scope:
        errors.append(
            f"Repository '{repository}' is outside the scope of this workflow: it "
            "does not appear among the repositories the Planning/Development "
            "stages actually consulted (or no such stage result is available)."
        )

    return RepositoryVerification(
        repository=repository,
        well_formed=True,
        tracked=tracked,
        in_workflow_scope=in_scope,
        errors=errors,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# File operation validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileOperationViolation:
    path: str
    operation: str
    reason: str


def _is_safe_destination(path: str) -> bool:
    """Reject absolute paths, home-relative paths, and any `..` segment —
    the only destination-validity checks that hold regardless of what the
    target repository actually contains."""
    if not path or path.startswith("/") or path.startswith("~"):
        return False
    segments = normalize_path(path).split("/")
    return all(segment not in ("", "..") for segment in segments)


def validate_file_operations(
    files: list[dict[str, Any]],
    repository: str,
    known_file_paths: dict[str, set[str]],
) -> list[FileOperationViolation]:
    """Reject invalid file operations before they ever reach git.

    - create: destination must be a safe relative path. Never gated by
      `known_file_paths` at all (ADR 0027 Invariant 2) — a genuinely new
      file has no existing evidence to match, by definition.
    - modify / delete: destination must be safe AND appear in
      `known_file_paths[repository]` — i.e. deterministically VERIFIED
      by Development's repository-scoped check (ADR 0027 Invariant 1).

    ADR 0027 correction: an earlier version of this function skipped the
    known-path check entirely whenever `known_file_paths.get(repository)`
    was empty — intended for the narrow case where Development reported
    *no components at all* for `repository`. Once `known_file_paths` was
    changed (ADR 0027) to include only `"verified"` entries, that same
    empty-set condition also — and far more commonly — describes "components
    were proposed for this repository, but none of them verified": e.g.
    every one was `UNVERIFIED` due to a repository/file mismatch. Silently
    falling through to path-safety-only in that case would let
    `modify`/`delete` bypass verification entirely, which directly
    contradicts Invariant 1 ("NOT_CHECKED/UNVERIFIED → reject"). The
    check below is therefore now unconditional on `known` being non-empty:
    an empty verified set for `repository` means every `modify`/`delete`
    against it is rejected, exactly as it should be per Invariant 1 —
    there is no longer a "no ground truth, allow anyway" fallback.
    """
    violations: list[FileOperationViolation] = []
    known = known_file_paths.get(repository, set())
    # Path-normalized (leading "./", backslashes, duplicate slashes) —
    # NOT case-folded: unlike claim-text matching (app.agents.verification),
    # file path case is a real correctness distinction on case-sensitive
    # filesystems, so "Payment.py" and "payment.py" must stay different
    # here. A raw exact-string membership check previously rejected a
    # legitimate "src/x.py" claim against known "./src/x.py" evidence (or
    # vice versa) purely over that formatting difference — a false
    # negative, not a real hallucination.
    known_normalized = {normalize_path(p) for p in known}

    for f in files:
        path = f.get("path", "")
        operation = f.get("operation", "")

        if not _is_safe_destination(path):
            violations.append(
                FileOperationViolation(
                    path=path, operation=operation, reason="unsafe or invalid destination path"
                )
            )
            continue

        if operation in ("modify", "delete") and normalize_path(path) not in known_normalized:
            violations.append(
                FileOperationViolation(
                    path=path,
                    operation=operation,
                    reason=(
                        f"'{path}' does not appear among the file paths the Development "
                        f"stage's own graph traversal reported for '{repository}'"
                    ),
                )
            )

    return violations
