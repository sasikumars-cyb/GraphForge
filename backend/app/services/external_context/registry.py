from __future__ import annotations

import re
from typing import Any

from app.services.external_context.base import ExternalContextItem, ExternalContextProvider
from app.services.external_context.providers.jira import JiraProvider
from app.services.external_context.providers.testrail import TestRailProvider
from app.services.external_context.providers.google_drive import GoogleDriveProvider


class ExternalContextRegistry:
    """Registry of external context providers for the Testing agent."""

    def __init__(self) -> None:
        self._providers: dict[str, ExternalContextProvider] = {}
        self.register(JiraProvider())
        self.register(TestRailProvider())
        self.register(GoogleDriveProvider())

    def register(self, provider: ExternalContextProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, provider_name: str) -> ExternalContextProvider | None:
        return self._providers.get(provider_name)

    def detect(self, user_input: str) -> list[str]:
        detected: list[str] = []
        # Matches any Jira issue key (e.g. SCRUM-6, PROJ-123), same pattern
        # JiraProvider.collect() itself extracts keys with — not just the
        # literal "PROJ" placeholder from the original example prompt.
        if re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", user_input):
            detected.append("jira")
        if re.search(r"\b(suite|testrail)\s+#?\d+\b", user_input, flags=re.IGNORECASE):
            detected.append("testrail")
        if re.search(r"\b(google drive|drive.google.com|docs.google.com)\b", user_input, flags=re.IGNORECASE):
            detected.append("google_drive")
        return detected

    async def collect(self, user_input: str, config: dict[str, Any]) -> list[ExternalContextItem]:
        items: list[ExternalContextItem] = []
        for provider_name in self.detect(user_input):
            provider = self.get(provider_name)
            if provider is None:
                continue
            items.extend(await provider.collect(user_input, config.get(provider_name, {})))
        return items
