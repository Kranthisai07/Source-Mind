"""The role-change rules (D-064), checked exhaustively and by named case."""

from __future__ import annotations

import itertools

import pytest
from pydantic import ValidationError as PydanticValidationError

from sourcemind.core.exceptions import WorkspaceAccessDeniedError
from sourcemind.schemas.workspace import WorkspaceMemberRoleUpdate
from sourcemind.services.workspace import roles
from sourcemind.services.workspace.roles import authorize_role_change, loses_contribution

ALL = ("owner", "admin", "member", "viewer")


def allowed(caller: str, target: str, new: str, owners: int = 2) -> bool:
    try:
        authorize_role_change(
            caller_role=caller, target_role=target, new_role=new, active_owner_count=owners
        )
    except WorkspaceAccessDeniedError:
        return False
    return True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("caller", "target", "new", "owners", "expected"),
    [
        # Founders can do everything except strand the workspace.
        ("owner", "member", "admin", 1, True),
        ("owner", "viewer", "owner", 1, True),  # promoting a co-founder
        ("owner", "admin", "member", 1, True),
        ("owner", "owner", "admin", 2, True),  # a founder may step down if another remains
        ("owner", "owner", "owner", 1, True),  # not a change
        ("owner", "owner", "admin", 1, False),  # the last founder
        ("owner", "owner", "viewer", 1, False),
        # Admins can only move people between teammate and viewer.
        ("admin", "member", "viewer", 2, True),
        ("admin", "viewer", "member", 2, True),
        ("admin", "member", "admin", 2, False),  # no granting admin
        ("admin", "member", "owner", 2, False),  # no granting founder
        ("admin", "viewer", "admin", 2, False),
        ("admin", "admin", "member", 2, False),  # cannot demote an admin (including themselves)
        ("admin", "admin", "owner", 2, False),  # cannot promote themselves
        ("admin", "owner", "member", 2, False),  # cannot touch a founder
        ("admin", "owner", "owner", 2, False),
        # Everyone else can change nothing.
        ("member", "viewer", "member", 2, False),
        ("viewer", "member", "viewer", 2, False),
    ],
)
def test_named_cases(caller, target, new, owners, expected) -> None:
    assert allowed(caller, target, new, owners) is expected


@pytest.mark.unit
def test_an_admin_can_never_produce_admin_or_founder_nor_touch_one() -> None:
    """The escalation invariant, over every combination, whatever the founder count."""
    for target, new, owners in itertools.product(ALL, ALL, (1, 2, 5)):
        if allowed("admin", target, new, owners):
            assert new in ("member", "viewer")
            assert target in ("member", "viewer")


@pytest.mark.unit
def test_members_and_viewers_and_unknown_roles_can_change_nothing() -> None:
    for caller, target, new, owners in itertools.product(
        ("member", "viewer", "", "superadmin"), ALL, ALL, (1, 2)
    ):
        assert allowed(caller, target, new, owners) is False


@pytest.mark.unit
def test_a_founder_can_never_leave_the_workspace_without_one() -> None:
    """With one founder left, no change by that founder (or anyone) removes their founder role."""
    for caller, new in itertools.product(("owner", "admin"), ("admin", "member", "viewer")):
        assert allowed(caller, "owner", new, owners=1) is False


@pytest.mark.unit
def test_the_messages_say_what_to_do_next() -> None:
    with pytest.raises(WorkspaceAccessDeniedError) as last:
        authorize_role_change(
            caller_role="owner", target_role="owner", new_role="admin", active_owner_count=1
        )
    assert "another founder" in str(last.value)
    with pytest.raises(WorkspaceAccessDeniedError) as ceiling:
        authorize_role_change(
            caller_role="admin", target_role="member", new_role="admin", active_owner_count=2
        )
    assert "founder is needed" in str(ceiling.value)
    assert roles.ADMIN_REQUIRED


@pytest.mark.unit
@pytest.mark.parametrize(
    ("target", "new", "expected"),
    [
        ("member", "viewer", True),
        ("admin", "viewer", True),
        ("owner", "viewer", True),
        ("viewer", "member", False),
        ("member", "admin", False),
        ("admin", "member", False),
        ("viewer", "viewer", False),
    ],
)
def test_only_losing_the_right_to_contribute_stops_queued_work(target, new, expected) -> None:
    assert loses_contribution(target, new) is expected


@pytest.mark.unit
def test_the_request_schema_accepts_only_the_four_roles_and_nothing_else() -> None:
    for role in ALL:
        assert WorkspaceMemberRoleUpdate(role=role).role == role
    for bad in ("superadmin", "", "Owner", "founder"):
        with pytest.raises(PydanticValidationError):
            WorkspaceMemberRoleUpdate(role=bad)
    with pytest.raises(PydanticValidationError):
        WorkspaceMemberRoleUpdate(role="member", user_id="x")  # type: ignore[call-arg]
