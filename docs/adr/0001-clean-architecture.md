# ADR 0001: Clean architecture for the backend

## Status
Superseded by [ADR 0003](0003-backend-folder-structure.md), which keeps the dependency-direction principle below but replaces the `domain`/`application`/`infrastructure` folder names with an explicit, stakeholder-specified layout.

## Context
ChangeGuard's backend needs to support several external integrations over time (GitHub, Jira, Neo4j, an AI analysis engine) without those integrations being decided at day one. The initial scaffold has no business logic — the structural decision has to hold up before there's any code to prove it wrong.

## Decision
Layer the backend as `domain` → `application` → `infrastructure`/`api`, with dependencies pointing inward only. `application` defines interfaces for anything it needs from the outside world (persistence, external APIs); `infrastructure` implements those interfaces. `api` depends on `application` and contains no business logic itself.

## Consequences
- Adding Neo4j, GitHub, Jira, or the AI engine later means writing a new class in `infrastructure` that implements an existing interface — no change to `application` or `domain`.
- Use cases are testable without a database, an HTTP server, or any external service, by substituting a fake implementation of an interface.
- The cost is more files and more indirection for what is, today, a trivial application. This is accepted deliberately: the alternative (business logic accreting directly into FastAPI routers) is the more common failure mode as a codebase grows, and is expensive to unwind after the fact.
