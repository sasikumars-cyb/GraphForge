"""Request/response schemas for admin-managed OAuth App credentials
(GitHub, Google Drive) — see app.models.oauth_app_credential.
"""

from pydantic import BaseModel


class OAuthAppCredentialStatus(BaseModel):
    provider_key: str
    configured: bool
    # "database" - stored via this API and takes precedence; "environment" -
    # falling back to GITHUB_CLIENT_ID/GOOGLE_CLIENT_ID etc; "unset" -
    # neither is configured, "Connect via OAuth" will 503.
    source: str
    # Client IDs aren't secret (they're visible in the OAuth redirect URL
    # itself), so this is returned in full - unlike the secret, which never
    # leaves the backend.
    client_id: str | None = None


class OAuthAppCredentialUpdate(BaseModel):
    client_id: str
    client_secret: str
