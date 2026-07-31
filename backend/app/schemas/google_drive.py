"""Request/response schemas for the Google Drive OAuth connection."""

from datetime import datetime

from pydantic import BaseModel


class GoogleDriveConnectionStatus(BaseModel):
    connected: bool
    google_email: str | None = None
    connected_at: datetime | None = None


class GoogleDriveConnectAuthorizationUrl(BaseModel):
    authorization_url: str
