"""Evaluation runner — orchestrates baselines against the retrieval metrics.

Usage:
    python -m evaluation.runner \
        --ground-truth evaluation/data/ground_truth.json \
        --output evaluation/data/results.json \
        --limit 5

Environment variables:
    OPENAI_API_KEY          — NaiveRAG embeddings (required for that baseline)
    SOURCEMIND_API_URL      — base URL of the SourceMind API
    SOURCEMIND_WORKSPACE_ID — workspace to ingest into and search
    SOURCEMIND_API_KEY      — static bearer token, if the instance needs one
    CLERK_SECRET_KEY + CLERK_EVAL_USER_ID
                            — mint short-lived session JWTs instead of a static
                              token; refreshed automatically near expiry
    SUPERMEMORY_API_KEY     — Supermemory baseline (excluded when unset)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.metrics.knowledge_retention import knowledge_retention
from evaluation.metrics.latency import measure_latency
from evaluation.metrics.role_scoped_retrieval import role_scoped_retrieval

# ── conflict_detection: deliberately excluded ────────────────────────────────
# Printed wherever the run is summarised so this reads as a decision rather
# than an omission. See also report.py, which renders the same text.
ATTRIBUTION_EXCLUSION_NOTE = (
    "attribution_accuracy: excluded from this run - ingestion attributes every "
    "memory to the authenticated API caller rather than the artifact's original "
    "author, because create_initial_attribution() writes a single contributor at "
    "weight 1.0 and the 5-signal algorithm runs only on PATCH. The metric would "
    "therefore report 0.000 for SourceMind by construction, measuring a wiring "
    "gap rather than attribution quality. Tracked in ARCHITECTURE.md; scheduled "
    "as follow-up work."
)

CONFLICT_EXCLUSION_NOTE = (
    "conflict_detection: excluded from this run — dataset lacks labeled "
    "negative pairs; system now includes a novel human-gated severity-tiered "
    "resolution design not present in comparable systems (Supermemory, Mem0 "
    "both auto-resolve via LLM with no human oversight — see paper Section X). "
    "Full evaluation of this feature is planned as follow-up work."
)

_ID_MAP_PATH = Path("evaluation/data/sourcemind_id_map.json")


def _load_api_env() -> None:
    """Load apps/api/.env regardless of where this runner was launched from.

    The runner has to run from the repository root - its dataset and output
    paths are repo-relative - but pydantic-settings resolves .env relative to
    the CURRENT WORKING DIRECTORY. From the root it therefore found no .env and
    fell back to a Redis URL of redis.railway.internal:6379, which resolves
    only inside Railway's network.

    Nothing surfaced, because the runner reaches SourceMind over HTTP and only
    touches Celery in _reenqueue. The consequence was that the retry-on-
    disappearance path failed for the whole of run 3 with an unresolvable
    broker, and the one vanished task was recorded as retry_unavailable rather
    than actually retried.

    override=False so anything explicitly exported still wins.
    """
    env_path = Path(__file__).resolve().parent.parent / "apps" / "api" / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except Exception as exc:  # pragma: no cover - depends on the install
        print(f"  ! could not load {env_path}: {exc}")


_load_api_env()


# ── Auth ─────────────────────────────────────────────────────────────────────

class TokenProvider:
    """Supplies a bearer token, refreshing it before it expires.

    Three modes, chosen by what the environment provides:

      static  SOURCEMIND_API_KEY is used verbatim and never refreshed.
      clerk   CLERK_SECRET_KEY + CLERK_EVAL_USER_ID mint a session JWT through
              the Clerk Backend API. Clerk session tokens are short-lived
              (~60s), which is why every request goes through here rather than
              caching one token for the whole run.
      none    No Authorization header. Correct against an instance running the
              development auth bypass.
    """

    # Refresh once fewer than this many seconds remain, so a token cannot
    # expire in flight between the check and the server validating it.
    _REFRESH_MARGIN_S = 15.0

    def __init__(self) -> None:
        self._static = os.environ.get("SOURCEMIND_API_KEY", "").strip()
        self._clerk_secret = os.environ.get("CLERK_SECRET_KEY", "").strip()
        self._clerk_user = os.environ.get("CLERK_EVAL_USER_ID", "").strip()
        self._token: str | None = None
        self._expires_at = 0.0
        self._session_id: str | None = None

        if self._static:
            self.mode = "static"
        elif self._clerk_secret and self._clerk_user:
            self.mode = "clerk"
        else:
            self.mode = "none"

    def headers(self) -> dict[str, str]:
        token = self._current_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _current_token(self) -> str | None:
        if self.mode == "static":
            return self._static
        if self.mode != "clerk":
            return None
        if self._token and time.monotonic() < self._expires_at - self._REFRESH_MARGIN_S:
            return self._token
        return self._mint_clerk_token()

    def _mint_clerk_token(self) -> str | None:
        """Mint a session JWT via the Clerk Backend API.

        NOTE: this project has never had a Clerk session flow — the end-to-end
        pipeline test authenticates through the in-process development bypass,
        not over the network. This path is written to Clerk's documented
        Backend API but is unexercised until someone supplies
        CLERK_EVAL_USER_ID for a real Clerk user.
        """
        import httpx

        auth = {"Authorization": f"Bearer {self._clerk_secret}"}
        try:
            if not self._session_id:
                created = httpx.post(
                    "https://api.clerk.com/v1/sessions",
                    headers=auth,
                    json={"user_id": self._clerk_user},
                    timeout=15.0,
                )
                created.raise_for_status()
                self._session_id = created.json()["id"]

            minted = httpx.post(
                f"https://api.clerk.com/v1/sessions/{self._session_id}/tokens",
                headers=auth,
                # json={} rather than no body: Clerk rejects an absent
                # Content-Type on this endpoint with 415 "Content-Type is
                # unsupported", even though the request needs no fields. This
                # is what the docstring above meant by unexercised - the path
                # had never run against the real API until run 4.
                json={},
                timeout=15.0,
            )
            minted.raise_for_status()
            payload = minted.json()
            self._token = payload.get("jwt") or payload.get("token")
            # Clerk session tokens last 60s unless the template says otherwise.
            self._expires_at = time.monotonic() + float(payload.get("expires_in", 60))
            return self._token
        except Exception as exc:
            print(f"    ! Clerk token mint failed: {exc}")
            # Drop the session so the next attempt re-creates it rather than
            # retrying against a session that may have been revoked.
            self._session_id = None
            return None


# ── SourceMind retriever adapter ─────────────────────────────────────────────

class SourceMindRetriever:
    """Ingests into, and searches, a live SourceMind instance.

    IDs need translating in both directions. The metrics compare against
    ground-truth ids like ``facebook/react/commits/c014813``, while SourceMind
    mints its own memory UUIDs — and one ingested document fans out into
    several memories, since the pipeline extracts discrete facts. index()
    therefore records ground-truth id -> [memory ids] and retrieve() maps each
    hit back, so a result counts as a hit for the artifact it came from.
    """

    def __init__(
        self,
        api_url: str,
        workspace_id: str,
        tokens: TokenProvider | None = None,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._workspace_id = workspace_id
        self._tokens = tokens or TokenProvider()
        self._memory_to_gt: dict[str, str] = {}
        self._gt_artifact_type: dict[str, str] = {}

    # ── ingestion ────────────────────────────────────────────────────────────

    def index(
        self,
        items: list[dict[str, Any]],
        poll_timeout_s: float = 90.0,
        progress_every: int = 20,
    ) -> dict[str, Any]:
        """Ingest ground-truth items, retry anything that vanishes, reconcile.

        Tasks are acknowledged on receipt, so a worker that drops one leaves the
        document sitting at 'queued' forever with no failure recorded anywhere.
        That is not hypothetical: a 5-item run lost 4 this way.

        Re-POSTing cannot fix it. The receiver deduplicates on a SHA-256 of the
        content and returns the existing job id WITHOUT re-enqueueing, so an
        identical resubmit just hands back the same stuck job. The retry
        therefore re-enqueues the Celery task directly by document id, which is
        the only thing that actually revives a dropped task.

        Every item ends in exactly one bucket, and the buckets are asserted to
        sum to len(items) — silence is what this whole mechanism exists to
        prevent, so an unaccounted item is a hard error.
        """
        import httpx

        stuck: list[tuple[str, str, str]] = []   # (gt_id, job_id, document_id)
        failed_ingestion: list[dict[str, str]] = []
        ingested = 0
        started = time.monotonic()

        # ── pass 1 ───────────────────────────────────────────────────────────
        for n, item in enumerate(items, start=1):
            gt_id = item["id"]
            self._gt_artifact_type[gt_id] = item.get("artifact_type", "")
            try:
                submitted = self._submit(httpx, item)
                if submitted is None:
                    failed_ingestion.append({"id": gt_id, "reason": "submit_failed"})
                    continue
                job_id, document_id = submitted

                memory_ids = self._await_job(httpx, job_id, poll_timeout_s)
                if memory_ids is None:
                    stuck.append((gt_id, job_id, document_id))
                    continue
                if not memory_ids:
                    failed_ingestion.append({"id": gt_id, "reason": "no_memories"})
                    continue

                for mid in memory_ids:
                    self._memory_to_gt[mid] = gt_id
                ingested += 1
            except Exception as exc:
                failed_ingestion.append(
                    {"id": gt_id, "reason": f"{type(exc).__name__}: {exc}"}
                )
                print(f"    ! [{n}/{len(items)}] {gt_id}: {type(exc).__name__}: {exc}")

            if n % progress_every == 0 or n == len(items):
                rate = (time.monotonic() - started) / n
                remaining = rate * (len(items) - n)
                print(
                    f"    [{n}/{len(items)}] ok={ingested} stuck={len(stuck)} "
                    f"failed={len(failed_ingestion)} "
                    f"memories={len(self._memory_to_gt)} "
                    f"~{remaining / 60:.1f}min left"
                )

        # ── pass 2: retry each vanished task exactly once ────────────────────
        retried_ok = 0
        if stuck:
            print(f"    retrying {len(stuck)} vanished task(s)...")
            for gt_id, job_id, document_id in stuck:
                if not self._reenqueue(document_id):
                    failed_ingestion.append(
                        {"id": gt_id, "reason": "retry_unavailable"}
                    )
                    continue
                memory_ids = self._await_job(httpx, job_id, poll_timeout_s)
                if memory_ids:
                    for mid in memory_ids:
                        self._memory_to_gt[mid] = gt_id
                    ingested += 1
                    retried_ok += 1
                else:
                    failed_ingestion.append(
                        {"id": gt_id, "reason": "vanished_twice"}
                    )
            print(f"    retry recovered {retried_ok}/{len(stuck)}")

        self._save_id_map()

        # ── reconciliation ───────────────────────────────────────────────────
        accounted = ingested + len(failed_ingestion)
        stats = {
            "total": len(items),
            "ingested": ingested,
            "recovered_by_retry": retried_ok,
            "failed_ingestion": len(failed_ingestion),
            "failures": failed_ingestion,
            "memories": len(self._memory_to_gt),
            "reconciled": accounted == len(items),
        }
        if accounted != len(items):
            raise AssertionError(
                f"reconciliation failed: {ingested} ingested + "
                f"{len(failed_ingestion)} failed = {accounted}, expected "
                f"{len(items)}"
            )
        return stats

    def _reenqueue(self, document_id: str) -> bool:
        """Re-publish the ingestion task for a document whose task was lost.

        Reaches past the HTTP API on purpose: the API cannot re-enqueue a
        deduplicated document, and this is the only path that revives one.
        Returns False when the broker is not reachable from here, so the
        caller records an honest failure rather than pretending it retried.
        """
        from sourcemind.workers.ingestion import process_document

        kwargs = {
            "document_id": document_id,
            "workspace_id": self._workspace_id,
            "user_id": os.environ.get(
                "SOURCEMIND_EVAL_USER_ID",
                "00000000-0000-4000-8000-000000000001",
            ),
        }

        # ignore_result=True keeps the result backend out of the path. This
        # runs at the END of a multi-hour ingestion, and the Celery redis
        # result-store connection has gone stale by then:
        #
        #   RuntimeError: Retry limit exceeded while trying to reconnect to the
        #   Celery redis result store backend.
        #
        # Nothing here reads a task result - completion is observed by polling
        # the job endpoint - so the backend is pure overhead and one more thing
        # to go stale.
        last: Exception | None = None
        for attempt in (1, 2):
            try:
                if attempt == 1:
                    process_document.apply_async(
                        kwargs=kwargs, priority=5, ignore_result=True
                    )
                else:
                    # A dedicated connection rather than the pooled one, which
                    # may be holding a dead socket. force_close_all() is NOT
                    # the way to do this: it closes the pool without reopening
                    # it, and the next acquire fails with "Acquire on closed
                    # pool".
                    from sourcemind.workers.celery_app import app as celery_app

                    with celery_app.connection_for_write() as conn:
                        process_document.apply_async(
                            kwargs=kwargs,
                            priority=5,
                            ignore_result=True,
                            connection=conn,
                        )
                return True
            except Exception as exc:
                last = exc
                if attempt == 1:
                    time.sleep(2.0)

        # Flattened: this exception's message starts with a newline, which split
        # the log line and made the error look empty.
        detail = " ".join(str(last).split())
        print(f"    ! re-enqueue unavailable: {type(last).__name__}: {detail}")
        return False

    # Submission timeout and attempts.
    #
    # Measured POST latency against Railway is ~880ms typical, with occasional
    # stalls at 21s, 49s and 109s and no server-side error to go with them. At
    # a 60s timeout those stalls were recorded as failed ingestions for items
    # the server had accepted, so the ceiling is well clear of the worst
    # observed stall and a timeout is retried rather than written off.
    _SUBMIT_TIMEOUT_S = 180.0
    _SUBMIT_ATTEMPTS = 3

    def _submit(self, httpx: Any, item: dict[str, Any]) -> tuple[str, str] | None:
        """POST the document, retrying a stalled or dropped request.

        The idempotency key is regenerated per attempt on purpose: content
        deduplication already prevents a duplicate document, so a retry that
        the server did receive returns the existing record rather than
        creating a second one.
        """
        last_error: Exception | None = None
        for attempt in range(1, self._SUBMIT_ATTEMPTS + 1):
            try:
                return self._submit_once(httpx, item)
            except Exception as exc:  # network stall, reset, timeout
                last_error = exc
                if attempt < self._SUBMIT_ATTEMPTS:
                    print(
                        f"    ~ submit {item['id']} attempt {attempt} "
                        f"{type(exc).__name__}; retrying"
                    )
                    time.sleep(2.0 * attempt)

        print(
            f"    ! submit {item['id']}: gave up after "
            f"{self._SUBMIT_ATTEMPTS} attempts: {type(last_error).__name__}: "
            f"{last_error}"
        )
        return None

    def _submit_once(self, httpx: Any, item: dict[str, Any]) -> tuple[str, str] | None:
        response = httpx.post(
            f"{self._api_url}/v1/memories",
            params={"workspace_id": self._workspace_id},
            headers={
                **self._tokens.headers(),
                "Idempotency-Key": str(uuid.uuid4()),
            },
            json={"content": item["content"], "source_type": "text"},
            timeout=self._SUBMIT_TIMEOUT_S,
        )
        if response.status_code >= 400:
            print(f"    ! submit {item['id']}: HTTP {response.status_code} {response.text[:160]}")
            return None
        data = response.json().get("data", {})
        job_id, document_id = data.get("job_id"), data.get("document_id")
        if not job_id or not document_id:
            return None
        return str(job_id), str(document_id)

    def _await_job(self, httpx: Any, job_id: str, timeout_s: float) -> list[str] | None:
        """Poll until the job reports completion. None on timeout or failure."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                response = httpx.get(
                    f"{self._api_url}/v1/memories/jobs/{job_id}",
                    headers=self._tokens.headers(),
                    timeout=60.0,
                )
            except Exception:
                # A stalled poll is not a failed job. Keep polling until the
                # deadline rather than abandoning a document mid-flight.
                time.sleep(2.0)
                continue
            if response.status_code >= 400:
                time.sleep(1.0)
                continue
            data = response.json().get("data", {})
            status = (data.get("status") or "").lower()
            if status in ("completed", "complete"):
                return [str(m) for m in (data.get("memory_ids") or [])]
            if status in ("failed", "error"):
                print(f"    ! job {job_id} failed: {data.get('error')}")
                return None
            time.sleep(1.0)
        return None

    def _save_id_map(self) -> None:
        _ID_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _ID_MAP_PATH.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "workspace_id": self._workspace_id,
                    "memory_to_ground_truth": self._memory_to_gt,
                    "ground_truth_artifact_type": self._gt_artifact_type,
                },
                f,
                indent=2,
            )
        print(f"    id map -> {_ID_MAP_PATH} ({len(self._memory_to_gt)} memories)")

    def load_id_map(self) -> bool:
        """Reuse a previous ingestion so querying can be run separately."""
        if not _ID_MAP_PATH.exists():
            return False
        with _ID_MAP_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        self._memory_to_gt = data.get("memory_to_ground_truth", {})
        self._gt_artifact_type = data.get("ground_truth_artifact_type", {})
        return bool(self._memory_to_gt)

    # ── retrieval ────────────────────────────────────────────────────────────

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        import httpx
        try:
            response = httpx.post(
                f"{self._api_url}/v1/memories/search",
                params={"workspace_id": self._workspace_id},
                headers=self._tokens.headers(),
                json={
                    "query": query,
                    "limit": top_k,
                    # Without this the response carries no contributor at all
                    # and attribution_accuracy has nothing to read.
                    "include_attribution": True,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
        except Exception as exc:
            print(f"    ! search failed: {type(exc).__name__}: {exc}")
            return []

        output: list[dict[str, Any]] = []
        for r in results[:top_k]:
            # SearchResultItem nests the memory: {memory: {...}, score, rank,
            # match_type}. The id and content live one level down.
            memory = r.get("memory") or {}
            memory_id = str(memory.get("id", ""))
            gt_id = self._memory_to_gt.get(memory_id, memory_id)

            # Author comes from SourceMind's own attribution, never from the
            # ground truth — reading it from the dataset would be scoring the
            # system against an answer we handed it.
            attribution = memory.get("attribution") or []
            author = ""
            if attribution:
                primary = next(
                    (a for a in attribution if a.get("is_primary")), attribution[0]
                )
                author = (primary.get("user") or {}).get("display_name") or ""

            output.append(
                {
                    "id": gt_id,
                    "content": memory.get("content", ""),
                    "score": r.get("score", 0.0),
                    "metadata": {
                        # Which source artifact this memory came from. Derived
                        # from the ingestion map, not from the query.
                        "artifact_type": self._gt_artifact_type.get(gt_id, ""),
                        "author": author,
                    },
                }
            )
        return output


# ── Runner ───────────────────────────────────────────────────────────────────

def run_evaluation(
    dataset: list[dict[str, Any]],
    retrievers: dict[str, Any],
) -> dict[str, Any]:
    """Run the four retrieval metrics for every retriever."""
    all_results: dict[str, Any] = {}
    queries = [item["question"] for item in dataset]

    print(f"\n  {ATTRIBUTION_EXCLUSION_NOTE}")
    print(f"\n  {CONFLICT_EXCLUSION_NOTE}")

    for name, retriever in retrievers.items():
        print(f"\n  Evaluating: {name}")
        results: dict[str, Any] = {}

        print("    -> knowledge_retention...")
        results["knowledge_retention"] = knowledge_retention(retriever, dataset, top_k=5)

        print("    -> role_scoped_retrieval (engineer)...")
        results["role_scoped_retrieval_engineer"] = role_scoped_retrieval(
            retriever, dataset, role="engineer"
        )
        print("    -> role_scoped_retrieval (manager)...")
        results["role_scoped_retrieval_manager"] = role_scoped_retrieval(
            retriever, dataset, role="manager"
        )

        print("    -> latency...")
        results["latency"] = measure_latency(retriever, queries[:50], top_k=5)

        results["attribution_accuracy"] = {
            "metric": "attribution_accuracy",
            "status": "excluded",
            "reason": ATTRIBUTION_EXCLUSION_NOTE,
        }
        results["conflict_detection"] = {
            "metric": "conflict_detection",
            "status": "excluded",
            "reason": CONFLICT_EXCLUSION_NOTE,
        }

        all_results[name] = results
        kr = results["knowledge_retention"]["score"]
        lat = results["latency"]["p95_ms"]
        print(f"    recall@5={kr:.3f}  p95={lat:.0f}ms")

    return all_results


def main(ground_truth_path: str, output_path: str, limit: int | None, skip_index: bool) -> None:
    gt_file = Path(ground_truth_path)
    if not gt_file.exists():
        print(f"ERROR: Ground truth file not found: {gt_file}", file=sys.stderr)
        sys.exit(1)

    with gt_file.open(encoding="utf-8") as f:
        gt_data = json.load(f)
    dataset = gt_data.get("items", gt_data)
    if limit:
        dataset = dataset[:limit]
    print(f"Loaded {len(dataset)} ground-truth items.")

    retrievers: dict[str, Any] = {}

    # ── NaiveRAG ─────────────────────────────────────────────────────────────
    try:
        from evaluation.baselines.naive_rag import NaiveRAGBaseline
        rag = NaiveRAGBaseline()
        print("Indexing NaiveRAG...")
        rag.index(dataset)
        retrievers["naive_rag"] = rag
    except Exception as e:
        print(f"  NaiveRAG unavailable: {e}")

    # ── Supermemory ──────────────────────────────────────────────────────────
    if os.environ.get("SUPERMEMORY_API_KEY"):
        try:
            from evaluation.baselines.supermemory_baseline import SupermemoryBaseline
            sm = SupermemoryBaseline(api_key=os.environ["SUPERMEMORY_API_KEY"])
            print("Indexing Supermemory...")
            sm.index(dataset)
            retrievers["supermemory"] = sm
        except Exception as e:
            print(f"  Supermemory unavailable: {e}")
    else:
        print("  supermemory: excluded — SUPERMEMORY_API_KEY not set")

    # ── SourceMind ───────────────────────────────────────────────────────────
    sm_url = os.environ.get("SOURCEMIND_API_URL")
    sm_workspace = os.environ.get("SOURCEMIND_WORKSPACE_ID")
    if sm_url and sm_workspace:
        tokens = TokenProvider()
        print(f"  SourceMind auth mode: {tokens.mode}")
        retriever = SourceMindRetriever(sm_url, sm_workspace, tokens)
        if skip_index:
            if retriever.load_id_map():
                print("  Reusing existing SourceMind id map (--skip-index).")
            else:
                print("  WARNING: --skip-index set but no id map found; recall will be 0.")
        else:
            print(f"Ingesting {len(dataset)} items into SourceMind...")
            stats = retriever.index(dataset)
            print(f"  Ingestion: {stats}")
        retrievers["sourcemind"] = retriever
    else:
        print("  SourceMind: SOURCEMIND_API_URL / SOURCEMIND_WORKSPACE_ID not set — skipping")

    if not retrievers:
        print("ERROR: No retrievers configured.", file=sys.stderr)
        sys.exit(1)

    print(f"\nRunning evaluation with {len(retrievers)} retriever(s)...")
    results = run_evaluation(dataset, retrievers)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ground_truth_items": len(dataset),
        "retrievers": list(results.keys()),
        "excluded_metrics": {
            "attribution_accuracy": ATTRIBUTION_EXCLUSION_NOTE,
            "conflict_detection": CONFLICT_EXCLUSION_NOTE,
        },
        "results": results,
    }

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run SourceMind evaluation harness")
    parser.add_argument("--ground-truth", default="evaluation/data/ground_truth.json")
    parser.add_argument("--output", default="evaluation/data/results.json")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Only use the first N ground-truth items (dry runs).",
    )
    parser.add_argument(
        "--skip-index", action="store_true",
        help="Reuse the stored SourceMind id map instead of re-ingesting.",
    )
    args = parser.parse_args()
    main(args.ground_truth, args.output, args.limit, args.skip_index)
