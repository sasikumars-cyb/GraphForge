"""Makes GraphForge's backend package importable from this framework
without installing it — this framework lives alongside `backend/` in the
same repo, not inside it, so `app.*` isn't on `sys.path` by default.

Only two things are imported from `app.*` anywhere in this framework
(see `lib/client.py` and `lib/memory.py`): `app.core.security` to mint a
session token the same way a real login would, and
`app.knowledge_engine.memory_service` to read Engineering Memory
provenance that no REST endpoint exposes. Everything else goes through
HTTP, same as any other API client.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_PATH = Path(__file__).resolve().parents[2] / "backend"


def ensure_backend_importable() -> None:
    path_str = str(_BACKEND_PATH)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
