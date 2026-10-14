"""Retire the memories of a superseded / deleted / removed source (contract C6, C7, C9).

The signature is a CONTRACT shared by the publication step (ingestion worker), the
connector removal path and the re-scan deletion rule. The implementation belongs to the
edit-concurrency package; until it lands the function raises ``NotImplementedError`` so
callers fail loudly instead of silently skipping retirement.

Rules the implementation must keep (docs/architecture/AUTOMATIC_INGESTION_CONTRACT.md):

* only MACHINE-ONLY memories are retired (``deleted_at`` + ``deleted_reason``): a single
  untouched row created by the pipeline;
* a lineage containing a human edit is NEVER retired; its current head gets
  ``source_stale_at`` / ``source_stale_reason`` and stays searchable;
* attribution and contribution rows are never altered or hidden;
* lock order (C10): connector row, then ``artifact_heads``, then memory rows in ascending id
  order; the lineage is re-read AFTER the memory locks are taken;
* idempotent: calling twice for the same document changes nothing the second time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

RetireReason = Literal["source_superseded", "source_deleted", "connector_removed"]


@dataclass(frozen=True)
class RetireOutcome:
    retired_ids: list[uuid.UUID] = field(default_factory=list)  # machine-only, now deleted
    flagged_ids: list[uuid.UUID] = field(default_factory=list)  # human-touched, now stale


async def retire_document_memories(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    reason: RetireReason,
    now: datetime | None = None,
) -> RetireOutcome:
    """Retire (machine-only) or flag (human-touched) every memory of ``document_id``."""
    raise NotImplementedError("implemented by the edit-concurrency package (C9/C10)")
