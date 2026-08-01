"""One-shot diagnostic: run DocumentationReviewAgent end-to-end against a
real tracked repository and print the full result — same verification
rigor as scripts/trace_confluence_resolver.py.

Run inside the backend container:
    python -m scripts.trace_documentation_agent <repository_full_name>
"""

import asyncio
import json
import sys
import uuid

from sqlalchemy import select

from app.agents._contract import AgentContext
from app.agents.documentation.agent import DocumentationReviewAgent, resolve_repository_subject
from app.database.session import AsyncSessionLocal
from app.models.repository import Repository


async def main() -> None:
    full_name = sys.argv[1] if len(sys.argv) > 1 else "CybHackathon-2026/Hi-Tech_GraphForge"

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(Repository).where(Repository.full_name == full_name))
        ).scalar_one_or_none()
        if row is None:
            print(f"Repository '{full_name}' not found.")
            return

        context = AgentContext(
            subject=resolve_repository_subject(row),
            goal="review_documentation",
            extras={"db": db, "user_id": row.user_id},
        )
        output = await DocumentationReviewAgent().run(context)

    print(f"\n[REPOSITORY] {full_name}")
    print(f"[CONFIDENCE] score={output.confidence.score} reasoning={output.confidence.reasoning}")
    print("\n[EVIDENCE]")
    for e in output.evidence:
        print(f"  - kind={e.kind} status={e.status} {e.reference}: {e.summary[:150]}")

    result = output.result
    print(f"\n[SUMMARY] {result.get('summary')}")
    print(f"[FILES REVIEWED] {len(result.get('files_reviewed', []))}")
    for f in result.get("files_reviewed", []):
        print(f"    - [{f['category']}] {f['path']} ({f['size_bytes']} bytes)")
    print(f"[FINDINGS] {len(result.get('findings', []))}")
    for finding in result.get("findings", []):
        print(f"    - [{finding['finding_type']}/{finding['severity']}] {finding['file_path']}: {finding['description']}")
    print(f"[PROPOSED UPDATES] {len(result.get('proposed_updates', []))}")
    for u in result.get("proposed_updates", []):
        print(f"    - {u['file_path']}: {u['rationale']}")
    print(f"[PROPOSED NEW DOCUMENTS] {len(result.get('proposed_new_documents', []))}")
    for d in result.get("proposed_new_documents", []):
        print(f"    - {d['file_path']}: {d['title']}")

    with open("/tmp/documentation_agent_result.json", "w") as f:
        json.dump(result, f, indent=2)
    print("\n[FULL RESULT WRITTEN TO] /tmp/documentation_agent_result.json")


if __name__ == "__main__":
    asyncio.run(main())
