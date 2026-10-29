"""Rebuild or repair the graph for one workspace by re-emitting every memory's state.

    python -m sourcemind.events.backfill --workspace-id <uuid> [--as-user <uuid>]

The graph is a projection of PostgreSQL, so the repair for a lost or dead-lettered event,
for a graph that was off while data changed, or for drift is to emit current state again:
events are full state ordered by ``seq``, so the projector applies them idempotently over
whatever is there. Nothing is ever edited in Neo4j by hand.

It reads PostgreSQL through the normal row-level security, as an active member of the
workspace (``--as-user``, or discovered if the policy lets it), so it needs no bypass role.
Soft-deleted memories are included so their nodes are flagged deleted.

The run is bracketed by two marker events (started, complete). Completing a backfill is what
tells the projector that every event it had to dead-letter before the run began has been
superseded, so the graph may be trusted again (D-062).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.database import (
    close_db,
    get_session_factory,
    init_db,
    set_rls_user_context,
    set_rls_workspace_context,
)
from sourcemind.events.envelope import EVENT_BACKFILL_COMPLETE, EVENT_BACKFILL_STARTED
from sourcemind.services.graph.outbox import emit_memory_states, emit_workspace_marker

log = structlog.get_logger(__name__)

BATCH_SIZE = 200


async def backfill_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, *, batch_size: int = BATCH_SIZE
) -> int:
    """Emit state for every memory of the workspace, in id order, one commit per batch."""
    total = 0
    last: str | None = None
    while True:
        if last is None:
            result = await session.execute(
                text(
                    "SELECT id::text AS id FROM memories "
                    "WHERE workspace_id = CAST(:ws AS uuid) ORDER BY id LIMIT :n"
                ),
                {"ws": str(workspace_id), "n": batch_size},
            )
        else:
            result = await session.execute(
                text(
                    "SELECT id::text AS id FROM memories "
                    "WHERE workspace_id = CAST(:ws AS uuid) AND id > CAST(:last AS uuid) "
                    "ORDER BY id LIMIT :n"
                ),
                {"ws": str(workspace_id), "last": last, "n": batch_size},
            )
        ids = [row["id"] for row in result.mappings().all()]
        if not ids:
            return total
        total += await emit_memory_states(session, ids, reason="backfill", force=True)
        await session.commit()
        last = ids[-1]
        log.info("graph.backfill_progress", workspace_id=str(workspace_id), emitted=total)


async def find_owner(session: AsyncSession, workspace_id: uuid.UUID) -> uuid.UUID | None:
    """An active owner of the workspace, if row-level security lets this connection see one."""
    row = (
        await session.execute(
            text(
                "SELECT user_id::text AS user_id FROM workspace_members "
                "WHERE workspace_id = CAST(:ws AS uuid) AND role = 'owner' "
                "AND status = 'active' AND departed_at IS NULL ORDER BY user_id LIMIT 1"
            ),
            {"ws": str(workspace_id)},
        )
    ).mappings().first()
    return uuid.UUID(row["user_id"]) if row else None


async def run(workspace_id: uuid.UUID, as_user: uuid.UUID | None) -> int:
    await init_db()
    try:
        async with get_session_factory()() as session:
            await set_rls_workspace_context(session, workspace_id)
            user = as_user or await find_owner(session, workspace_id)
            if user is None:
                print(
                    "Could not find an active owner for this workspace under row-level "
                    "security. Pass --as-user <uuid of an active member>.",
                    file=sys.stderr,
                )
                return 2
            await set_rls_user_context(session, user)
            # Bracket the re-emit. The projector clears a workspace's dead-letter gap only on the
            # completion marker, and only gaps older than `started_seq`; if this run dies midway
            # there is no completion marker and the workspace stays on PostgreSQL.
            started_seq = await emit_workspace_marker(
                session, workspace_id, EVENT_BACKFILL_STARTED, {}
            )
            await session.commit()
            total = await backfill_workspace(session, workspace_id)
            await emit_workspace_marker(
                session,
                workspace_id,
                EVENT_BACKFILL_COMPLETE,
                {"started_seq": started_seq, "emitted": total},
            )
            await session.commit()
        print(f"Emitted {total} memory states for workspace {workspace_id}.")
        return 0
    finally:
        await close_db()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace-id", type=uuid.UUID, required=True)
    parser.add_argument("--as-user", type=uuid.UUID, default=None)
    args = parser.parse_args(argv)
    return asyncio.run(run(args.workspace_id, args.as_user))


if __name__ == "__main__":
    raise SystemExit(main())
