-- STAGING bootstrap: a migration OWNER and a restricted RUNTIME role, plus the application database.
--
-- Why this exists: a managed Postgres hands you a superuser. Every row-level-security policy is vacuous
-- for a superuser (and for any BYPASSRLS role), and the application does NOT check this at startup. The
-- API must therefore connect as a NOSUPERUSER, NOBYPASSRLS runtime role, and migrations must run as a
-- separate owner role. This mirrors infra/postgres/local-roles.sql and security-test-init.sql.
--
-- Run ONCE per staging database, as the superuser, connected to any existing database (for example the
-- default one Railway creates). It is safe to re-run: every statement checks before it creates.
--
--   psql "<superuser connection string>" -v ON_ERROR_STOP=1 -f infra/postgres/staging-roles.sql
--
-- Passwords come from the environment of the shell running psql and are never written to this file:
--   SOURCEMIND_STAGING_OWNER_PASSWORD, SOURCEMIND_STAGING_RUNTIME_PASSWORD
-- Use long random values (for example `openssl rand -base64 32`), and do not reuse any other password.
--
--   sourcemind_owner     owns the database and the public schema; runs `alembic upgrade head`
--                        (with SOURCEMIND_RUNTIME_ROLE=sourcemind_runtime in its environment).
--   sourcemind_runtime   what the API connects as: DML through the default privileges below,
--                        NOSUPERUSER, NOBYPASSRLS.
--   sourcemind_staging   the application database.

\getenv owner_password SOURCEMIND_STAGING_OWNER_PASSWORD
\getenv runtime_password SOURCEMIND_STAGING_RUNTIME_PASSWORD

SELECT format('CREATE ROLE sourcemind_owner LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS',
              :'owner_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sourcemind_owner') \gexec

SELECT format('CREATE ROLE sourcemind_runtime LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS',
              :'runtime_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sourcemind_runtime') \gexec

ALTER ROLE sourcemind_owner SET timezone = 'UTC';
ALTER ROLE sourcemind_runtime SET timezone = 'UTC';

SELECT 'CREATE DATABASE sourcemind_staging OWNER sourcemind_owner'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'sourcemind_staging') \gexec

\connect sourcemind_staging

-- Extensions are created HERE, as the superuser: the restricted owner role cannot create `vector` and
-- migration 0001 only runs CREATE EXTENSION IF NOT EXISTS (a no-op once they exist).
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

ALTER SCHEMA public OWNER TO sourcemind_owner;
REVOKE ALL ON DATABASE sourcemind_staging FROM PUBLIC;
GRANT CONNECT ON DATABASE sourcemind_staging TO sourcemind_runtime;
GRANT USAGE ON SCHEMA public TO sourcemind_runtime;

-- Applies to tables the OWNER creates during `alembic upgrade head`. Migrations from 20261005_0010 also
-- grant explicitly to the role named by SOURCEMIND_RUNTIME_ROLE.
ALTER DEFAULT PRIVILEGES FOR ROLE sourcemind_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sourcemind_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE sourcemind_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO sourcemind_runtime;

-- Proof printed at the end: both roles must show f / f (not superuser, cannot bypass RLS).
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles
WHERE rolname IN ('sourcemind_owner', 'sourcemind_runtime')
ORDER BY rolname;
