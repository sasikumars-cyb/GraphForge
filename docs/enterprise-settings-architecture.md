# Enterprise Settings Architecture

## Architecture Summary

GraphForge Settings is now structured as an enterprise administration console with role-based access control. The architecture separates concerns into six logical sections, with visibility governed by the authenticated user's role.

```
Settings
├── Workspace          (all users)     — Organization, notifications, preferences
├── Integrations       (all users)     — External systems (GitHub, Jira, Confluence, Neo4j)
├── AI Workspace       (admin only)    — Providers, profiles, models, health, fallback
├── Tool Registry      (admin only)    — Agent capabilities, health, enable/disable
├── Security           (admin only)    — Credentials, encryption, access control
└── Advanced           (admin only)    — Diagnostics, feature flags, telemetry
```

## Backend Changes

### User Model — RBAC

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `role` | VARCHAR(32) | `"user"` | Role-based access: `user` or `admin` |

The `role` column was added to `users` via migration `b5c6d7e8f9a0`. Existing users default to `"user"`.

### Bootstrap Admin Account

A development-only admin account is seeded by the migration:

| Field | Value |
|-------|-------|
| Email | `admin@graphforge.local` |
| Password | `admin` |
| Role | `admin` |

**WARNING:** These credentials are for local development only. Production deployments MUST change or remove this account.

### Authorization Dependency

```python
# backend/app/api/v1/dependencies.py

async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if getattr(current_user, "role", "user") != "admin":
        raise ForbiddenError("Administrator access required.")
    return current_user
```

### Protected Routes

| API Group | Dependency | Who Can Access |
|-----------|-----------|----------------|
| `/ai/*` (AI Workspace) | `require_admin` | Admins only |
| `/tools/*` (Tool Registry) | `require_admin` | Admins only |
| `/knowledge/*` (Integrations backend) | `get_current_user` | All authenticated users |
| `/github/*` | `get_current_user` | All authenticated users |
| `/auth/*` | Public / `get_current_user` | Public (login/register) or authenticated (me) |

### New Exception

```python
class ForbiddenError(AppError):
    status_code = 403
    error_code = "forbidden"
```

Non-admin users calling admin-only APIs receive a 403 response with `{"error": {"code": "forbidden", "message": "Administrator access required."}}`.

## Frontend Changes

### User Type

```typescript
export interface User {
  id: string;
  email: string;
  full_name: string;
  auth_provider: string;
  role: string;        // ← NEW: "user" | "admin"
  created_at: string;
}
```

### Settings Tab Visibility

The `SettingsShell` component filters tabs based on `user.role`:

```typescript
const visibleTabs = TABS.filter((tab) => !tab.adminOnly || isAdmin);
```

| Tab | `adminOnly` | Visible To |
|-----|-------------|------------|
| Workspace | `false` | Everyone |
| Integrations | `false` | Everyone |
| AI Workspace | `true` | Admin only |
| Tool Registry | `true` | Admin only |
| Security | `true` | Admin only |
| Advanced | `true` | Admin only |

### Rename: Knowledge Sources → Integrations

The "Knowledge Sources" concept has been renamed to "Integrations" in the user-facing UI:
- Tab label: "Integrations"
- Tab icon: `Plug` (was `Database`)
- Component: `IntegrationsSection` (was `KnowledgeSourcesSection`)
- Description: "External systems connected to GraphForge"

Transport details (REST, MCP, GraphQL) are hidden from normal users. The UI shows only: connection name, status, credentials presence, and health.

## Database Changes

| Migration | Description |
|-----------|-------------|
| `b5c6d7e8f9a0` | Adds `role` column to `users`, seeds admin account |
| `a3b4c5d6e7f8` | Adds `knowledge_connections` table (multi-connection architecture) |

## New APIs

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/knowledge/overview` | GET | User | Sources + connections overview |
| `/knowledge/connections` | GET/POST | User | List or create connections |
| `/knowledge/connections/:id` | GET/PUT/DELETE | User | CRUD single connection |
| `/knowledge/connections/:id/health` | POST | User | Health check |

Existing APIs (`/ai/*`, `/tools/*`) now require admin role.

## Reuse Summary

| Component | Reused? | Notes |
|-----------|---------|-------|
| `get_current_user` dependency | Yes | Base auth, unchanged |
| `encrypt_secret` / `decrypt_secret` | Yes | Credentials encryption for connections |
| `knowledge_connections` table | Yes | Multi-connection storage from prior work |
| AI Workspace API | Yes | Unchanged, just added `require_admin` |
| Tool Registry API | Yes | Unchanged, just added `require_admin` |
| GitHub service | Yes | Unchanged, still handles OAuth flow |
| Card, StatusBadge components | Yes | Shared UI primitives |

## Integration Architecture

```
User-facing (Integrations tab)          Internal (runtime)
─────────────────────────────────        ─────────────────────
                                         
GitHub [2 connections]                   Planning Agent
  ├── Production Enterprise                  │
  └── Open Source                            ▼
                                         "Repository Search" capability
Jira [2 connections]                         │
  ├── Engineering                            ▼
  └── Customer Platform                  Tool Registry resolves to
                                         → GitHub Production connection
Confluence [1 connection]                    │
  └── Engineering Docs                       ▼
                                         REST transport (internal)
Neo4j [1 connection]                         │
  └── Local Instance                         ▼
                                         api.github.com
```

Agents request capabilities. The Tool Registry resolves them to configured connections. Transport (REST, MCP, SDK) is an internal implementation detail — never exposed in the user-facing Settings UI.

## Known Limitations

1. **Health checks are configuration-based** — live transport pings (HTTP, DB connect) are architecture-ready but not yet implemented per-transport.
2. **Jira/Confluence live queries** — the connection model and CRUD exist; actual API calls to Jira/Confluence (list projects, list spaces) are a follow-up.
3. **Role management UI** — roles are stored but there's no UI for an admin to promote/demote users. Currently managed via the database directly.
4. **Single admin bootstrap** — only one admin is seeded. Additional admins must be created by updating the `role` column in the database.
5. **Frontend tab hiding is defense-in-depth** — the backend enforces authorization regardless of what the frontend shows. A malicious client calling `/ai/providers` without admin role gets 403.
