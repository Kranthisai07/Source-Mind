"""One-off seed for a fresh deployment: an Organization and a Workspace.

WHY NOT A USER
--------------
core/dependencies.py::_get_or_create_user looks a user up by `clerk_id` and
creates the row on first authenticated request. `users.id` is an internal
UUID (server_default gen_random_uuid()); the Clerk identifier lives in the
separate `clerk_id` column. Seeding a user here with a guessed clerk_id would
create an orphan row that authentication never matches, and the real row
would be provisioned alongside it.

So ingestion needs only an Organization and a Workspace to exist:
receiver.receive() validates the workspace, and attribution's FK to users is
satisfied by the row auth has already created by that point. Membership is
not checked on the ingestion path — it matters for GET /v1/workspaces — so
it is added afterwards, once the user's real UUID exists.

USAGE
-----
    python seed_production.py                 # create org + workspace
    python seed_production.py --link <clerk_id>   # add membership after login
    python seed_production.py --show          # print current rows

Targets whatever DATABASE_URL settings resolve to. Use the public proxy URL
when running from a laptop; the *.railway.internal form only resolves inside
Railway. Safe to re-run: every statement is idempotent.
"""

from __future__ import annotations

import argparse
import sys
import uuid

import psycopg2

from sourcemind.core.config import get_settings

ORG_SLUG = "sourcemind"
WORKSPACE_SLUG = "production"


def connect():
    settings = get_settings()
    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    host = url.split("@")[1] if "@" in url else url
    needs_ssl = "ssl=require" in url or ".railway.internal" not in url
    print(f"target: {host}  (sslmode={'require' if needs_ssl else 'disabled'})")
    kwargs = {"connect_timeout": 10}
    if needs_ssl:
        kwargs["sslmode"] = "require"
    return psycopg2.connect(url, **kwargs)


def show(cur) -> None:
    for table in ("organizations", "workspaces", "users", "workspace_members"):
        cur.execute(f"SELECT count(*) FROM {table}")
        print(f"  {table:20} {cur.fetchone()[0]}")
    cur.execute(
        "SELECT id, name, slug FROM workspaces ORDER BY created_at LIMIT 5"
    )
    rows = cur.fetchall()
    if rows:
        print("  workspaces:")
        for r in rows:
            print(f"    {r[0]}  {r[1]!r} ({r[2]})")
    cur.execute("SELECT id, clerk_id, email FROM users ORDER BY created_at LIMIT 5")
    rows = cur.fetchall()
    if rows:
        print("  users:")
        for r in rows:
            print(f"    {r[0]}  clerk_id={r[1]}  {r[2]}")


def seed(cur) -> tuple[str, str]:
    """Create the organization and workspace if absent. Returns their ids."""
    cur.execute("SELECT id FROM organizations WHERE slug = %s", (ORG_SLUG,))
    row = cur.fetchone()
    if row:
        org_id = str(row[0])
        print(f"  organization exists: {org_id}")
    else:
        org_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO organizations (id, name, slug, plan) "
            "VALUES (%s, %s, %s, 'free')",
            (org_id, "SourceMind", ORG_SLUG),
        )
        print(f"  organization created: {org_id}")

    cur.execute("SELECT id FROM workspaces WHERE slug = %s", (WORKSPACE_SLUG,))
    row = cur.fetchone()
    if row:
        ws_id = str(row[0])
        print(f"  workspace exists:    {ws_id}")
    else:
        ws_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO workspaces (id, organization_id, name, slug, description) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                ws_id,
                org_id,
                "Production",
                WORKSPACE_SLUG,
                "Primary workspace for the deployed environment.",
            ),
        )
        print(f"  workspace created:   {ws_id}")

    return org_id, ws_id


def link(cur, clerk_id: str, ws_id: str) -> None:
    """Add the authenticated user to the workspace, once auth has created it."""
    cur.execute("SELECT id, email FROM users WHERE clerk_id = %s", (clerk_id,))
    row = cur.fetchone()
    if not row:
        print(f"  no user with clerk_id={clerk_id!r} yet.")
        print("  Make one authenticated request first — the API provisions the")
        print("  row automatically — then re-run with --link.")
        return
    user_id, email = str(row[0]), row[1]
    cur.execute(
        "INSERT INTO workspace_members (id, workspace_id, user_id, role) "
        "SELECT gen_random_uuid(), %s, %s, 'admin' "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM workspace_members WHERE workspace_id = %s AND user_id = %s"
        ")",
        (ws_id, user_id, ws_id, user_id),
    )
    print(f"  linked user {user_id} ({email}) to workspace {ws_id} as admin")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--link", metavar="CLERK_ID", help="add membership for a clerk_id")
    ap.add_argument("--show", action="store_true", help="print current rows only")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()
    try:
        if args.show:
            show(cur)
            return 0

        org_id, ws_id = seed(cur)
        if args.link:
            link(cur, args.link, ws_id)
        conn.commit()

        print()
        print("=" * 58)
        print(f"  ORGANIZATION_ID : {org_id}")
        print(f"  WORKSPACE_ID    : {ws_id}")
        print("=" * 58)
        print()
        show(cur)
        return 0
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
