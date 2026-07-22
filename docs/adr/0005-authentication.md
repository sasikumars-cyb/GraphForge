# ADR 0005: Authentication

## Status
Accepted

## Context
The app needed real authentication — a login page and JWT-based sessions — with the explicit requirement that GitHub OAuth is prepared for but **not** implemented yet. This is the first feature in the project with actual business logic and a real database table, so several decisions needed making beyond "add a login form."

## Decisions

**JWT via PyJWT, HS256, passwords via bcrypt directly (no passlib).** `core/security.py` is the only module allowed to import `bcrypt` or `jwt`. Passwords are truncated to 72 bytes before hashing/verifying — bcrypt's own limit — so a long or multi-byte-heavy password degrades gracefully instead of raising.

**FastAPI's `debug=False` matters here too**, per ADR (see `main.py`): it's what makes the JSON error contract — including 401s from `UnauthorizedError` — actually reach the client instead of Starlette's HTML debug page.

**JSON request bodies for `/auth/register` and `/auth/login`, not `OAuth2PasswordRequestForm`.** FastAPI's tutorials use the form-encoded `OAuth2PasswordRequestForm` because it makes Swagger UI's built-in "Authorize" password-flow dialog work out of the box. This API is JSON throughout (matching every other schema in `app/schemas`), and the frontend is a JSON SPA — bending the login endpoint into form-encoding just for a Swagger convenience wasn't worth the inconsistency. `OAuth2PasswordBearer` is still used for the *protected-route* dependency (`get_current_user`), so Swagger still shows padlocks on protected routes; testers just get the token from `/auth/login`'s "Try it out" response and paste it into the Authorize dialog manually.

**The `User` model is shaped for a future OAuth account, not just a local one.** `hashed_password` is nullable and `auth_provider` (`"local"` today) exists specifically so a future GitHub-OAuth-only user can exist with no local password at all, without a schema migration to add nullability later.

**`IOAuthProvider` is a real interface with no implementation**, in `app/integrations/interfaces.py`, alongside the existing `IVersionControlProvider`/`IIssueTrackerProvider` (same file, same pattern: a contract now, an adapter later). It models exactly what a GitHub OAuth adapter needs: `get_authorization_url`, `exchange_code_for_token`, `fetch_user_profile`, returning an `OAuthUserProfile` the eventual adapter would map GitHub's actual payload down to.

**`/auth/github/login` and `/auth/github/callback` exist and return 501, not 404.** `api/v1/dependencies.get_oauth_provider()` currently returns `None`; both routes depend on it and raise `NotImplementedYetError` (501) when it's `None`. This makes the extension point concrete and discoverable in Swagger, rather than a route that simply doesn't exist yet. Registering a real adapter later is exactly one function body change in `get_oauth_provider` — no router or service code changes.

**Frontend: token in `localStorage`, not an httpOnly cookie.** The backend has no session/cookie infrastructure, and adding one just for this would be a larger change than the login feature itself. This is a known, accepted tradeoff (a `localStorage` token is readable by any JS on the page, i.e. vulnerable to XSS exfiltration in a way an httpOnly cookie isn't) — worth revisiting if this goes further than a hackathon MVP.

**Frontend has no registration page.** Only a login page was asked for. `POST /auth/register` exists on the backend and is documented (in Swagger, and as a note on the login page itself) as the way to create a test account for now.

**Backend tests run against a real Postgres, wrapped in a rolled-back transaction per test.** `tests/conftest.py`'s `db_session` fixture begins a transaction, hands the app a session bound to it via `join_transaction_mode="create_savepoint"` (so the app's own `session.commit()` calls become savepoints, not real commits), and rolls back the outer transaction at teardown. This means auth tests can run repeatedly against a shared dev database with zero data leakage — verified by running the suite three times in a row and checking `SELECT count(*) FROM users` stayed at 0 between runs. CI spins up an ephemeral `postgres:16-alpine` service container so it never depends on a developer's local DB state either.

**One event loop for the whole pytest session** (`asyncio_default_fixture_loop_scope` and `asyncio_default_test_loop_scope`, both `"session"`). `app.database.session` creates its async engine once at import time; pytest-asyncio's default of one event loop per test function causes asyncpg connections to be used across event loops they weren't created on, which asyncpg does not support. This surfaced as a real, reproducible failure while writing the auth tests, not a hypothetical.

## Consequences
- Adding GitHub OAuth later touches: one new file in `app/integrations` implementing `IOAuthProvider`, one line in `get_oauth_provider`, and the two route bodies in `oauth.py` (currently just the "not configured" branch) — no change to `auth_service`, no change to the `User` model beyond possibly relaxing the current single-provider assumption if a user should be able to link both.
- Anyone adding a DB-touching test must use the `db_session`/`db_client` fixtures, not the plain `client` fixture — `client` intentionally has no database wired in, so purely HTTP-level tests (health, error handling) stay independent of Postgres being reachable at all.
- The `localStorage` token storage tradeoff is written down here specifically so it isn't silently forgotten if this project moves past hackathon scope.
