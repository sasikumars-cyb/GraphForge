"""Tests for app.core.crypto — Fernet encryption for secrets at rest
(GitHub access tokens, AI provider API keys).

Previously only exercised incidentally as test fixture setup (calling
encrypt_secret to produce a value for another test), never as a direct
correctness/failure-path check of this module itself.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.crypto import TokenDecryptionError, decrypt_secret, encrypt_secret


class TestEncryptDecryptRoundTrip:
    def test_round_trip_returns_original_value(self):
        plaintext = "ghp_abcdef1234567890"
        ciphertext = encrypt_secret(plaintext)
        assert decrypt_secret(ciphertext) == plaintext

    def test_ciphertext_is_not_the_plaintext(self):
        plaintext = "super-secret-token"
        ciphertext = encrypt_secret(plaintext)
        assert ciphertext != plaintext
        assert plaintext not in ciphertext

    def test_encrypting_the_same_value_twice_yields_different_ciphertext(self):
        """Fernet includes a random IV per call — encrypting the same
        plaintext twice must not produce identical ciphertext (otherwise
        stored secrets would leak repetition patterns)."""
        plaintext = "same-value"
        assert encrypt_secret(plaintext) != encrypt_secret(plaintext)

    def test_empty_string_round_trips(self):
        assert decrypt_secret(encrypt_secret("")) == ""


class TestDecryptionFailures:
    def test_ciphertext_encrypted_with_a_different_key_is_rejected(self):
        """Regression scenario: TOKEN_ENCRYPTION_KEY changing between when a
        secret was stored and when it's read back (e.g. a misconfigured
        deployment) must surface as a clean TokenDecryptionError, not a raw
        `cryptography` exception leaking past this module's boundary."""
        foreign_ciphertext = Fernet(Fernet.generate_key()).encrypt(b"some-token").decode("utf-8")
        with pytest.raises(TokenDecryptionError):
            decrypt_secret(foreign_ciphertext)

    def test_garbage_input_is_rejected_cleanly(self):
        with pytest.raises(TokenDecryptionError):
            decrypt_secret("this-is-not-a-fernet-token-at-all")

    def test_empty_string_input_is_rejected_cleanly(self):
        with pytest.raises(TokenDecryptionError):
            decrypt_secret("")
