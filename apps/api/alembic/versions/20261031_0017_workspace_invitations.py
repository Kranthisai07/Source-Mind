"""Workspace invitations: invite people who have no account yet (D-065).

An invitation carries a role (member/viewer from an admin or founder, admin/owner from a
founder only) and the SHA-256 of a 256-bit token. The token itself is shown once and never
stored. Whoever holds it, and whose Clerk-verified primary email equals the invited email, can
create their own membership; nobody else can, and nothing about the table is readable to a
non-member except the one live row their token hash names.

How acceptance works without widening the membership table: two small SECURITY DEFINER
predicates in the style of ``sm_can_bootstrap_workspace_owner`` (migration 20260909_0007) are
added to ``workspace_members`` as three extra permissive policies. They admit exactly one
thing: the signed-in user (``app.current_user_id``) creating or reactivating THEIR OWN row,
with the role the invitation grants, while the request holds the matching token hash and
verified email (``app.invitation_token_hash`` / ``app.invitation_email``), the invitation is
unaccepted, unrevoked and unexpired, and the inviter still holds the authority the invited role
requires (so an invitation to admin or founder dies if its founder is demoted or removed).

Revision ID: 20261031_0017
Revises: 20261029_0016
Create Date: 2026-10-31
"""
# ruff: noqa: S608

from __future__ import annotations

import os
import re

from sqlalchemy import text

from alembic import context, op

revision = "20261031_0017"
down_revision = "20261029_0016"
branch_labels = None
depends_on = None

RUNTIME_ROLE_ENV = "SOURCEMIND_RUNTIME_ROLE"
TABLE = "workspace_invitations"

_ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_USER_ID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
_WORKSPACE_ID = "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"
_ADMIN_ROLES = "ARRAY['owner', 'admin']::text[]"
_OWNER_ROLE = "ARRAY['owner']::text[]"

# The token, the verified email and the "still unaccepted, unrevoked, unexpired" state.
_PENDING_BY_HASH = """
    token_hash = NULLIF(current_setting('app.invitation_token_hash', true), '')
    AND accepted_at IS NULL
    AND revoked_at IS NULL
    AND expires_at > now()
"""

# What a live, authorised invitation looks like to the two predicates. The inviter's authority is
# read from the same shadow table the other access functions use, at acceptance time.
_LIVE_INVITATION = """
    invitation.workspace_id = {workspace}
    AND invitation.token_hash = NULLIF(current_setting('app.invitation_token_hash', true), '')
    AND invitation.email = NULLIF(current_setting('app.invitation_email', true), '')
    AND invitation.accepted_at IS NULL
    AND invitation.revoked_at IS NULL
    AND invitation.expires_at > now()
    AND EXISTS (
        SELECT 1
        FROM public.sm_workspace_bootstraps AS bootstrap
        WHERE bootstrap.workspace_id = invitation.workspace_id
          AND bootstrap.deleted_at IS NULL
    )
    AND EXISTS (
        SELECT 1
        FROM public.sm_workspace_access_grants AS inviter
        WHERE inviter.workspace_id = invitation.workspace_id
          AND inviter.user_id = invitation.invited_by
          AND inviter.status = 'active'
          AND inviter.departed_at IS NULL
          AND (
              inviter.role = 'owner'
              OR (inviter.role = 'admin' AND invitation.role IN ('member', 'viewer'))
          )
    )
"""


def required_runtime_role() -> str:
    role = (os.environ.get(RUNTIME_ROLE_ENV) or "").strip()
    if not role:
        raise RuntimeError(
            f"{RUNTIME_ROLE_ENV} must name the restricted runtime database role."
        )
    if not _ROLE_NAME.fullmatch(role):
        raise RuntimeError(f"{RUNTIME_ROLE_ENV} must be a plain PostgreSQL role identifier.")
    return role


def _assert_role_exists(role: str) -> None:
    if context.is_offline_mode():
        return
    exists = (
        op.get_bind()
        .execute(text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role})
        .scalar()
    )
    if not exists:
        raise RuntimeError(f"{RUNTIME_ROLE_ENV} names missing role {role!r}.")


def _table_owner(table: str) -> str | None:
    if context.is_offline_mode():
        return None
    owner = (
        op.get_bind()
        .execute(
            text(
                "SELECT tableowner FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = :table"
            ),
            {"table": table},
        )
        .scalar()
    )
    return str(owner) if owner is not None else None


def upgrade() -> None:
    role = required_runtime_role()
    _assert_role_exists(role)

    op.execute(
        f"""
        CREATE TABLE {TABLE} (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            email text NOT NULL,
            role text NOT NULL,
            token_hash char(64) NOT NULL UNIQUE,
            invited_by uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            accepted_at timestamptz,
            accepted_by uuid REFERENCES users(id) ON DELETE SET NULL,
            revoked_at timestamptz,
            revoked_by uuid REFERENCES users(id) ON DELETE SET NULL,
            CONSTRAINT ck_workspace_invitations_role
                CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
            CONSTRAINT ck_workspace_invitations_email
                CHECK (email = lower(email) AND email ~ '^[ -~]+@[ -~]+$'),
            CONSTRAINT ck_workspace_invitations_hash CHECK (token_hash ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_workspace_invitations_expiry CHECK (expires_at > created_at),
            CONSTRAINT ck_workspace_invitations_terminal
                CHECK (NOT (accepted_at IS NOT NULL AND revoked_at IS NOT NULL))
        )
        """
    )
    # One live invitation per person per workspace: a new one revokes the old first.
    op.execute(
        f"CREATE UNIQUE INDEX uq_workspace_invitations_pending ON {TABLE} (workspace_id, email) "
        "WHERE accepted_at IS NULL AND revoked_at IS NULL"
    )
    op.execute(f"CREATE INDEX ix_workspace_invitations_workspace ON {TABLE} (workspace_id)")

    # An accepted or revoked invitation is finished: nothing about it may change afterwards.
    op.execute(
        """
        CREATE FUNCTION protect_workspace_invitation_terminal() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF OLD.accepted_at IS NOT NULL OR OLD.revoked_at IS NOT NULL THEN
                RAISE EXCEPTION 'a finished workspace invitation cannot change'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION protect_workspace_invitation_terminal() FROM PUBLIC")
    op.execute(
        f"CREATE TRIGGER workspace_invitations_terminal_immutable BEFORE UPDATE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION protect_workspace_invitation_terminal()"
    )

    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")

    op.execute(
        f"""
        CREATE POLICY workspace_invitations_select ON {TABLE} FOR SELECT
        USING (sm_has_active_workspace_access(workspace_id, {_ADMIN_ROLES}))
        """
    )
    # The one live row a token hash names, readable by whoever presents the hash.
    op.execute(
        f"CREATE POLICY workspace_invitations_lookup ON {TABLE} FOR SELECT "
        f"USING ({_PENDING_BY_HASH})"
    )
    # PostgreSQL also requires an updated row to stay visible under the SELECT policies. Once an
    # invitation is accepted it is no longer "pending", so without this the accepting user could
    # not write the row they just consumed. It shows a person only the invitations they accepted.
    op.execute(
        f"CREATE POLICY workspace_invitations_accepted_by_me ON {TABLE} FOR SELECT "
        f"USING (accepted_by = {_USER_ID})"
    )
    op.execute(
        f"""
        CREATE POLICY workspace_invitations_insert ON {TABLE} FOR INSERT WITH CHECK (
            invited_by = {_USER_ID}
            AND accepted_at IS NULL
            AND revoked_at IS NULL
            AND sm_has_active_workspace_access(workspace_id, {_ADMIN_ROLES})
            AND (
                role IN ('member', 'viewer')
                OR sm_has_active_workspace_access(workspace_id, {_OWNER_ROLE})
            )
        )
        """
    )
    # Revoking: admins revoke teammate/viewer invitations, only founders revoke admin/founder ones.
    op.execute(
        f"""
        CREATE POLICY workspace_invitations_revoke ON {TABLE} FOR UPDATE
        USING (
            accepted_at IS NULL AND revoked_at IS NULL
            AND sm_has_active_workspace_access(workspace_id, {_ADMIN_ROLES})
            AND (
                role IN ('member', 'viewer')
                OR sm_has_active_workspace_access(workspace_id, {_OWNER_ROLE})
            )
        )
        WITH CHECK (
            accepted_at IS NULL
            AND revoked_at IS NOT NULL
            AND revoked_by = {_USER_ID}
            AND sm_has_active_workspace_access(workspace_id, {_ADMIN_ROLES})
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY workspace_invitations_accept ON {TABLE} FOR UPDATE
        USING ({_PENDING_BY_HASH})
        WITH CHECK (
            accepted_at IS NOT NULL
            AND revoked_at IS NULL
            AND accepted_by = {_USER_ID}
        )
        """
    )

    # ── The two predicates the membership policies use ───────────────────────────────────
    op.execute(
        f"""
        CREATE FUNCTION sm_invitation_matches_workspace(target_workspace_id uuid)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT EXISTS (
                SELECT 1
                FROM public.workspace_invitations AS invitation
                WHERE {_LIVE_INVITATION.format(workspace="target_workspace_id")}
            )
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION sm_invitation_grants_membership(
            target_workspace_id uuid,
            target_user_id uuid,
            target_role text,
            target_status text,
            target_departed_at timestamptz
        ) RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT target_user_id = {_USER_ID}
               AND target_status = 'active'
               AND target_departed_at IS NULL
               AND EXISTS (
                    SELECT 1
                    FROM public.workspace_invitations AS invitation
                    WHERE {_LIVE_INVITATION.format(workspace="target_workspace_id")}
                      AND invitation.role = target_role
               )
        $$
        """
    )
    for signature in (
        "sm_invitation_matches_workspace(uuid)",
        "sm_invitation_grants_membership(uuid, uuid, text, text, timestamptz)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        if not context.is_offline_mode():
            op.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{role}"')

    # Three additive, permissive policies. Each admits only the signed-in user's own row and
    # only while a live, authorised invitation for exactly that role is being presented.
    op.execute(
        f"""
        CREATE POLICY sm_workspace_members_invite_select ON workspace_members FOR SELECT
        USING (
            user_id = {_USER_ID}
            AND sm_invitation_matches_workspace(workspace_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY sm_workspace_members_invite_insert ON workspace_members FOR INSERT
        WITH CHECK (
            ({_WORKSPACE_ID} IS NULL OR workspace_id = {_WORKSPACE_ID})
            AND sm_invitation_grants_membership(
                workspace_id, user_id, role, status, departed_at
            )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY sm_workspace_members_invite_update ON workspace_members FOR UPDATE
        USING (
            ({_WORKSPACE_ID} IS NULL OR workspace_id = {_WORKSPACE_ID})
            AND user_id = {_USER_ID}
            AND status = 'departed'  -- never a member mid-handoff ('departing')
            AND sm_invitation_matches_workspace(workspace_id)
        )
        WITH CHECK (
            ({_WORKSPACE_ID} IS NULL OR workspace_id = {_WORKSPACE_ID})
            AND sm_invitation_grants_membership(
                workspace_id, user_id, role, status, departed_at
            )
        )
        """
    )

    # Runtime privileges: read, create, and set only the four state columns. No DELETE.
    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM PUBLIC")
    if _table_owner(TABLE) != role:
        op.execute(f'REVOKE ALL ON TABLE {TABLE} FROM "{role}"')
        op.execute(f'GRANT SELECT, INSERT ON TABLE {TABLE} TO "{role}"')
        op.execute(
            f"GRANT UPDATE (revoked_at, revoked_by, accepted_at, accepted_by) "
            f'ON TABLE {TABLE} TO "{role}"'
        )


def downgrade() -> None:
    # Remove the three policies first: they call the two functions.
    for policy in (
        "sm_workspace_members_invite_update",
        "sm_workspace_members_invite_insert",
        "sm_workspace_members_invite_select",
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON workspace_members")
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "sm_invitation_grants_membership(uuid, uuid, text, text, timestamptz)"
    )
    op.execute("DROP FUNCTION IF EXISTS sm_invitation_matches_workspace(uuid)")
    op.execute(f"DROP TRIGGER IF EXISTS workspace_invitations_terminal_immutable ON {TABLE}")
    op.execute("DROP FUNCTION IF EXISTS protect_workspace_invitation_terminal()")
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
