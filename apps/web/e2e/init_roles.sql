-- Provision the disposable integration database.
--
-- Deliberately mirrors infra/postgres/security-test-init.sql rather than
-- reusing it: that file creates `sourcemind_security_test` for the backend
-- acceptance gate, and sharing one database between two gates would mean each
-- could truncate the other's fixtures. The role SHAPE is what matters and is
-- copied exactly — neither role may bypass RLS, so the integration run
-- exercises the same policy engine production does instead of a superuser
-- connection that makes every policy vacuous.
--
-- Run as the cluster superuser. Expects two psql variables:
--   :owner_password    owns the schema, runs migrations, has DDL rights
--   :runtime_password  what the API connects as: DML only, no DDL

CREATE ROLE sourcemind_owner LOGIN PASSWORD :'owner_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
CREATE ROLE sourcemind_test LOGIN PASSWORD :'runtime_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;

ALTER ROLE sourcemind_owner SET timezone = 'UTC';
ALTER ROLE sourcemind_test  SET timezone = 'UTC';

CREATE DATABASE sourcemind_e2e OWNER sourcemind_owner;

\connect sourcemind_e2e

-- Extensions need superuser, so they are created here rather than by the
-- owner role during migration.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

GRANT CONNECT ON DATABASE sourcemind_e2e TO sourcemind_test;
GRANT USAGE ON SCHEMA public TO sourcemind_test;

-- Applies to tables the OWNER creates during `alembic upgrade head`, which is
-- why this must be set before migrating rather than after.
ALTER DEFAULT PRIVILEGES FOR ROLE sourcemind_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sourcemind_test;
ALTER DEFAULT PRIVILEGES FOR ROLE sourcemind_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO sourcemind_test;
