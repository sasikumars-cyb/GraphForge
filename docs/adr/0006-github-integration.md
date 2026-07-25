# ADR 0006: GitHub integration ("Connect GitHub", repositories, webhook)

## Status
Accepted

## Context
The ask was: let a user connect a GitHub account, list and select repositories, store their metadata, receive GitHub webhook deliveries, read `pull_request` events, and persist PR metadata — explicitly without doing any AI analysis yet. This is the first feature that talks to a real third-party API and the first that receives inbound webhooks.

Constraint carried over from earlier in this project: **no company-owned GitHub account or org may be used for this integration**, in testing or otherwise. Combined with this being an automated coding session with no interactive browser available to click through GitHub's real consent screen, the implementation and verification strategy below follows directly from that: write real, correct code against real GitHub endpoints, configured via env vars pointing at *the user's own* personal GitHub OAuth App — and verify it without ever needing a live GitHub login.

## Decisions

**"Connect GitHub" is not "log in with GitHub."** `IOAuthProvider` (ADR 0005) already existed for a login-via-GitHub use case that remains unimplemented — `/auth/github/login` and `/auth/github/callback` are untouched and still return 501. This feature is a *second*, separate use case: a locally-authenticated user (via `auth_service`) linking a GitHub account so GraphForge can read their repos. It reuses the same `IOAuthProvider` contract (extended with `list_repositories`) and the same concrete `GitHubOAuthProvider`, but through entirely new routes (`/github/connect`, `/github/callback`, `/github/connection`, `/github/repositories`) and a new dependency getter (`github_service._build_provider`) — deliberately not `api/v1/dependencies.get_oauth_provider`, which stays wired to the still-unimplemented login stub. Wiring both use cases to the same provider getter would have made the login stub silently start "working" (returning empty 200s instead of a clear 501) the moment GitHub credentials were configured for the connect flow — a real bug, caught and avoided by keeping the two dependency-injection points separate.

**Stateless, signed OAuth `state`.** The connect flow needs to know *which user* initiated it when GitHub redirects back to `/github/callback` — that redirect is a top-level browser navigation with no Authorization header. Rather than a server-side state store (Redis, a DB table), `state` is a short-lived (10 minute) JWT with `sub=user_id`, created with the exact same `create_access_token`/`decode_access_token` functions `core.security` already provides for login sessions. No new infrastructure, and it's inherently CSRF-resistant (signed, time-limited, single-purpose).

**The frontend fetches the authorization URL via JSON, then navigates to it itself.** `GET /github/connect` is a normal JWT-authenticated JSON endpoint (`{authorization_url}`), not a redirect endpoint — a `window.location.href = ...` navigation from the browser can't carry an Authorization header, so the endpoint that *needs* the JWT must be reached via `fetch`, and the endpoint that *doesn't* (github.com's own authorize page) is what the browser navigates to directly.

**GitHub access tokens are encrypted at rest.** `core/crypto.py` wraps `cryptography`'s Fernet with a `TOKEN_ENCRYPTION_KEY` setting (same insecure-dev-default-with-a-loud-comment pattern as `JWT_SECRET_KEY`). A real access token is meaningfully more sensitive than session bookkeeping — worth the one extra dependency and ~30 lines.

**`Repository` rows belong to a user, not a global registry.** Two users tracking the same public repo get two independent rows (`UniqueConstraint(user_id, github_repo_id)`, not a bare unique on `github_repo_id`). This matters for the webhook handler: one GitHub delivery for one repo can fan out to updating *multiple* `PullRequest` rows, one per tracking user.

**Selecting repositories replaces the whole set, not an add/remove diff.** `POST /repositories` takes the full desired list and the service diffs it against what's already tracked — inserting new, updating metadata on existing, deleting ones no longer present. This matches a checkbox-list-plus-Save-button UI far better than incremental add/remove calls would, and it's simpler: the client never needs to track "what changed," only "what's currently checked."

**The webhook endpoint is signature-verified, not JWT-authenticated**, and fails closed: if `GITHUB_WEBHOOK_SECRET` isn't set, every delivery is rejected (503) rather than accepted unverified. Signature verification happens over the *raw* request body (`await request.body()`), before any JSON parsing — HMAC is computed over exact bytes, and re-serializing the parsed JSON would not reproduce them.

**`ping` events are handled explicitly.** GitHub sends a `ping` event immediately when a webhook is created, to verify the endpoint responds correctly; a naive implementation that only branches on `pull_request` would either error or silently 200 in a way that doesn't confirm anything. This endpoint explicitly acks `ping` with `{"status": "pong"}`.

**Metadata only — no diff content, no risk scoring.** `PullRequest` stores number, title, state, author, branch refs, and timestamps; nothing about the diff itself. `IVersionControlProvider.get_diff` (ADR 0001/0003's placeholder for exactly this) remains unimplemented, on purpose — that's the AI-analysis feature this task explicitly excluded.

**Registering the webhook on the GitHub repo itself is a manual step, not automated.** The task asked for the *receiving* endpoint, reading events, and persisting them — not for auto-provisioning webhooks via GitHub's API (`POST /repos/{owner}/{repo}/hooks`, requiring `admin:repo_hook` scope). Automating that is a reasonable next step, tracked as a gap rather than built speculatively; see `docs/setup.md` for the manual setup instructions in the meantime.

## Verification strategy (given the no-company-infra constraint)

- **OAuth flow**: `GitHubOAuthProvider`'s HTTP-calling methods are patched at the class level (`unittest.mock.patch.object`) in `tests/integration/test_github_oauth.py` — the real routes, services, and database are exercised end to end; only the two calls that would hit `github.com`/`api.github.com` are stubbed. No GitHub account, company or personal, is touched.
- **Webhook**: `tests/integration/test_webhooks.py` uses hand-crafted payloads shaped exactly like GitHub's real `pull_request` webhook schema, signed with a real HMAC-SHA256 computation the same way GitHub itself signs deliveries. This is a stronger test than mocking, not a weaker one — it proves the endpoint works against the actual wire format.
- **What isn't (and can't be) verified here**: an actual user clicking "Authorize" on github.com. That requires a real personal GitHub OAuth App and a human in a real browser — see `docs/setup.md` for how to do that yourself once this is running.

## Consequences
- Enabling this for real requires the user's own personal GitHub OAuth App (`GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET`) and a manually-configured webhook (`GITHUB_WEBHOOK_SECRET`) on each repo they want events from — see `docs/setup.md`.
- Login-via-GitHub (the login page's disabled "Continue with GitHub" button) is still not implemented; this ADR doesn't change that.
- The next natural extension — reading a PR's actual diff (`IVersionControlProvider.get_diff`) and doing something with it — is exactly the boundary this task stopped short of.
