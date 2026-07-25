from __future__ import annotations

import re
from typing import Any

from app.services.external_context.base import ExternalContextItem, ExternalContextProvider


class GoogleDriveProvider(ExternalContextProvider):
    name = "google_drive"

    async def collect(self, user_input: str, config: dict[str, Any]) -> list[ExternalContextItem]:
        matches = re.findall(r"https://(?:drive\.google\.com|docs\.google\.com)/[^\s]+", user_input)
        if not matches:
            return []
        file_ids = []
        for match in matches:
            file_ids.append(match)
        return [
            ExternalContextItem(
                provider="google_drive",
                title="Google Drive reference",
                summary="Google Drive link provided by the user",
                details=f"file_ids={file_ids}",
                references=file_ids,
                raw={"file_ids": file_ids},
            )
        ]
