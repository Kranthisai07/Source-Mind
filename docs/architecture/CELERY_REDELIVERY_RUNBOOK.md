# Celery redelivery after a worker dies: measured behaviour and operator procedure

Scope: tasks with `acks_late=True` on the Redis broker (kombu 5.6.2, celery 5.6.2, redis-py 6.4.0), i.e.
`process_document` and the connector sync tasks. Measured with `redelivery_probe.py` (a minimal one-task app,
no SourceMind code; real Redis; the worker process group SIGKILLed mid-task), 2026-10-14, WSL, CPython 3.12.

## What was measured

| visibility_timeout | replacement worker started | redelivered after the kill | after the replacement started |
|---|---|---|---|
| 5 s (mutex default and 5 s) | immediately | 100.9, 100.9, 101.1, 100.9 s | same |
| 30 s | immediately | 100.9 s | same |
| 5 s | 30 s after the kill | 31.4 s | 1.4 s |
| 120 s | immediately | 201.1 s | 201.1 s |

## What it means (derived from these runs plus kombu's `QoS.restore_visible`; not a promise)

* An unacknowledged message is put back only by a **live worker's restore tick**. A tick runs every 10 s but
  acts on every 10th call: at about 0 s, 100 s, 200 s, ... after that worker started. It restores a message only
  if the message is **older than `visibility_timeout`** at that moment.
* So recovery happens at the first acting tick at which the message's age exceeds the timeout. With the
  replacement started at once, a 5 s or 30 s timeout still took ~100 s (the first tick found the message too
  young); a 120 s timeout took ~200 s. With a worker started 30 s later, a 5 s timeout recovered in 1.4 s.
* The unacknowledged state is held in Redis (`unacked` hash and `unacked_index` zset), which is why a restart
  of the worker alone is enough; nothing is lost while Redis is up and not flushed.
* **No upper bound is claimed from the setting alone.** The model gives "timeout + up to ~100 s, provided a
  worker is running", and the runs agree for 5, 30 and 120 s, but the production default is **3600 s** and
  was not measured at that value. Without any running worker nothing is restored at all.
* `unacked_mutex_expire` made no difference in these runs.

## Operator procedure (safe; every step is read-only unless stated)

1. Keep at least one worker running on the queue. Do not flush Redis, do not delete `unacked` /
   `unacked_index`, do not `redis-cli FLUSHDB`.
2. Look for stuck work:
   `SELECT id, ingestion_status, updated_at FROM documents WHERE ingestion_status IN ('pending','processing')
   AND updated_at < now() - interval '30 minutes' ORDER BY updated_at;`
   and, on the broker database, `HLEN unacked` and `ZRANGE unacked_index 0 -1 WITHSCORES` (scores are delivery
   times).
3. If a document has been stuck longer than the configured visibility timeout plus ~2 minutes with a live
   worker, re-send its task; duplicate delivery is proven harmless (the claim is `FOR NO KEY UPDATE SKIP LOCKED`,
   a completed document returns "already completed", and three deliveries of one document produced one memory in
   the real-worker test):
   `celery -A sourcemind.workers.celery_app:app call sourcemind.workers.ingestion.process_document
   --queue ingestion --kwargs '{"document_id":"<id>","workspace_id":"<ws>","user_id":"<submitter>"}'`
4. Connector documents that failed (not merely stuck): use **Retry failed** (or `POST /v1/connectors/{id}/retry-failed`),
   and **Re-arm quarantined** for quarantined items. A paused connector must be resumed first.
5. For a sync worker killed holding a lease: do nothing; the lease expires (10 min) and the next run reaps
   the interrupted log and continues from the stored resume key.

## Not done

Stale `processing` documents are not swept automatically, and the visibility timeout is not tuned. A deliberate
`visibility_timeout` (longer than the longest task, e.g. the 120 s embedding/extraction path plus margin) and a
stale-`processing` sweep are the recommended follow-ups; they change behaviour and are left for a decision.
