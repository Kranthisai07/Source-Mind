-- Local development roles: a migration OWNER and a restricted RUNTIME role.
--
-- Mirrors infra/postgres/security-test-init.sql (CI) so local development
-- exercises the same policy engine as CI and production instead of a
-- superuser connection that makes every row-level-security policy vacuous.
--
-- Runs once, after 01_init.sql, when the postgres volume is first created,
-- as the container's bootstrap superuser (POSTGRES_USER). Passwords come
-- from the container environment (set in infra/docker-compose.yml with
-- local, env-overridable defaults) and are never written to this file.
--
--   sourcemind_owner    owns the database and the public schema; runs
--                       `alembic upgrade head` (the api service overrides
--                       DATABASE_URL with MIGRATION_DATABASE_URL for that).
--   sourcemind_runtime  what the api and worker connect as: DML only through
--                       the default privileges below, NOSUPERUSER, NOBYPASSRLS.
--
-- Existing volumes created before this file keep the old single superuser
-- role. Recreate them with `docker compose -f infra/docker-compose.yml down -v`
-- (this deletes local data).

\getenv owner_password SOURCEMIND_LOCAL_OWNER_PASSWORD
\getenv runtime_password SOURCEMIND_LOCAL_RUNTIME_PASSWORD

CREATE ROLE sourcemind_owner LOGIN PASSWORD :'owner_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
CREATE ROLE sourcemind_runtime LOGIN PASSWORD :'runtime_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
ALTER ROLE sourcemind_owner SET timezone = 'UTC';
ALTER ROLE sourcemind_runtime SET timezone = 'UTC';

-- The database created by the entrypoint (POSTGRES_DB) and its public schema.
ALTER DATABASE sourcemind OWNER TO sourcemind_owner;
ALTER SCHEMA public OWNER TO sourcemind_owner;

GRANT CONNECT ON DATABASE sourcemind TO sourcemind_runtime;
GRANT USAGE ON SCHEMA public TO sourcemind_runtime;

-- Applies to tables the OWNER creates during `alembic upgrade head`.
-- Migrations from 20261005_0010 also grant explicitly to the role named by
-- SOURCEMIND_RUNTIME_ROLE (sourcemind_runtime here).
ALTER DEFAULT PRIVILEGES FOR ROLE sourcemind_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sourcemind_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE sourcemind_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO sourcemind_runtime;
