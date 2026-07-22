"""Symmetric encryption for secrets stored at rest — today, just GitHub
access tokens in `GitHubConnection.access_token`.

The only module allowed to import `cryptography` directly.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.exceptions import AppError


class TokenDecryptionError(AppError):
    """Raised when a stored encrypted token can't be decrypted — e.g. the
    encryption key changed since it was written."""

    status_code = 500
    error_code = "token_decryption_failed"


@lru_cache
def _fernet() -> Fernet:
    return Fernet(get_settings().token_encryption_key.encode("utf-8"))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise TokenDecryptionError(
            "Stored secret could not be decrypted; TOKEN_ENCRYPTION_KEY may have changed."
        ) from exc
