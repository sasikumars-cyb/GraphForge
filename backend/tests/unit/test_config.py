"""Smoke test proving settings load without touching a real environment."""

from app.core.config import get_settings


def test_settings_have_sane_defaults() -> None:
    settings = get_settings()

    assert settings.app_name == "GraphForge"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.database_url.startswith("postgresql+asyncpg://")
