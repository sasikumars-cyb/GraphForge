"""The Engineering Intelligence Service Layer — reusable domain services
consumed by every Engineering Intelligence Agent. See `contracts.py` for
the shared result shapes and each module's docstring for what it owns.

No UI code, no agent-specific prompt logic, no LLM calls, and no
duplicate retrieval/traversal/confidence logic live here — the constraints
the approved RFC placed on this package.
"""
