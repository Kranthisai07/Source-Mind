"""Staging-only seeder: add a few FAKE memories to an existing workspace.

WHY: MCP search needs something to return. Creating memories through normal ingestion needs an
embedding provider (a paid model call), which staging deliberately does not have. These rows have no
embedding, so they are found by KEYWORD search only (use ``mode: keyword``).

SAFETY
------
* Refuses to run unless ``ENVIRONMENT=staging``.
* Run it as the RESTRICTED runtime role (the API's own ``DATABASE_URL``), never as a superuser: the
  inserts then go through the same row-level-security policies as the application.
* Every memory is labelled ``[staging seed]`` and points at a fake ``example`` repository.
* Idempotent: a memory whose content hash already exists in the workspace is skipped.

USAGE (inside the staging API container, or anywhere with the staging runtime ``DATABASE_URL``)
    python scripts/seed_staging.py --clerk-id <user_...> --workspace-slug <slug>
    python scripts/seed_staging.py --clerk-id <user_...> --workspace-id <uuid>

``--clerk-id`` is the Clerk user id (``user_...``, visible in the Clerk dashboard) of a member of
that workspace. The app creates the user row on the person's first authenticated request, so sign
in to the staging app and create the workspace first.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import uuid
from pathlib import Path

# Allow `python scripts/seed_staging.py` from the API root (sys.path[0] is otherwise scripts/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from sourcemind.core.config import Environment, get_settings  # noqa: E402
from sourcemind.core.database import (  # noqa: E402
    get_session_factory,
    init_db,
    set_rls_user_context,
    set_rls_workspace_context,
)
from sourcemind.models.connector import ArtifactLink  # noqa: E402
from sourcemind.models.document import Document  # noqa: E402
from sourcemind.models.memory import Memory  # noqa: E402
from sourcemind.models.user import User  # noqa: E402
from sourcemind.models.workspace import Workspace  # noqa: E402

REPO = "example/staging-demo"
SEEDS = (
    ("issues", 1, "[staging seed] Cache decision: use Redis for session storage."),
    ("issues", 2, "[staging seed] Cache decision: keep Redis keys under a 24 hour TTL."),
    ("pull", 3, "[staging seed] Switch the cache layer to Redis, behind a feature flag."),
    ("issues", 4, "[staging seed] Revisit the Redis eviction policy after the load test."),
    ("pull", 5, "[staging seed] Deploy notes: staging uses a separate database and Redis."),
)


async def main(workspace_id: uuid.UUID | None, workspace_slug: str | None, clerk_id: str) -> int:
    settings = get_settings()
    if settings.environment != Environment.STAGING:
        print("Refusing to run: ENVIRONMENT must be 'staging'.", file=sys.stderr)
        return 2
    await init_db()
    factory = get_session_factory()
    async with factory() as session:
        user_id = (
            await session.execute(select(User.id).where(User.clerk_id == clerk_id))
        ).scalar_one_or_none()
        if user_id is None:
            print("No such user. Sign in to the staging app once first.", file=sys.stderr)
            return 3
        await set_rls_user_context(session, user_id)
        if workspace_id is None:
            # Looked up under the user's own RLS context: only their own workspaces are visible.
            workspace_id = (
                await session.execute(select(Workspace.id).where(Workspace.slug == workspace_slug))
            ).scalars().first()
            if workspace_id is None:
                print("No such workspace for that user. Create it in the staging app first.",
                      file=sys.stderr)
                return 4
        await set_rls_workspace_context(session, workspace_id)
        added = 0
        for kind, number, text_ in SEEDS:
            content_hash = hashlib.sha256(f"staging-seed:{text_}".encode()).hexdigest()
            exists = (
                await session.execute(
                    select(Memory.id).where(
                        Memory.workspace_id == workspace_id, Memory.content_hash == content_hash
                    )
                )
            ).first()
            if exists:
                continue
            document = Document(
                workspace_id=workspace_id,
                submitter_id=user_id,
                source_type="text",
                sha256_hash=hashlib.sha256(f"doc:{content_hash}".encode()).hexdigest(),
                ingestion_status="completed",
            )
            session.add(document)
            await session.flush()
            memory = Memory(
                workspace_id=workspace_id,
                document_id=document.id,
                content=text_,
                content_hash=content_hash,
            )
            session.add(memory)
            await session.flush()
            session.add(
                ArtifactLink(
                    workspace_id=workspace_id,
                    document_id=document.id,
                    memory_id=memory.id,
                    source_tool="github",
                    source_type="issue" if kind == "issues" else "pull_request",
                    source_id=f"{REPO}/{kind}/{number}",
                    source_url=f"https://github.com/{REPO}/{kind}/{number}",
                    source_author="staging-seed",
                    source_revision="rev-1",
                    revision_seq=1,
                )
            )
            await session.flush()
            added += 1
        await session.commit()
    print(f"Seeded {added} memories ({len(SEEDS) - added} already present).")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--workspace-id", type=uuid.UUID)
    group.add_argument("--workspace-slug")
    parser.add_argument("--clerk-id", required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.workspace_id, args.workspace_slug, args.clerk_id)))
