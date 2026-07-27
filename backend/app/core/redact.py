"""Redact credential-shaped substrings from text before it enters a prompt
or gets persisted (LLMTrace, Evidence).

Mitigates OWASP LLM02 (Sensitive Information Disclosure) / LLM07 (System
Prompt Leakage): a Jira ticket or GitHub issue fetched into a planning
prompt may contain a secret someone pasted for debugging context (an API
key, a token, a private key). Once that text is sent to an LLM provider
and stored verbatim in LLMTrace, it has both left the org's boundary (the
provider) and landed in a UI surface (the workflow Log tab) with no
access control of its own. Redacting before either happens is cheap
insurance; it does not depend on knowing who can see the workflow.

Pattern-based, not a secret-scanning service — covers common credential
shapes (cloud keys, tokens, private key blocks, generic
password/secret/token assignments). False negatives are expected for
exotic formats; false positives are acceptable here since the redacted
text still analyses fine as a plan input.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("github_fine_grained_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)),
    (
        "generic_credential_assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=]{8,}['\"]?"
        ),
    ),
]


def redact_secrets(text: str) -> str:
    """Return `text` with credential-shaped substrings replaced by a
    `[REDACTED:<kind>]` marker. Idempotent and safe to call on already-clean
    text (no matches → returned unchanged)."""
    if not text:
        return text
    redacted = text
    for kind, pattern in _PATTERNS:
        redacted = pattern.sub(f"[REDACTED:{kind}]", redacted)
    return redacted
