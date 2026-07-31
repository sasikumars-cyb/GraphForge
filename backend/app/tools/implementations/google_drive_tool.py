"""Google Drive Tool — real implementation, REST only.

Fetches a file's content (or a folder's file listing) given a Drive/Docs/
Sheets/Slides URL found anywhere in the tool's `query` string. Used by
Context Discovery so a task description that links a design doc plans
against its real, current content instead of the literal URL string — the
same enrichment Jira/GitHub references already get (see
app.tools.implementations.jira_tool/github_tool).

Per-user, like GitHubTool: Drive access is a per-user OAuth connection
(app.models.google_drive_connection), not an install-wide credential, so
this is never registered on the global Tool Registry singleton — callers
construct one instance per run using that run's own user's token (see
app.context_pipeline.providers.GoogleDriveProvider).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.tools.interfaces import ToolCategory, ToolHealth, ToolInput, ToolResult

logger = logging.getLogger(__name__)

_API_BASE = "https://www.googleapis.com/drive/v3"
_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
# Google's native formats have no raw bytes to download - they're exported
# to a plain-text-ish format instead. Anything not listed here (PDFs,
# images, binaries) falls through to "can't be read as text" rather than
# attempting extraction this pass doesn't support.
_EXPORTABLE_MIME_TYPES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
_MAX_CONTENT_CHARS = 20_000

# Matches the same URL shapes app.context_pipeline.reference_detection
# recognizes - defined here (not there) so the extraction logic lives
# beside the tool that actually acts on it, and reference_detection
# imports this the same way it already imports extract_issue_key/
# extract_pr_or_issue_ref from jira_tool/github_tool.
_DRIVE_URL_RE = re.compile(
    r"https?://(?:drive|docs)\.google\.com/"
    r"(?:file/d/|drive/folders/|document/d/|spreadsheets/d/|presentation/d/|open\?id=)"
    r"([\w-]+)"
)


def extract_drive_file_id(text: str) -> str | None:
    """Return the first Google Drive/Docs/Sheets/Slides file or folder id
    found in free text, or None."""
    match = _DRIVE_URL_RE.search(text)
    return match.group(1) if match else None


class GoogleDriveApiError(Exception):
    """Raised internally when the Drive API returns an error response."""


class GoogleDriveTool:
    """Fetches file content or a folder's file listing from Google Drive."""

    tool_id = "google_drive"
    display_name = "Google Drive"
    description = (
        "Fetches file content (or a folder's file listing) from Google Drive, given a "
        "file/folder referenced by URL. Used to enrich agent context with linked design "
        "docs and specs."
    )
    category = ToolCategory.DOCUMENTATION
    capabilities = ["file_content", "folder_listing"]

    def __init__(self, config: dict[str, Any]) -> None:
        self._token: str = config.get("google_drive_access_token", "")

    def requires_auth(self) -> bool:
        return True

    @property
    def _configured(self) -> bool:
        return bool(self._token)

    async def execute(self, input: ToolInput) -> ToolResult:
        if not self._configured:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="Google Drive is not connected for this user.",
            )

        file_id = extract_drive_file_id(input.query)
        if file_id is None:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error="No Google Drive file/folder reference found in the query.",
            )

        try:
            metadata = await self._get_metadata(file_id)
        except GoogleDriveApiError as exc:
            logger.warning("google_drive_tool_metadata_failed file_id=%s error=%s", file_id, exc)
            return ToolResult(
                tool_id=self.tool_id, tool_name=self.display_name, success=False, error=str(exc)
            )
        except httpx.HTTPError as exc:
            return ToolResult(
                tool_id=self.tool_id,
                tool_name=self.display_name,
                success=False,
                error=f"Google Drive request failed: {exc}",
            )

        name = str(metadata.get("name", file_id))
        mime_type = str(metadata.get("mimeType", ""))
        web_link = str(metadata.get("webViewLink", ""))

        if mime_type == _FOLDER_MIME_TYPE:
            return await self._build_folder_result(file_id, name, web_link)
        return await self._build_file_result(file_id, name, mime_type, web_link)

    async def _get_metadata(self, file_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{_API_BASE}/files/{file_id}",
                params={"fields": "id,name,mimeType,webViewLink"},
                headers={"Authorization": f"Bearer {self._token}"},
            )
        if response.status_code == 404:
            raise GoogleDriveApiError(f"Drive file/folder '{file_id}' not found (or not shared).")
        if response.status_code == 401:
            raise GoogleDriveApiError("Google Drive authentication failed — reconnect the account.")
        response.raise_for_status()
        return dict(response.json())

    async def _build_folder_result(self, file_id: str, name: str, web_link: str) -> ToolResult:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{_API_BASE}/files",
                params={
                    "q": f"'{file_id}' in parents and trashed = false",
                    "fields": "files(name,mimeType)",
                    "pageSize": 50,
                },
                headers={"Authorization": f"Bearer {self._token}"},
            )
        response.raise_for_status()
        children = response.json().get("files", [])
        listing = "\n".join(f"- {f.get('name', '')}" for f in children) or "(empty folder)"
        context_text = f"Google Drive folder '{name}':\n{listing}"
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=True,
            data={
                "file_id": file_id,
                "name": name,
                "is_folder": True,
                "context_text": context_text,
                "url": web_link,
            },
            summary=f"Listed Google Drive folder '{name}' ({len(children)} item(s)).",
            evidence_items=[context_text],
        )

    async def _build_file_result(
        self, file_id: str, name: str, mime_type: str, web_link: str
    ) -> ToolResult:
        export_mime = _EXPORTABLE_MIME_TYPES.get(mime_type)
        content = ""
        if export_mime is not None:
            content = await self._export(file_id, export_mime)
        elif mime_type.startswith("text/"):
            content = await self._download_raw(file_id)
        # Anything else (PDF, image, binary) is left with no extracted
        # content — the file's existence and link are still useful
        # context, and this pass doesn't attempt document parsing.

        content = content[:_MAX_CONTENT_CHARS]
        context_text = f"Google Drive file '{name}' ({mime_type}):\n" + (
            content if content else "(content not extracted — not a text-based format)"
        )
        return ToolResult(
            tool_id=self.tool_id,
            tool_name=self.display_name,
            success=True,
            data={
                "file_id": file_id,
                "name": name,
                "mime_type": mime_type,
                "is_folder": False,
                "content": content,
                "context_text": context_text,
                "url": web_link,
            },
            summary=f"Fetched Google Drive file '{name}'.",
            evidence_items=[context_text],
        )

    async def _export(self, file_id: str, export_mime: str) -> str:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{_API_BASE}/files/{file_id}/export",
                params={"mimeType": export_mime},
                headers={"Authorization": f"Bearer {self._token}"},
            )
        response.raise_for_status()
        return response.text

    async def _download_raw(self, file_id: str) -> str:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{_API_BASE}/files/{file_id}",
                params={"alt": "media"},
                headers={"Authorization": f"Bearer {self._token}"},
            )
        response.raise_for_status()
        return response.text

    async def health_check(self) -> ToolHealth:
        if not self._configured:
            return ToolHealth.UNCONFIGURED
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{_API_BASE}/about",
                    params={"fields": "user"},
                    headers={"Authorization": f"Bearer {self._token}"},
                )
            if response.status_code == 401:
                return ToolHealth.AUTH_FAILED
            if response.status_code >= 500:
                return ToolHealth.UNAVAILABLE
            return ToolHealth.HEALTHY if response.status_code < 400 else ToolHealth.OFFLINE
        except httpx.HTTPError:
            return ToolHealth.OFFLINE
