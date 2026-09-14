# End-to-end conflict-resolution exercise

Runs `src/pages/conflictResolution.e2e.test.js` against a real API and a real
PostgreSQL, and asserts on committed rows rather than on HTTP responses.

It exists because every earlier check of this feature asserted against a belief
about the backend, and two of those beliefs were wrong — a note Pydantic
dropped, and a split rule that did not exist. Neither produced a failure
anywhere.

In CI this is the `integration` job of `.github/workflows/web-ci.yml`. Locally
it is the same suite with different connection settings.

The suite skips itself unless `E2E_API_URL` is set, so `npm test` and the unit
CI job are unaffected. **A skipped run still exits 0**, which is why CI does not
trust the exit status — see "The gate" below.

## What it does and does not prove

Real: the page component, its validation, its payload construction, its error
handling, the `realApi` adapter, the API, the resolver, and PostgreSQL with RLS
in force under a `NOSUPERUSER NOBYPASSRLS` role.

Not real, and therefore not evidence:

- **No browser.** jsdom renders the component and `fetch` is a shim over Node's
  `http`. CSS, layout and real event dispatch are untested.
- **No Clerk — auth stub.** The API runs with `AUTH_DEV_BYPASS_ENABLED`, the
  backend's own validated development-only flag (`config.py` refuses it outside
  `ENVIRONMENT=development`). It pins the identity `seed_base.sql` inserts.
  Token verification is covered by the API's unit tests, not by this job.
- **Embeddings are stubbed — provider stub.** `openai_stub.py` answers the one
  external model call in this suite (the `merged` path embeds the merged
  memory) with a deterministic vector, so nothing leaves the machine. It proves
  the call is made and the result stored; it proves nothing about embedding
  quality, and conflict *detection*, which needs real embeddings, is out of
  scope entirely.

## Configuration

Everything is environment-driven. `psqlClient.js` reads:

| Variable | Meaning |
|---|---|
| `E2E_ALLOW_DISPOSABLE` | Must be `1`. Without it the client refuses to run. |
| `E2E_PG_HOST` / `E2E_PG_PORT` | Default `127.0.0.1` / `5432`. |
| `E2E_PG_USER` | Required. |
| `E2E_PG_PASSWORD` | Optional; omit for trust/peer auth. |
| `E2E_PG_DATABASE` | Required, and must satisfy the guard below. |
| `E2E_PSQL` | psql executable. Default `psql`. |
| `E2E_PSQL_MODE` | `direct` or `wsl`. Defaults by platform. |

and the suite itself reads `E2E_API_URL`, `REACT_APP_USE_MOCKS=false` and
`REACT_APP_BACKEND_URL`.

### The disposable-target guard

The seed issues `DELETE FROM memories` and `DELETE FROM memory_conflicts` for
its workspace. Until this harness became portable, the only thing keeping that
away from real data was that the connection was hardcoded to one throwaway
cluster. That accident is gone, so the guard is now explicit and fails closed:

- `E2E_ALLOW_DISPOSABLE` must be exactly `1`;
- the database name must look disposable (`e2e`, `test`, `ci`, `scratch`, …);
- a name containing `prod`, `live`, `staging`, `main` or `master` is refused
  **whatever the flag says**.

It is unit-tested in `src/lib/psqlGuard.test.js`, which runs in the ordinary
unit suite on every machine with no services. A guard nothing tests is a guard
nobody knows is broken.

### Secret handling

The password is passed to psql through `PGPASSWORD` in the child's environment
and never appears in argv, where `ps` would expose it to every process on the
host. Anything thrown from the client is scrubbed, because psql echoes the
connection it attempted and `execFileSync` attaches the full command line.
Both properties are asserted in `psqlGuard.test.js`.

### `direct` vs `wsl`

`direct` runs `psql` as a child process — what CI does, and what any machine
with a psql client should do. `wsl` routes through `wsl.exe` for a Windows host
whose PostgreSQL lives inside WSL. The default is by platform; set the variable
to make it a decision rather than a guess.

> **Running from Git Bash on Windows:** MSYS rewrites environment values that
> look like absolute Unix paths, so `E2E_PSQL=/usr/lib/postgresql/16/bin/psql`
> arrives as `C:/Program Files/Git/usr/lib/...` and psql is "not found". Either
> leave `E2E_PSQL` unset (the WSL guest has `psql` on `PATH`) or set
> `MSYS_NO_PATHCONV=1`. This is a property of the shell, not of the client.

## Running it locally

1. **Disposable PostgreSQL 16 + Redis** on ports nobody else holds. Claim a
   port pair in `docs/handoff/RESOURCE_LEASES.md` first, and never point this
   at shared development data.

2. **Provision** with the same file CI uses:

   ```bash
   psql -h 127.0.0.1 -p <port> -U <superuser> -d postgres -v ON_ERROR_STOP=1 \
        -v owner_password="$OWNER_PW" -v runtime_password="$RUNTIME_PW" \
        -f apps/web/e2e/init_roles.sql
   ```

3. **Migrate as the owner role** — never as a superuser, or RLS is bypassed and
   every isolation property becomes vacuously true:

   ```bash
   cd apps/api
   DATABASE_URL=postgresql+asyncpg://sourcemind_owner:PW@127.0.0.1:<port>/sourcemind_e2e \
     python -m alembic upgrade head
   ```

4. **Seed.** `seed_base.sql` once (organization, dev-bypass identity,
   workspace, active membership), then `seed_conflicts.sql`, which is
   re-runnable and recreates one conflict per resolution type.

5. **Start the stub and the API:**

   ```bash
   python apps/web/e2e/openai_stub.py 8402

   cd apps/api
   DATABASE_URL=postgresql+asyncpg://sourcemind_test:PW@127.0.0.1:<port>/sourcemind_e2e \
   REDIS_URL=redis://127.0.0.1:<redis>/0 \
   ENVIRONMENT=development AUTH_DEV_BYPASS_ENABLED=true \
   OPENAI_API_KEY=stub-key-not-a-real-credential \
   OPENAI_BASE_URL=http://127.0.0.1:8402/v1 \
     python -m uvicorn sourcemind.main:app --host 127.0.0.1 --port 8010
   ```

6. **Run**, supplying the connection settings from the table above:

   ```bash
   cd apps/web
   CI=true E2E_API_URL=http://127.0.0.1:8010 \
   REACT_APP_USE_MOCKS=false REACT_APP_BACKEND_URL=http://127.0.0.1:8010 \
   E2E_ALLOW_DISPOSABLE=1 E2E_PG_HOST=127.0.0.1 E2E_PG_PORT=<port> \
   E2E_PG_USER=<user> E2E_PG_PASSWORD=<pw> E2E_PG_DATABASE=sourcemind_e2e \
     npx craco test --watchAll=false --runInBand \
       --testPathPattern conflictResolution.e2e
   ```

`--runInBand` is required: the cases share fixtures and read the database
between steps.

## The gate

Jest exits 0 when every test is skipped, and a suite that skips itself on
missing configuration is exactly what a broken CI job produces. So CI ignores
the exit status and runs `.github/scripts/check_web_integration_report.py`
over the `--json` report instead. It fails unless every test in
`.github/scripts/expected_web_integration_tests.txt` is present, absent from
nowhere, and `passed` — `pending` is a failure.

Regenerate that manifest deliberately when adding a test. Never to make a red
build green.

## The defect this run found, and its fix

The `deferred` branch of `resolve_conflict` used to return before the shared
UPDATE, so the resolution note the form collects for every action was accepted,
answered 200, and dropped — along with `resolver_id`. Reported in
`docs/handoff/DEFECT-deferred-resolution-note-discarded.md`, fixed by Codex in
`04babab`, recorded as D-010.

The suite now asserts the corrected behaviour: the note and the deferring
user's UUID persist, `resolved_at` stays NULL, `revisit_at` is unchanged, and
`blocks_derivation` is untouched.

### A note on boolean assertions

Under `psql -At`, a bare boolean renders as `t` / `f`, but `(TRUE)::text`
renders as `true`, and a **NULL boolean renders as the empty string, not `f`**.
Assertions written against that rendering therefore conflate "false" with
"unknown" as soon as the expression changes. Cases that care about nullness
select an explicit sentinel (`IS_NULL` / `IS_SET`) instead, and one test pins
the rendering itself so a psql change cannot silently alter what the others
mean.
