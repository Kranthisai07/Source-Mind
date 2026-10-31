"""Invitation tokens, who may invite as what, and the request/response shapes (D-065)."""

from __future__ import annotations

import itertools
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError

from sourcemind.core.exceptions import WorkspaceAccessDeniedError
from sourcemind.schemas.invitation import (
    TOKEN_PATTERN,
    InvitationAccept,
    InvitationCreate,
    InvitationCreated,
    InvitationResponse,
)
from sourcemind.services.workspace import invitations as inv

ALL = ("owner", "admin", "member", "viewer")


@pytest.mark.unit
def test_a_token_is_long_random_prefixed_and_matches_the_accept_schema() -> None:
    token, digest = inv.generate_token()
    assert token.startswith("smi_") and len(token) == 47
    assert re.fullmatch(TOKEN_PATTERN, token)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == inv.hash_token(token)
    assert token not in digest and digest not in token


@pytest.mark.unit
def test_tokens_do_not_repeat_and_the_hash_depends_on_every_character() -> None:
    tokens = {inv.generate_token()[0] for _ in range(500)}
    assert len(tokens) == 500
    token = next(iter(tokens))
    flipped = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert inv.hash_token(token) != inv.hash_token(flipped)


@pytest.mark.unit
def test_the_accept_schema_rejects_anything_that_is_not_a_token() -> None:
    token, _ = inv.generate_token()
    assert InvitationAccept(token=token).token == token
    padded = " " + token + " x"
    for bad in ("", "smi_", "smi_short", token[:-1], token + "x", "smk_" + token[4:], padded):
        with pytest.raises(PydanticValidationError):
            InvitationAccept(token=bad)
    with pytest.raises(PydanticValidationError):
        InvitationAccept(token=token, role="owner")  # type: ignore[call-arg]


@pytest.mark.unit
def test_founders_invite_as_anything_and_admins_only_as_teammate_or_viewer() -> None:
    for role in ALL:
        inv.authorize_invitation(caller_role="owner", invite_role=role)
    for role in ("member", "viewer"):
        inv.authorize_invitation(caller_role="admin", invite_role=role)
    for role in ("owner", "admin"):
        with pytest.raises(WorkspaceAccessDeniedError) as denied:
            inv.authorize_invitation(caller_role="admin", invite_role=role)
        assert "founder" in str(denied.value)


@pytest.mark.unit
def test_nobody_below_admin_can_invite_or_revoke() -> None:
    for caller, role in itertools.product(("member", "viewer", "", "superadmin"), ALL):
        with pytest.raises(WorkspaceAccessDeniedError):
            inv.authorize_invitation(caller_role=caller, invite_role=role)


@pytest.mark.unit
def test_an_admin_can_never_create_an_invitation_that_confers_admin_or_founder() -> None:
    """The escalation invariant over every combination, mirroring the role-change one."""
    for caller, role in itertools.product(ALL, ALL):
        try:
            inv.authorize_invitation(caller_role=caller, invite_role=role)
        except WorkspaceAccessDeniedError:
            continue
        if caller == "admin":
            assert role in ("member", "viewer")
        assert caller in ("owner", "admin")


@pytest.mark.unit
def test_the_create_schema_lowercases_refuses_non_ascii_and_unknown_fields() -> None:
    assert InvitationCreate(email="Priya.Nair@Example.COM").email == "priya.nair@example.com"
    assert InvitationCreate(email="a@b.co").role == "member"
    for role in ALL:
        assert InvitationCreate(email="a@b.co", role=role).role == role
    for bad in ("superadmin", "", "founder", "Owner"):
        with pytest.raises(PydanticValidationError):
            InvitationCreate(email="a@b.co", role=bad)
    with pytest.raises(PydanticValidationError):
        InvitationCreate(email="josé@example.com")
    with pytest.raises(PydanticValidationError):
        InvitationCreate(email="a@b.co", workspace_id=str(uuid.uuid4()))  # type: ignore[call-arg]
    with pytest.raises(PydanticValidationError):
        InvitationCreate(email="not-an-email")


@pytest.mark.unit
def test_only_the_creation_response_carries_a_token() -> None:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "email": "a@b.co",
        "role": "member",
        "created_at": now,
        "expires_at": now + timedelta(days=7),
        "invited_by": uuid.uuid4(),
    }
    assert "token" not in InvitationResponse(**base).model_dump()
    created = InvitationCreated(**base, token="smi_" + "x" * 43, accept_path="/invite/smi_x")
    assert created.model_dump()["token"].startswith("smi_")
    assert "token_hash" not in created.model_dump()


@pytest.mark.unit
def test_an_invitation_lasts_seven_days_and_a_workspace_holds_a_bounded_number() -> None:
    assert inv.INVITATION_TTL == timedelta(days=7)
    assert 1 <= inv.MAX_PENDING_PER_WORKSPACE <= 200
