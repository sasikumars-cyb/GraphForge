"""One-shot diagnostic: run a real Context Discovery cycle end-to-end and
prove KnowledgeAccessResolver is the code path Confluence document
discovery actually goes through — instruments resolve_knowledge_access to
log every call/result instead of just trusting it silently worked.

Run inside the backend container:
    python -m scripts.trace_confluence_resolver
"""

import asyncio
import uuid

from app.agents._contract import AgentContext, Subject
from app.agents.context_discovery.agent import ContextDiscoveryAgent
from app.context.resolvers.freetext import resolve as resolve_freetext
from app.database.session import AsyncSessionLocal
from app.tools.setup import register_all_tools, sync_all_knowledge_connections_to_tools


async def main() -> None:
    # This script is a separate process from the running uvicorn server —
    # it never went through app.main's lifespan, so the Tool Registry
    # starts empty. Reproduce that startup step here so Jira/graph tools
    # actually work, same as the real app.
    register_all_tools()
    async with AsyncSessionLocal() as startup_db:
        await sync_all_knowledge_connections_to_tools(startup_db)
        # Same reason: app.ai.config.store's snapshot also starts empty
        # outside app.main's lifespan. Without this, resolve() falls
        # through to the environment-tier provider (Settings.ai_provider)
        # instead of whatever is actually configured in the UI — this
        # script would then silently trace a different provider than the
        # one a real request actually uses.
        from app.ai.config import store

        await store.refresh(startup_db)

    import app.knowledge.access_resolver as resolver_module

    real_resolve = resolver_module.resolve_knowledge_access
    calls: list[tuple[str, str]] = []

    async def instrumented_resolve(db, source_type, capability):
        access = await real_resolve(db, source_type, capability)
        calls.append((source_type, str(capability)))
        print(
            f"[RESOLVER CALL] source_type={source_type!r} capability={capability!r} "
            f"-> available={access.available} "
            f"methods={[(m.transport.value, m.synthesized) for m in access.methods]}"
        )
        return access

    resolver_module.resolve_knowledge_access = instrumented_resolve
    # ConfluenceProvider imported the function by reference at module load
    # time — patch its own binding too, exactly like the test suite's
    # `unittest.mock.patch("app.context_pipeline.providers.resolve_knowledge_access", ...)`
    # does, so the instrumentation is actually on the path that's called.
    import app.context_pipeline.providers as providers_module

    providers_module.resolve_knowledge_access = instrumented_resolve

    subject = resolve_freetext(
        "https://cybage-team-n8wdf7c7.atlassian.net/browse/NPT-30"
    )
    print(f"\n[SUBJECT] subject_id={subject.subject_id!r} display_name={subject.display_name!r}\n")

    async with AsyncSessionLocal() as db:
        context = AgentContext(
            subject=subject,
            goal="discover_context",
            model=None,
            extras={
                "db": db,
                "user_id": uuid.UUID("420072cc-f0ce-4748-aa1d-c688afd8cf72"),
            },
        )
        output = await ContextDiscoveryAgent().run(context)

    result = output.result
    print("\n[RESULT]")
    print("  readiness:", result.get("readiness"))
    print("  capability_confidence:", result.get("capability_confidence"))
    print("\n[ALL EVIDENCE]")
    for e in output.evidence:
        print(f"  - kind={e.kind} reference={e.reference} status={e.status} summary={e.summary[:120]}")
    eu = result.get("engineering_understanding")
    print("\n[ENGINEERING UNDERSTANDING]")
    print("  populated:", bool(eu))
    if eu:
        print("  keys:", list(eu.keys()))
    print(f"\n[RESOLVER CALLS TOTAL]: {len(calls)} -> {calls}")
    assert any(c[0] == "confluence" for c in calls), (
        "FAIL: resolve_knowledge_access was never called for confluence — "
        "ConfluenceProvider did not go through the resolver"
    )
    print("\n[VERIFIED] ConfluenceProvider called resolve_knowledge_access for source_type='confluence'.")


if __name__ == "__main__":
    asyncio.run(main())
