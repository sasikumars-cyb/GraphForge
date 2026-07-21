# ADR 0003: Backend folder structure

## Status
Accepted

## Context
[ADR 0001](0001-clean-architecture.md) laid out a `domain`/`application`/`infrastructure`/`api` layering. Once implementation started, a specific folder layout was specified directly:

```
app/
    api/
    services/
    models/
    schemas/
    database/
    core/
    utils/
    integrations/
    graph/
    ai/
    indexer/
```

This is a different, and more common, shape than ADR 0001's strict Clean Architecture layering: it names concerns (`services`, `models`, `database`) rather than architectural rings (`domain`, `application`, `infrastructure`), and it promotes the two integration-heavy subsystems (`graph`, `ai`) plus `integrations` and `indexer` to top-level modules instead of nesting them under a generic `infrastructure/external`.

## Decision
Adopt the specified layout, mapped as follows:

| ADR 0001 concept | New location |
|---|---|
| `domain/entities`, `application/use_cases` | `services/`, `models/` (no business logic exists yet in either) |
| `application/interfaces` (the four ports) | Split to live beside what they front: `graph/interfaces.py`, `ai/interfaces.py`, `integrations/interfaces.py` |
| `infrastructure/database` | `database/` (engine, session, declarative base) |
| `infrastructure/external` | `integrations/` (GitHub, Jira — not implemented) |
| `api/v1/schemas` | `schemas/` (promoted to top-level, no longer nested under `api`) |
| `core` | `core/` (unchanged: settings, logging; gained `exceptions.py` and `error_handlers.py`) |
| — | `utils/` (new, empty — shared stateless helpers) |
| — | `indexer/` (new — the future codebase-parsing pipeline that feeds `graph/`) |

The dependency-direction principle from ADR 0001 is kept, just expressed with these names: `services` depends on `models`/`schemas` and on the interfaces declared in `graph`, `ai`, and `integrations` — never on a concrete adapter. `api` depends on `services` and contains no business logic.

## Consequences
- `graph`, `ai`, `integrations`, and `indexer` being top-level (not nested under a shared `infrastructure/`) makes each future subsystem's boundary more visible in the tree, at the cost of four packages instead of one.
- Each of `graph`, `ai`, and `integrations` currently holds only an `interfaces.py` — the contract a future adapter must satisfy — and no implementation. `indexer` holds neither yet, since it is not one of the four external integrations named in the original scope (Neo4j, GitHub, Jira, AI engine) but a first-party pipeline to be designed when `graph` and `integrations` both exist.
- ADR 0001 is superseded, not deleted, so the reasoning for the dependency-inversion approach itself remains on record.
