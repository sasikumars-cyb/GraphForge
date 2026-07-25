from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.models.external_context_settings import ExternalContextSettings
from app.services.external_context.registry import ExternalContextRegistry


async def load_workspace_external_context_config(db: AsyncSession) -> dict[str, Any]:
    """Return the provider-keyed config dict (e.g. {"jira": {...}}), with
    secrets decrypted — the shape ExternalContextRegistry.collect() expects.
    """
    row = (await db.execute(select(ExternalContextSettings).limit(1))).scalar_one_or_none()
    if row is None:
        return {}
    settings = dict(row.settings or {})
    providers = settings.get("providers", {}) or {}
    if not isinstance(providers, dict):
        return {}
    for provider_name, provider_config in list(providers.items()):
        if isinstance(provider_config, dict):
            for key, value in list(provider_config.items()):
                if key in {"api_token", "api_key", "service_account_json", "client_credentials"} and isinstance(value, str) and value:
                    try:
                        provider_config[key] = decrypt_secret(value)
                    except Exception:
                        provider_config[key] = value
            providers[provider_name] = provider_config
    return providers


async def collect_external_context(user_input: str, db: AsyncSession) -> list[dict[str, Any]]:
    config = await load_workspace_external_context_config(db)
    registry = ExternalContextRegistry()
    items = await registry.collect(user_input, config)
    return [
        {
            "provider": item.provider,
            "title": item.title,
            "summary": item.summary,
            "details": item.details,
            "references": item.references,
        }
        for item in items
    ]
