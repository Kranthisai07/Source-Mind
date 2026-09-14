# End-to-end conflict-resolution exercise

Runs `src/pages/conflictResolution.e2e.test.js` against a real API and a real
PostgreSQL, and asserts on committed rows rather than on HTTP responses.

It exists because every earlier check of this feature asserted against a belief
about the backend, and two of those beliefs were wrong — a note Pydantic
dropped, and a split rule that did not exist. Neither produced a failure
anywhere.

The suite is skipped unless `E2E_API_URL` is set, so `npm test` and CI are
unaffected.

## What it does and does not prove

Real: the page component, its validation, its payload construction, its error
handling, the `realApi` adapter, the API, the resolver, and PostgreSQL with RLS
in force under a `NOSUPERUSER NOBYPASSRLS` role.

Not real, and therefore not evidence:

- **No browser.** jsdom renders the component and `fetch` is a shim over Node's
  `http`. CSS, layout and real event dispatch are untested.
- **No Clerk.** The API runs with the validated development-only
  `AUTH_DEV_BYPASS_ENABLED` flag. Token handling is not covered.
- **Embeddings are stubbed** by `openai_stub.py`. That proves the `merged` path
  calls the model and stores the result. It proves nothing about embedding
  quality, and conflict *detection*, which depends on real embeddings, is out
  of scope.

## Resources

Claim a port pair in `docs/handoff/RESOURCE_LEASES.md` before starting, and
never point this at shared development data — it truncates the workspace it
uses. The run below is what was used on 2026-09-14, on ports 55433/56380.

## Running it

1. **Disposable PostgreSQL 16 + Redis**, on ports nobody else holds. Create the
   roles the way `infra/postgres/security-test-init.sql` does, so the API runs
   as a role that cannot bypass RLS:

   ```sql
   CREATE ROLE sourcemind_owner LOGIN PASSWORD :'owner_password'
       NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
   CREATE ROLE sourcemind_test  LOGIN PASSWORD :'runtime_password'
       NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
   CREATE DATABASE sourcemind_e2e OWNER sourcemind_owner;
   ```

   Then, as a superuser on that database: `CREATE EXTENSION` for `vector`,
   `uuid-ossp`, `pg_trgm`, `btree_gin`, plus the `GRANT`s and
   `ALTER DEFAULT PRIVILEGES` from that same init file.

2. **Migrate as the owner role:**

   ```bash
   cd apps/api
   DATABASE_URL=postgresql+asyncpg://sourcemind_owner:PW@127.0.0.1:55433/sourcemind_e2e \
     python -m alembic upgrade head
   ```

3. **Seed.** `seed_conflicts.sql` assumes an organization, the dev-bypass user
   `00000000-0000-4000-8000-000000000001`, workspace `…a0`, and an active
   membership already exist. It is re-runnable: it wipes and recreates every
   fixture, one conflict per resolution type.

4. **Start the embedding stub** (no outbound network call is made):

   ```bash
   python apps/web/e2e/openai_stub.py 8402
   ```

5. **Start the API as the runtime role:**

   ```bash
   cd apps/api
   DATABASE_URL=postgresql+asyncpg://sourcemind_test:PW@127.0.0.1:55433/sourcemind_e2e \
   REDIS_URL=redis://127.0.0.1:56380/0 \
   ENVIRONMENT=development AUTH_DEV_BYPASS_ENABLED=true \
   OPENAI_API_KEY=stub-key-not-a-real-credential \
   OPENAI_BASE_URL=http://127.0.0.1:8402/v1 \
     python -m uvicorn sourcemind.main:app --host 127.0.0.1 --port 8010
   ```

6. **Run:**

   ```bash
   cd apps/web
   CI=true E2E_API_URL=http://127.0.0.1:8010 \
   REACT_APP_USE_MOCKS=false REACT_APP_BACKEND_URL=http://127.0.0.1:8010 \
     npx craco test --watchAll=false --runInBand \
       --testPathPattern=conflictResolution.e2e
   ```

`--runInBand` is required: the cases share fixtures and read the database
between steps.

## The `sql()` helper

It shells out through `wsl.exe` to `psql`, which is specific to the machine
this was written on. On a host where `psql` is on `PATH` directly, replace the
body of that helper; nothing else in the suite is environment-specific.

## One case asserts a bug on purpose

`KNOWN DEFECT: the note is silently discarded on defer` pins the *current*
behaviour so the defect is recorded rather than hidden. See
`docs/handoff/DEFECT-deferred-resolution-note-discarded.md`. When the backend
is fixed, that test fails, and it should then be inverted to expect the note.
