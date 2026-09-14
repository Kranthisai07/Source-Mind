# Disposable test-resource leases

Two agents share one machine. Test infrastructure is the only thing they can
collide on destructively: a dropped schema or a flushed cache is not a merge
conflict Git will catch, it is a run that silently produced the wrong answer.

The rule is exclusivity by non-contention. Claim a port range here before
binding to it, and never bind to a range claimed by someone else — including
to "just read" from it, because a migration or a truncate issued against a
database another agent is mid-run on invalidates their evidence, not yours.

A lease is released by deleting its row and tearing the resource down.

## Active

None.

## Released

| Ports         | Holder | Purpose                                 | Claimed    | Released   |
|---------------|--------|-----------------------------------------|------------|------------|
| 55433 / 56380 | Claude | Conflict-resolution end-to-end exercise | 2026-09-14 | 2026-09-14 |

Cluster stopped, redis shut down, and `/tmp/sm-claude-e2e` removed. Both ports
verified free. Nothing from that run outlived it; to reproduce, rebuild from
`apps/web/e2e/README.md`.

## Not available

| Ports         | Holder | Note                                                                 |
|---------------|--------|----------------------------------------------------------------------|
| 5432 / 6379   | —      | Ordinary development data. Not disposable. Never a test target.       |
| 55432 / 56379 | Codex  | Security-acceptance stack. Lease status was unresolved at the time of |
|               |        | this claim, so it is treated as held. Do not bind, migrate or query.  |

## Why 55433 / 56380

They are the next free ports above the acceptance stack, so a stray connection
string that is off by one digit fails to connect rather than succeeding against
somebody else's database. Both are created empty, migrated from scratch, and
destroyed at the end of the run — nothing in them outlives the exercise.
