"""One-shot script: fix the Confluence knowledge connection to use MCP transport.

Run inside the backend container:
    python -m scripts.fix_confluence_connection
"""

import asyncio
import json
import sys

from sqlalchemy import select, update

from app.core.crypto import decrypt_secret, encrypt_secret
from app.database.session import AsyncSessionLocal
from app.models.knowledge_connection import KnowledgeConnection


async def main() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(KnowledgeConnection).where(
                KnowledgeConnection.source_type == "confluence",
            )
        )
        row = result.scalars().first()
        if row is None:
            print("ERROR: No confluence connection found.")
            sys.exit(1)

        print(f"Found connection: id={row.id}, name={row.name}")
        print(f"  transport={row.transport}, auth_method={row.auth_method}")
        print(f"  config={json.dumps(row.config, indent=2)}")
        print(f"  enabled={row.enabled}, status={row.status}")

        # Decrypt existing credentials
        if row.encrypted_credentials:
            creds = json.loads(decrypt_secret(row.encrypted_credentials))
            # Mask sensitive values for display
            masked = {k: v[:8] + "..." if len(v) > 8 else "***" for k, v in creds.items()}
            print(f"  credentials keys={list(creds.keys())}, masked={masked}")
        else:
            print("  credentials=NONE")
            creds = {}

        # Determine the api_key value
        api_key = creds.get("api_key") or creds.get("api_token") or creds.get("token")
        if not api_key:
            print("\nERROR: No api_key/api_token/token found in credentials.")
            print(f"  Available keys: {list(creds.keys())}")
            sys.exit(1)

        # Build the correct config and credentials for MCP transport
        base_url = (row.config or {}).get("base_url", "")
        new_config = {
            "server_url": "https://mcp.atlassian.com/v1/mcp/authv2",
            "base_url": base_url,
        }
        new_credentials = json.dumps({"api_key": api_key})
        encrypted = encrypt_secret(new_credentials)

        print("\n--- Applying fix ---")
        print(f"  transport: {row.transport} -> mcp")
        print(f"  auth_method: {row.auth_method} -> api_key")
        print(f"  config: {json.dumps(new_config, indent=2)}")
        print("  credentials: re-encrypted with api_key field")

        await db.execute(
            update(KnowledgeConnection)
            .where(KnowledgeConnection.id == row.id)
            .values(
                transport="mcp",
                auth_method="api_key",
                config=new_config,
                encrypted_credentials=encrypted,
            )
        )
        await db.commit()
        print("\nDone! Connection updated to MCP transport.")


if __name__ == "__main__":
    asyncio.run(main())
