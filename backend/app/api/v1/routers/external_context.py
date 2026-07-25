from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_user
from app.core.crypto import encrypt_secret
from app.database.session import get_db_session
from app.models.external_context_settings import ExternalContextSettings
from app.models.user import User

router = APIRouter(prefix="/external-context", tags=["external-context"])


@router.get("", summary="Get workspace external context configuration")
async def get_external_context_settings(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    row = (await db.execute(select(ExternalContextSettings).limit(1))).scalar_one_or_none()
    if row is None:
        return {"providers": {}}
    settings = dict(row.settings or {})
    providers = settings.get("providers", {}) or {}
    return {"providers": providers if isinstance(providers, dict) else {}}


@router.put("", summary="Save workspace external context configuration")
async def save_external_context_settings(
    payload: dict[str, object],
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    row = (await db.execute(select(ExternalContextSettings).limit(1))).scalar_one_or_none()
    if row is None:
        row = ExternalContextSettings()
        db.add(row)

    providers = dict(payload.get("providers") or {})
    normalized_providers: dict[str, dict[str, object]] = {}
    for provider_name, provider_config in providers.items():
        if not isinstance(provider_config, dict):
            continue
        normalized_config: dict[str, object] = {}
        for key, value in provider_config.items():
            if key in {"api_token", "api_key", "service_account_json", "client_credentials"} and isinstance(value, str) and value:
                normalized_config[key] = encrypt_secret(value)
            else:
                normalized_config[key] = value
        normalized_providers[provider_name] = normalized_config
    row.settings = {"providers": normalized_providers}
    await db.commit()
    return {"ok": True}
