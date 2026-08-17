"""Phase 3, hard cutover guardrail — the Control Plane is the sole
authorization authority.

Backs the claim `app/control_plane/control_plane.py`'s own module
docstring makes: "Nothing outside this class may issue an
`AuthorizationGrant` or append an `Authorization*` Engineering Event."
This test makes that structurally checked, matching every other Phase 0-2
architecture-boundary test's convention (a ratchet, not a rewrite —
grandfathered sets are frozen allowlists, growth fails CI).
"""

from __future__ import annotations

from tests.unit.architecture._source_scan import find_imports_of, relative

_INTERNAL_TO_CONTROL_PLANE_PREFIX = "app/control_plane/"
_TEST_PATH_PREFIXES: tuple[str, ...] = ("tests/",)

# No file outside app/control_plane/ or tests/ constructs an
# AuthorizationGrant directly today. A new entry here is a deliberate,
# reviewable widening of who may issue authorization — not something to
# add just to make this test pass.
_ALLOWED_AUTHORIZATION_GRANT_IMPORTERS: frozenset[str] = frozenset()


def test_authorization_grant_is_not_constructed_outside_the_control_plane() -> None:
    hits = find_imports_of("app.control_plane.grant", symbol="AuthorizationGrant")
    offenders = {
        relative(path)
        for path in hits
        if not relative(path).startswith(_INTERNAL_TO_CONTROL_PLANE_PREFIX)
        and not relative(path).startswith(_TEST_PATH_PREFIXES)
        and relative(path) not in _ALLOWED_AUTHORIZATION_GRANT_IMPORTERS
    }
    assert not offenders, (
        f"AuthorizationGrant imported outside app/control_plane/: {sorted(offenders)}. "
        "Cap §7: the Control Plane is the sole authority that may issue a Grant — "
        "no other module may construct one, even to read its shape."
    )


def test_control_plane_module_is_the_only_importer_of_the_event_repository_for_authorization() -> (
    None
):
    """A weaker, more honest check than "no one else imports
    EngineeringEventRepository at all" (Phase 1 already established many
    legitimate callers for non-authorization event types). This instead
    confirms the one file that both (a) imports `EngineeringEventRepository`
    and (b) imports the `AUTHORIZATION_*` event-type constants is
    `control_plane.py` itself — i.e., nothing else has both the means and
    the vocabulary to append an Authorization* event."""
    repo_importers = {
        relative(p) for p in find_imports_of("app.repositories.engineering_event_repository")
    }
    auth_const_importers = {
        relative(p)
        for p in find_imports_of("app.engineering_state.events", symbol="AUTHORIZATION_GRANTED")
    }
    both = repo_importers & auth_const_importers
    offenders = {
        f
        for f in both
        if not f.startswith(_INTERNAL_TO_CONTROL_PLANE_PREFIX)
        and not f.startswith(_TEST_PATH_PREFIXES)
    }
    assert not offenders, (
        f"Unexpected set of modules with both Engineering Event append access "
        f"and Authorization* event vocabulary: {sorted(offenders)}. Only "
        "app/control_plane/control_plane.py should have both."
    )
    assert "app/control_plane/control_plane.py" in both, (
        "app/control_plane/control_plane.py no longer imports both "
        "EngineeringEventRepository and the AUTHORIZATION_GRANTED constant — "
        "this test's own premise is stale; update it alongside whatever "
        "structural change caused this."
    )
