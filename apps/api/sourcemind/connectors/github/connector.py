"""GitHubConnector: the fenced sync orchestrator for one connector.

It owns lease acquisition and fence checks, the discovery positions (cursors), the
work items for pre-commit handoff failures, the recovery sweep and the deletion rule.
It consumes ``ScopeRun`` objects and a ``RepoAccessVerifier`` (contracts.py) and never
calls GitHub content endpoints itself (docs/architecture/AUTOMATIC_INGESTION_CONTRACT.md).

Run outline (C3, C4, C5, C7, C11):
  acquire lease -> reap stale logs -> start log -> recovery sweep (no GitHub) ->
  authorization checks -> for each scope (least recently progressed first, with a
  redistributed per-scope quota): verify access, iterate the ScopeRun, hand off each
  item with ``pre_commit`` (fence renewal + resume key in the handoff transaction) ->
  deletion bookkeeping -> finish (schedule + lease release).

The user that owns the run is only the Document submitter (the worker's authorization
principal). It is never credited as an author (D-021).
"""

from __future__ import annotations

import hashlib
import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.connectors.github.client import GitHubClient
from sourcemind.connectors.github.contracts import (
    SCOPES,
    AccessVerdict,
    DiscoveryLimits,
    GitHubFetchError,
    GoneError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    ScopeState,
    TransientError,
    VerifierResult,
)
from sourcemind.connectors.github.mapper import ConnectorDocument
from sourcemind.core.config import get_settings
from sourcemind.models.connector import ConnectorSyncLog
from sourcemind.models.document import Document, DocumentSourceType, IngestionStatus
from sourcemind.services.attribution.github_links import (
    NO_GITHUB_ACCOUNT,
    resolve_github_link,
    valid_github_user_id,
)
from sourcemind.services.connectors import sync_state as ss
from sourcemind.services.connectors.sync_state import LeaseLostError, LeaseToken
from sourcemind.services.ingestion import receiver as _receiver
from sourcemind.services.ingestion.receiver import receive, recover_pending_dispatch
from sourcemind.services.memory.retire import retire_document_memories

log = structlog.get_logger(__name__)

# Optional until the receiver package lands on this branch (contract "Recovery").
requeue_transient_failure = getattr(_receiver, "requeue_transient_failure", None)

# Document.pipeline_data["dispatch_state"] values for which task publication is
# not confirmed (receiver.py's dispatch state machine).
_UNCONFIRMED_DISPATCH_STATES = frozenset({"orphaned", "publishing", "uncertain"})
_RETRYABLE_ERROR_CODES = ("transient", "incomplete_extraction")
_SWEEP_BATCH = 200

PreCommit = Callable[[AsyncSession], Awaitable[None]]


@dataclass
class SyncOutcome:
    status: str  # completed | continuing | failed | skipped_locked | lease_lost
    log_id: uuid.UUID | None = None
    error_code: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    filtered: dict[str, int] = field(default_factory=dict)
    sweep: dict[str, int] = field(default_factory=dict)


@dataclass
class _Scope:
    repo: str  # "owner/name"
    scope: str
    state: ScopeState
    last_progress_at: datetime | None
    stopped: bool = False  # not served again in this run


class _RunEndError(Exception):
    """Internal: stop the whole run (e.g. rate limited)."""

    def __init__(self, code: str, retry_at: datetime | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_at = retry_at


class GitHubConnector:
    """Orchestrates syncs for a single GitHub connector config.

    Args:
        config: The :class:`ConnectorConfig` row (only ``id`` is required up front;
            the live row is re-read after the lease is taken).
        auth: GitHub App auth for THIS connector's installation.
        session: Async session already carrying the RLS user context. Must be created
            with ``expire_on_commit=False``.
        workspace_id: Workspace that owns the connector.
        user_id: Document submitter / authorization principal. Never an author.
        client / discover / verifier / retire / settings / clock: injectable.
    """

    def __init__(
        self,
        config: Any,
        auth: Any,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        client: Any | None = None,
        discover: Callable[..., Any] | None = None,
        verifier: Any | None = None,
        retire: Callable[..., Awaitable[Any]] | None = None,
        settings: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._auth = auth
        self._client = client if client is not None else GitHubClient(auth)
        self._session = session
        self._workspace_id = workspace_id
        self._user_id = user_id
        self._discover = discover
        self._verifier = verifier
        self._retire = retire
        self._settings = settings if settings is not None else get_settings()
        self._now = clock or (lambda: datetime.now(UTC))
        self._token: LeaseToken | None = None
        self._live: SimpleNamespace | None = None
        self._log: ConnectorSyncLog | None = None
        self._last_result: dict[str, Any] = {}
        self._work_items: dict[tuple[str, str, str], tuple[int, str]] = {}
        self._pre_commit_ran = False

    # ── lease / write helpers ────────────────────────────────────────────────

    @property
    def _lease_seconds(self) -> int:
        return int(self._settings.connector_lease_seconds)

    async def _fence(self) -> None:
        assert self._token is not None
        await ss.renew_lease(self._session, self._token, lease_seconds=self._lease_seconds)

    async def _write(self, fn: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
        """One fenced write transaction: renew the fence first, then write, then commit."""
        await self._fence()
        try:
            out = await fn(self._session)
            await self._session.commit()
        except LeaseLostError:
            raise
        except BaseException:
            await self._session.rollback()
            raise
        return out

    @staticmethod
    async def _noop(session: AsyncSession) -> None:
        return None

    # ── public API ───────────────────────────────────────────────────────────

    async def sync(self, sync_type: str = "incremental", trigger: str = "manual") -> SyncOutcome:
        return await self.run(trigger=trigger, sync_type=sync_type)

    async def run(
        self, *, trigger: str = "scheduled", sync_type: str = "incremental"
    ) -> SyncOutcome:
        token = await ss.acquire_lease(
            self._session, self._config.id, lease_seconds=self._lease_seconds
        )
        if token is None:
            return SyncOutcome(status="skipped_locked")
        self._token = token
        outcome = SyncOutcome(status="failed")
        try:
            return await self._run_locked(trigger, sync_type, outcome)
        except LeaseLostError:
            log.warning("connector_sync_lease_lost", connector_id=str(self._config.id))
            outcome.status = "lease_lost"
            return outcome
        except TransientError:
            await self._fail_run(outcome, "transient", timedelta(seconds=60))
            raise
        except Exception as exc:
            log.error(
                "github_connector_sync_failed",
                connector_id=str(self._config.id),
                error_type=type(exc).__name__,
            )
            await self._fail_run(outcome, "sync_error", None)
            return outcome

    # ── run body ─────────────────────────────────────────────────────────────

    async def _run_locked(self, trigger: str, sync_type: str, outcome: SyncOutcome) -> SyncOutcome:
        cfg = await self._load_live_config()
        self._live = cfg

        sync_log = ConnectorSyncLog(
            connector_id=self._config.id,
            sync_type=sync_type,
            trigger=trigger if trigger in ("manual", "scheduled", "recovery") else "manual",
            status="running",
            artifacts_found=0,
            artifacts_new=0,
            artifacts_skipped=0,
            started_at=self._now(),
        )
        self._log = sync_log

        async def _start(session: AsyncSession) -> None:
            await ss.mark_stale_runs_interrupted(session, self._config.id)
            session.add(sync_log)
            await session.flush()

        await self._write(_start)
        outcome.log_id = sync_log.id

        counts = {"found": 0, "new": 0, "updated": 0, "skipped": 0, "failed": 0, "quarantined": 0}
        filtered: dict[str, int] = {}
        outcome.counts, outcome.filtered = counts, filtered

        # Recovery sweep: independent of discovery, never calls GitHub.
        outcome.sweep = await self.recovery_sweep()

        error_code: str | None = None
        more = False
        retry_at: datetime | None = None
        connector_status: str | None = None

        auth_error = await self._check_authorization(cfg)
        if auth_error:
            error_code = auth_error
            connector_status = "error" if auth_error == "owner_inactive" else None
        elif trigger != "recovery":
            try:
                more, error_code = await self._serve_scopes(cfg, counts, filtered)
            except _RunEndError as end:
                error_code, retry_at = end.code, end.retry_at

        if error_code is not None:
            status = "failed"
        else:
            status = "continuing" if more else "completed"

        now = self._now()
        if status == "continuing":
            next_sync = now
        elif retry_at is not None:
            next_sync = retry_at
        else:
            next_sync = now + timedelta(minutes=int(cfg.sync_interval_minutes))

        sync_log.status = status
        sync_log.error_code = error_code
        sync_log.error_message = "Connector sync failed." if status == "failed" else None
        sync_log.artifacts_found = counts["found"]
        sync_log.artifacts_new = counts["new"]
        sync_log.artifacts_updated = counts["updated"]
        sync_log.artifacts_skipped = counts["skipped"]
        sync_log.artifacts_failed = counts["failed"]
        sync_log.artifacts_filtered = dict(filtered)
        sync_log.completed_at = now

        token = self._token
        assert token is not None
        try:
            # finish_run is itself the fenced statement (and releases the lease); the
            # log row flush rides in the same transaction and rolls back with it.
            self._session.add(sync_log)
            await self._session.flush()
            await ss.finish_run(
                self._session,
                token,
                next_sync_at=next_sync,
                last_sync_at=now,
                success=status != "failed",
                error_code=error_code,
                connector_status=connector_status,
            )
            await self._session.commit()
        except LeaseLostError:
            raise
        except BaseException:
            await self._session.rollback()
            raise
        outcome.status, outcome.error_code = status, error_code
        return outcome

    async def _fail_run(self, outcome: SyncOutcome, code: str, retry_in: timedelta | None) -> None:
        """Best-effort failed-run bookkeeping after an unexpected error."""
        try:
            await self._session.rollback()
            now = self._now()
            interval = (
                retry_in
                if retry_in is not None
                else timedelta(
                    minutes=int(getattr(self._live, "sync_interval_minutes", None) or 15)
                )
            )
            if outcome.log_id is not None:
                # By id and SQL: the rollback above expired the ORM object, and reading an
                # expired attribute in async code raises (which used to leave the run
                # "running" forever). Fenced together with finish_run below.
                await self._session.execute(
                    text(
                        "UPDATE connector_sync_logs SET status = 'failed', error_code = :c, "
                        "error_message = 'Connector sync failed.', completed_at = :n "
                        "WHERE id = :i AND status = 'running'"
                    ),
                    {"c": code, "n": now, "i": outcome.log_id},
                )
            assert self._token is not None
            await ss.finish_run(
                self._session,
                self._token,
                next_sync_at=now + interval,
                last_sync_at=now,
                success=False,
                error_code=code,
            )
            await self._session.commit()
            outcome.status, outcome.error_code = "failed", code
        except LeaseLostError:
            outcome.status = "lease_lost"
        except Exception:
            await self._session.rollback()

    async def _load_live_config(self) -> SimpleNamespace:
        row = (
            (
                await self._session.execute(
                    text(
                        "SELECT status, created_by, installation_id, authorized_github_user_id, "
                        "generation, sync_interval_minutes, config FROM connector_configs "
                        "WHERE id = :id"
                    ),
                    {"id": self._config.id},
                )
            )
            .mappings()
            .first()
        )
        await self._session.rollback()
        if row is None:
            raise LeaseLostError("connector row not visible")
        return SimpleNamespace(**dict(row))

    # ── authorization (C11) ──────────────────────────────────────────────────

    async def _check_authorization(self, cfg: SimpleNamespace) -> str | None:
        """Connector-level checks. Returns an error code or None."""
        active_admin = None
        if cfg.created_by is not None:
            active_admin = (
                await self._session.execute(
                    text(
                        "SELECT 1 FROM workspace_members WHERE workspace_id = :ws "
                        "AND user_id = :u AND status = 'active' AND departed_at IS NULL "
                        "AND role IN ('owner', 'admin')"
                    ),
                    {"ws": self._workspace_id, "u": cfg.created_by},
                )
            ).first()
        await self._session.rollback()  # read-only probe; release the transaction
        if active_admin is None:
            return "owner_inactive"

        auth_installation = getattr(self._auth, "_installation_id", None) or getattr(
            self._auth, "installation_id", None
        )
        if (
            cfg.installation_id is not None
            and auth_installation is not None
            and str(auth_installation) != str(cfg.installation_id)
        ):
            return "installation_mismatch"
        return None

    async def _installation_repos(self) -> set[str] | None:
        """Repos the installation lists now. ``None`` = cannot tell (fail closed)."""
        lister = getattr(self._client, "list_installation_repositories", None)
        if lister is None:
            return None
        result = lister()
        if hasattr(result, "__aiter__"):  # the real client returns a Paginator of repo dicts
            names: set[str] = set()
            async for item in result:
                full = item.get("full_name") if isinstance(item, dict) else item
                if full:
                    names.add(str(full).lower())
            # A page-capped listing cannot say a repo is absent: fail closed.
            return names if getattr(result, "exhausted", True) is not False else None
        return {str(n).lower() for n in await result}

    # ── scopes ───────────────────────────────────────────────────────────────

    async def _serve_scopes(
        self, cfg: SimpleNamespace, counts: dict[str, int], filtered: dict[str, int]
    ) -> tuple[bool, str | None]:
        """Serve all scopes under the per-run handoff budget. Returns (more, error_code)."""
        s = self._settings
        repos = [r for r in (cfg.config or {}).get("repos", []) if r.count("/") == 1]
        repos = repos[: int(s.connector_max_repos)]
        cursors = await ss.load_cursors(self._session, self._config.id)
        self._work_items = await ss.load_work_items(self._session, self._config.id)
        await self._session.rollback()

        error_code: str | None = None
        try:
            installed = await self._installation_repos()
        except RateLimitedError as exc:
            raise _RunEndError("rate_limited", self._retry_at(exc)) from exc
        except TransientError:
            raise
        except GitHubFetchError as exc:
            log.warning("github_installation_list_failed", code=exc.code)
            installed = None

        plan: list[_Scope] = []
        for repo in repos:
            for scope in SCOPES:
                row = cursors.get((repo, scope))
                state = (
                    ScopeState(
                        phase=row["phase"],
                        window_low=row["window_low"],
                        window_high=row["window_high"],
                        resume_ts=row["resume_ts"],
                        resume_id=row["resume_id"],
                        listing_complete=row["listing_complete"],
                        head_sha=row["head_sha"],
                        consumed=int(row.get("consumed") or 0),
                    )
                    if row
                    else ScopeState()
                )
                sc = _Scope(repo, scope, state, row["last_progress_at"] if row else None)
                if installed is None:
                    await self._set_scope_status(
                        sc, "authorization_unverified", "installation_list_unavailable"
                    )
                    error_code = error_code or "authorization_unverified"
                elif repo.lower() not in installed:
                    await self._set_scope_status(sc, "access_lost", "access_lost")
                    error_code = error_code or "access_lost"
                else:
                    plan.append(sc)

        plan.sort(key=lambda sc: sc.last_progress_at or datetime.min.replace(tzinfo=UTC))

        remaining = int(s.connector_max_handoffs_per_run)
        pending = list(plan)
        while remaining > 0 and pending:
            quota = max(1, math.ceil(remaining / len(pending)))
            progressed = 0
            still: list[_Scope] = []
            for sc in pending:
                if remaining <= 0:
                    still.append(sc)
                    continue
                handed, scope_more, err = await self._serve_scope(
                    sc, min(quota, remaining), counts, filtered
                )
                remaining -= handed
                progressed += handed
                if err:
                    error_code = error_code or err
                if scope_more and not sc.stopped:
                    still.append(sc)
            pending = still
            if progressed == 0:
                break
        more = remaining <= 0 and any(not sc.stopped for sc in pending)
        return more, error_code

    def _retry_at(self, exc: RateLimitedError) -> datetime:
        if exc.reset_at is not None:
            return exc.reset_at
        return self._now() + timedelta(seconds=max(60.0, exc.retry_after_s or 60.0))

    async def _set_scope_status(self, sc: _Scope, status: str, code: str | None) -> None:
        sc.stopped = True

        async def _w(session: AsyncSession) -> None:
            await ss.upsert_cursor(
                session,
                workspace_id=self._workspace_id,
                connector_id=self._config.id,
                repo=sc.repo,
                scope=sc.scope,
                status=status,
                last_error_code=code,
            )

        await self._write(_w)

    async def _serve_scope(
        self, sc: _Scope, budget: int, counts: dict[str, int], filtered: dict[str, int]
    ) -> tuple[int, bool, str | None]:
        """One bounded batch for one scope. Returns (handoffs, more, error_code)."""
        s = self._settings
        owner, repo_name = sc.repo.split("/", 1)

        # Disconnect/pause honoured between batches (raises LeaseLostError).
        await self._write(self._noop)

        # C11: a current access check before EVERY content-fetch batch.
        verdict = await self._verify(owner, repo_name)
        if verdict.verdict is not AccessVerdict.AUTHORIZED:
            if verdict.verdict is AccessVerdict.DENIED:
                await self._set_scope_status(
                    sc, "reauthorization_required", "reauthorization_required"
                )
                return 0, False, "reauthorization_required"
            await self._set_scope_status(sc, "authorization_unverified", "authorization_unverified")
            return 0, False, "authorization_unverified"

        async def _mark(session: AsyncSession) -> None:
            await ss.upsert_cursor(
                session,
                workspace_id=self._workspace_id,
                connector_id=self._config.id,
                repo=sc.repo,
                scope=sc.scope,
                status="ok",
                last_error_code=None,
                authorization_verified_at=self._now(),
            )

        await self._write(_mark)

        limits = DiscoveryLimits(
            max_items=budget,
            import_days=int(s.connector_import_days),
            max_items_per_type=int(s.connector_max_items_per_type),
            max_segments_per_pr=int(s.connector_max_segments_per_pr),
            reconcile_batch=int(s.connector_review_reconcile_batch),
            max_artifact_chars=int(s.connector_max_artifact_chars),
            skip_bots=bool(s.connector_skip_bots),
            skip_empty_body=bool(s.connector_skip_empty_body),
        )
        discover = self._discover
        if discover is None:
            from sourcemind.connectors.github.discovery import discover_scope as discover
        scope_run = discover(
            self._client,
            owner=owner,
            repo=repo_name,
            scope=sc.scope,
            state=sc.state,
            now=self._now(),
            limits=limits,
        )

        handed = 0
        stopped_early = False
        exhausted = False
        try:
            async for item in scope_run.items():
                counts["found"] += 1
                if await self._handle_item(sc, item, counts) == "stop":
                    stopped_early = True
                    break
                handed += 1
                ts, rid = item.position
                sc.state = replace(sc.state, resume_ts=ts, resume_id=rid)
            # The ScopeRun stops itself at ``limits.max_items`` (= this budget) and then
            # publishes its ``result``; the consumer never cuts it short.
            exhausted = not stopped_early
        except RateLimitedError as exc:
            sc.stopped = True
            await self._set_scope_status(sc, "ok", "rate_limited")
            raise _RunEndError("rate_limited", self._retry_at(exc)) from exc
        except PermissionDeniedError:
            await self._set_scope_status(sc, "access_lost", "permission_denied")
            return handed, False, "permission_denied"
        except NotFoundError:
            await self._set_scope_status(sc, "error", "not_found")
            return handed, False, "not_found"
        except GoneError as exc:
            source_id = getattr(exc, "source_id", None)
            if source_id:
                await self._retire_gone(str(source_id))
                return handed, True, None
            await self._set_scope_status(sc, "error", "gone")
            return handed, False, "gone"
        except TransientError:
            await self._set_scope_status(sc, "ok", "transient")
            raise
        except GitHubFetchError as exc:
            await self._set_scope_status(sc, "error", exc.code)
            return handed, False, exc.code
        finally:
            aclose = getattr(scope_run, "aclose", None)
            if aclose is not None and not exhausted:
                try:
                    await aclose()
                except Exception:  # noqa: S110 - best-effort generator cleanup
                    pass

        if stopped_early:
            sc.stopped = True
            return handed, False, "handoff_failed"

        result = getattr(scope_run, "result", None)
        if result is None:
            return handed, handed >= budget, None
        for reason, n in result.filtered.items():
            filtered[reason] = filtered.get(reason, 0) + int(n)
        for reason, n in result.truncated.items():
            key = f"truncated_{reason}"
            filtered[key] = filtered.get(key, 0) + int(n)
        sc.state = result.state
        await self._persist_state(sc)
        if result.full_inventory and result.seen_source_ids:
            await self._apply_deletion_rule(sc, result.seen_source_ids)
        return handed, handed >= budget and not result.state.listing_complete, None

    async def _persist_state(self, sc: _Scope) -> None:
        st = sc.state

        async def _w(session: AsyncSession) -> None:
            await ss.upsert_cursor(
                session,
                workspace_id=self._workspace_id,
                connector_id=self._config.id,
                repo=sc.repo,
                scope=sc.scope,
                phase=st.phase,
                window_low=st.window_low,
                window_high=st.window_high,
                resume_ts=st.resume_ts,
                resume_id=st.resume_id,
                listing_complete=st.listing_complete,
                head_sha=st.head_sha,
                consumed=st.consumed,
                status="ok",
                last_error_code=None,
                last_progress_at=self._now(),
            )

        await self._write(_w)

    async def _verify(self, owner: str, repo: str) -> VerifierResult:
        if self._verifier is None:
            return VerifierResult(AccessVerdict.UNVERIFIED, "no_verifier")
        try:
            return await self._verifier.verify(
                owner=owner,
                repo=repo,
                github_user_id=int(getattr(self._live, "authorized_github_user_id", 0) or 0),
            )
        except Exception as exc:
            log.warning("github_verifier_failed", error_type=type(exc).__name__)
            return VerifierResult(AccessVerdict.UNVERIFIED, "verifier_unavailable")

    # ── one item ─────────────────────────────────────────────────────────────

    async def _handle_item(self, sc: _Scope, item: Any, counts: dict[str, int]) -> str:
        """Hand one Discovered item off. Returns 'ok' or 'stop' (scope stops this run)."""
        doc: ConnectorDocument = item.document
        s = self._settings
        wkey = (sc.repo, sc.scope, doc.source_id)
        _attempts, state = self._work_items.get(wkey, (0, "pending"))
        ts, rid = item.position

        async def _advance(session: AsyncSession) -> None:
            await ss.upsert_cursor(
                session,
                workspace_id=self._workspace_id,
                connector_id=self._config.id,
                repo=sc.repo,
                scope=sc.scope,
                resume_ts=ts,
                resume_id=rid,
                last_progress_at=self._now(),
            )

        if state == "quarantined":
            # Re-armable by an admin; until then the key passes it without a handoff.
            await self._write(_advance)
            counts["skipped"] += 1
            return "ok"

        await self._write(self._noop)  # heartbeat + disconnect check before the work

        async def pre_commit(session: AsyncSession) -> None:
            assert self._token is not None
            await ss.renew_lease(session, self._token, lease_seconds=self._lease_seconds)
            await _advance(session)
            if wkey in self._work_items:
                await ss.mark_work_done(
                    session,
                    connector_id=self._config.id,
                    repo=sc.repo,
                    scope=sc.scope,
                    source_id=doc.source_id,
                )
            self._pre_commit_ran = True

        self._pre_commit_ran = False
        try:
            ingested = await self._ingest(doc, pre_commit=pre_commit)
            if self._pre_commit_ran:
                await self._session.commit()
            else:
                await self._write(_advance)  # deduplicated before pre_commit could run
        except LeaseLostError:
            raise
        except Exception as exc:
            await self._session.rollback()
            err_name = type(exc).__name__[:60]
            log.warning(
                "github_connector_handoff_failed",
                source_id=doc.source_id,
                error_type=type(exc).__name__,
            )
            counts["failed"] += 1

            async def _fail(session: AsyncSession) -> str:
                n, st = await ss.record_work_failure(
                    session,
                    workspace_id=self._workspace_id,
                    connector_id=self._config.id,
                    repo=sc.repo,
                    scope=sc.scope,
                    source_id=doc.source_id,
                    error_code=err_name,
                    max_attempts=int(s.connector_max_handoff_attempts),
                )
                if st == "quarantined":
                    await _advance(session)  # the key may now pass the poison item
                self._work_items[wkey] = (n, st)
                return st

            if await self._write(_fail) == "quarantined":
                counts["quarantined"] += 1
                return "ok"
            return "stop"

        if not ingested:
            counts["skipped"] += 1
        elif int(self._last_result.get("revision_seq") or 1) > 1:
            counts["updated"] += 1
        else:
            counts["new"] += 1
        return "ok"

    # ── deletion rule (C7) ───────────────────────────────────────────────────

    async def _apply_deletion_rule(self, sc: _Scope, seen: frozenset[str]) -> None:
        """Two consecutive COMPLETE inventories that omit an artifact retire it."""
        retire = self._retire or retire_document_memories

        async def _w(session: AsyncSession) -> None:
            due = await ss.apply_inventory(
                session,
                workspace_id=self._workspace_id,
                repo=sc.repo,
                scope=sc.scope,
                seen_source_ids=seen,
            )
            for source_id, document_id in due:
                await retire(
                    session,
                    workspace_id=self._workspace_id,
                    document_id=document_id,
                    reason="source_deleted",
                    now=self._now(),
                )
                log.info("github_artifact_retired_missing", source_id=source_id)

        await self._write(_w)

    async def _retire_gone(self, source_id: str) -> None:
        """HTTP 410 on a refetch retires immediately (a bare 404 never does)."""
        retire = self._retire or retire_document_memories

        async def _w(session: AsyncSession) -> None:
            for document_id in await ss.head_document_for(
                session, workspace_id=self._workspace_id, source_id=source_id
            ):
                await retire(
                    session,
                    workspace_id=self._workspace_id,
                    document_id=document_id,
                    reason="source_deleted",
                    now=self._now(),
                )

        await self._write(_w)

    # ── recovery sweep (C4) ──────────────────────────────────────────────────

    async def recovery_sweep(self) -> dict[str, int]:
        """Re-dispatch/re-queue this connector's stranded documents. No GitHub calls."""
        out = {"redispatched": 0, "requeued": 0, "errors": 0}
        now = self._now()
        await self._write(self._noop)
        attempts = func.coalesce(Document.pipeline_data["attempts"].as_integer(), 0)
        rows = (
            await self._session.execute(
                select(
                    Document.id,
                    Document.ingestion_status,
                    Document.pipeline_data["next_retry_at"].as_string().label("next_retry_at"),
                )
                .where(
                    Document.workspace_id == self._workspace_id,
                    Document.connector_id == self._config.id,
                    Document.deleted_at.is_(None),
                    or_(
                        and_(
                            Document.ingestion_status == IngestionStatus.PENDING,
                            Document.pipeline_data["dispatch_state"]
                            .as_string()
                            .in_(_UNCONFIRMED_DISPATCH_STATES),
                        ),
                        and_(
                            Document.ingestion_status == IngestionStatus.FAILED,
                            Document.pipeline_data["error_code"]
                            .as_string()
                            .in_(_RETRYABLE_ERROR_CODES),
                            attempts < int(self._settings.connector_max_handoff_attempts),
                        ),
                    ),
                )
                .limit(_SWEEP_BATCH)
            )
        ).all()
        await self._session.rollback()

        for row in rows:
            try:
                if row.ingestion_status == IngestionStatus.PENDING:
                    await recover_pending_dispatch(self._session, row.id, self._workspace_id)
                    out["redispatched"] += 1
                    continue
                if requeue_transient_failure is None:
                    continue
                due = _parse_ts(row.next_retry_at)
                if due is not None and due > now:
                    continue
                if await requeue_transient_failure(
                    self._session, row.id, self._workspace_id, now=now
                ):
                    out["requeued"] += 1
            except Exception as exc:
                await self._session.rollback()
                out["errors"] += 1
                log.warning(
                    "github_connector_recovery_failed",
                    document_id=str(row.id),
                    error_type=type(exc).__name__,
                )
        return out

    # ── dedup + ingest ───────────────────────────────────────────────────────

    async def _ingest(self, doc: ConnectorDocument, *, pre_commit: PreCommit | None = None) -> bool:
        """Hand one authored segment to the receiver.

        The receiver decides: the same source revision comes back as ``already_exists``
        (counted as skipped), a changed one is allocated the next revision and supersedes
        the previous document. Documents stranded before dispatch are the recovery
        sweep's job, not this method's.

        Returns True if a new document (first import or new revision) was created.
        """
        self._last_result = {}
        attribution = await self._attribution_decision(doc)
        try:
            result = await self._handoff(doc, attribution, pre_commit)
        except Exception as exc:
            log.warning(
                "github_connector_ingest_failed",
                source_id=doc.source_id,
                error_type=type(exc).__name__,
            )
            raise
        self._last_result = result if isinstance(result, dict) else {}
        return not result.get("already_exists")

    async def _handoff(
        self, doc: ConnectorDocument, attribution: dict, pre_commit: PreCommit | None
    ) -> dict[str, Any]:
        """Call ``receive`` with the revision identity, context and ``pre_commit`` so the
        fence renewal and the resume key are atomic with the handoff."""
        link: dict[str, Any] = {
            "source_tool": doc.source_tool,
            "source_type": doc.source_type,
            "source_id": doc.source_id,
            "source_url": doc.source_url,
            "source_author": doc.source_author,
            "source_timestamp": doc.source_timestamp,
            "artifact_metadata": doc.metadata,
        }
        kwargs: dict[str, Any] = {
            "session": self._session,
            "workspace_id": self._workspace_id,
            "user_id": self._user_id,  # technical submitter only, never an author
            "content": doc.content,
            "source_type": DocumentSourceType.TEXT,
            "title": doc.title,
            "idempotency_key": doc.idempotency_key,
            "artifact_link": link,
            "attribution": attribution,
        }
        revision = hashlib.sha256(doc.content.encode()).hexdigest()
        live = self._live
        link["source_revision"] = revision
        link["connector_id"] = self._config.id
        link["connector_generation"] = (
            live.generation if live is not None else getattr(self._config, "generation", 0)
        )
        kwargs["idempotency_key"] = hashlib.sha256(
            f"{doc.idempotency_key}:{revision}".encode()
        ).hexdigest()
        context = getattr(doc, "context", None)
        if context:
            kwargs["context"] = context
        mode = getattr(doc, "ingestion_mode", None)
        if mode:
            kwargs["ingestion_mode"] = mode
        kwargs["pre_commit"] = pre_commit
        return await receive(**kwargs)

    async def _attribution_decision(self, doc: ConnectorDocument) -> dict:
        """Sync-time attribution decision, resolved ONLY by numeric GitHub id.

        A login, display name or e-mail is never used to find a user. Authors
        without a numeric id (git display-name fallback) are recorded as
        unresolved without any lookup. The connector creator/initiator is never
        consulted.
        """
        author = doc.metadata.get("author") or {}
        github_user_id = (
            valid_github_user_id(author.get("github_user_id"))
            if author.get("kind") == "github_account"
            else None
        )
        link_user_id: uuid.UUID | None = None
        resolution = NO_GITHUB_ACCOUNT
        if github_user_id is not None:
            lookup = await resolve_github_link(self._session, self._workspace_id, github_user_id)
            link_user_id, resolution = lookup.user_id, lookup.status
        return {
            "mode": "external",
            "source_tool": doc.source_tool,
            "github_user_id": github_user_id,
            "source_author": doc.source_author,
            "link_user_id": str(link_user_id) if link_user_id else None,
            "resolution": resolution,
        }


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
