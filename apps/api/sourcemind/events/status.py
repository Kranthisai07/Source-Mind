"""Is the graph caught up and safe to trust? A read-only report per workspace.

    python -m sourcemind.events.status [--workspace-id <uuid>] [--limit N] [--json]

For each workspace that has published graph events it shows what the API's freshness check sees:
the relay's published cursor (PostgreSQL), the projector's applied watermark and dead-letter
gap (Neo4j), and how many outbox rows the relay has not published yet. Exit status is 0 only if
every reported workspace is ``trusted``, so a rollout step can assert on it.

It needs ``GRAPH_RELAY_DATABASE_URL`` (the relay role: SELECT on the outbox and the cursor, which
is all it does) and the Neo4j settings. It writes nothing anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from sourcemind.core.config import get_settings
from sourcemind.core.graph import close_graph, init_graph, run_read
from sourcemind.services.graph.reads import WORKSPACE_WATERMARK

TRUSTED = "trusted"
GAP = "gap: run the backfill"
BEHIND = "behind: projector catching up"
RELAY_BEHIND = "relay behind"
EMPTY = "not in the graph yet"

CURSORS = (
    "SELECT workspace_id::text AS workspace_id, published_seq FROM graph_publication_cursor "
    "{where} ORDER BY workspace_id LIMIT :n"
)
PENDING = (
    "SELECT workspace_id::text AS workspace_id, count(*) AS pending, "
    "EXTRACT(EPOCH FROM now() - min(created_at)) AS oldest_seconds "
    "FROM graph_outbox WHERE published_at IS NULL {where} GROUP BY workspace_id"
)


@dataclass(frozen=True)
class WorkspaceStatus:
    workspace_id: str
    published_seq: int | None
    applied_seq: int | None
    gap_seq: int | None
    pending: int
    oldest_pending_seconds: float
    state: str


def classify(
    published: int | None, applied: int | None, gap: int | None, pending: int
) -> str:
    """The same rule the API applies in ``_graph_fresh``, plus the relay's own backlog."""
    if gap is not None:
        return GAP
    if applied is None:
        return EMPTY
    if published is not None and applied < published:
        return BEHIND
    if pending:
        return RELAY_BEHIND
    return TRUSTED


async def collect(
    conn: AsyncConnection, *, workspace_id: uuid.UUID | None = None, limit: int = 50
) -> list[WorkspaceStatus]:
    where = "WHERE workspace_id = CAST(:ws AS uuid)" if workspace_id else ""
    params: dict[str, Any] = {"n": limit}
    if workspace_id:
        params["ws"] = str(workspace_id)
    cursors = (await conn.execute(text(CURSORS.format(where=where)), params)).mappings().all()
    pending_where = "AND workspace_id = CAST(:ws AS uuid)" if workspace_id else ""
    pending = {
        row["workspace_id"]: row
        for row in (
            await conn.execute(
                text(PENDING.format(where=pending_where)),
                {"ws": str(workspace_id)} if workspace_id else {},
            )
        )
        .mappings()
        .all()
    }
    ids = [row["workspace_id"] for row in cursors]
    # A workspace with events waiting but nothing published yet has no cursor row. It is exactly
    # what a rollout needs to see (the relay is not keeping up), so it is listed too.
    ids += [ws for ws in pending if ws not in ids]

    published = {row["workspace_id"]: int(row["published_seq"]) for row in cursors}
    report: list[WorkspaceStatus] = []
    for ws in ids[:limit]:
        rows = await run_read(WORKSPACE_WATERMARK, ws)
        applied = rows[0]["seq"] if rows else None
        gap = rows[0].get("gap_seq") if rows else None
        waiting = pending.get(ws)
        count = int(waiting["pending"]) if waiting else 0
        report.append(
            WorkspaceStatus(
                workspace_id=ws,
                published_seq=published.get(ws),
                applied_seq=None if applied is None else int(applied),
                gap_seq=None if gap is None else int(gap),
                pending=count,
                oldest_pending_seconds=(
                    round(float(waiting["oldest_seconds"]), 1) if waiting else 0.0
                ),
                state=classify(
                    published.get(ws),
                    None if applied is None else int(applied),
                    None if gap is None else int(gap),
                    count,
                ),
            )
        )
    return report


def render(report: Sequence[WorkspaceStatus]) -> str:
    if not report:
        return "No workspace has graph events yet."
    head = ("workspace", "published", "applied", "gap", "pending", "oldest_s", "state")
    rows = [
        (
            r.workspace_id,
            "-" if r.published_seq is None else str(r.published_seq),
            "-" if r.applied_seq is None else str(r.applied_seq),
            "-" if r.gap_seq is None else str(r.gap_seq),
            str(r.pending),
            f"{r.oldest_pending_seconds:g}",
            r.state,
        )
        for r in report
    ]
    widths = [max(len(head[i]), *(len(row[i]) for row in rows)) for i in range(len(head))]
    lines = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in [head, *rows]
    ]
    return "\n".join(lines)


async def run(workspace_id: uuid.UUID | None, limit: int, as_json: bool) -> int:
    settings = get_settings()
    if not settings.graph_relay_database_url:
        print("GRAPH_RELAY_DATABASE_URL is not set.", file=sys.stderr)
        return 2
    engine = create_async_engine(settings.graph_relay_database_url, pool_size=1, max_overflow=0)
    await init_graph()
    try:
        async with engine.connect() as conn:
            report = await collect(conn, workspace_id=workspace_id, limit=limit)
    finally:
        await close_graph()
        await engine.dispose()
    print(json.dumps([asdict(r) for r in report], indent=1) if as_json else render(report))
    return 0 if all(r.state == TRUSTED for r in report) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace-id", type=uuid.UUID, default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.workspace_id, args.limit, args.json))


if __name__ == "__main__":
    raise SystemExit(main())
