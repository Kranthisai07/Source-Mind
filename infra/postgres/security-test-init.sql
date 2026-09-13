CREATE ROLE sourcemind_owner LOGIN PASSWORD 'sourcemind_owner'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
CREATE ROLE sourcemind_test LOGIN PASSWORD 'sourcemind_test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
ALTER ROLE sourcemind_owner SET timezone = 'UTC';
ALTER ROLE sourcemind_test SET timezone = 'UTC';
CREATE DATABASE sourcemind_security_test OWNER sourcemind_owner;

\connect sourcemind_security_test

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

GRANT CONNECT ON DATABASE sourcemind_security_test TO sourcemind_test;
GRANT USAGE ON SCHEMA public TO sourcemind_test;

ALTER DEFAULT PRIVILEGES FOR ROLE sourcemind_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sourcemind_test;
ALTER DEFAULT PRIVILEGES FOR ROLE sourcemind_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO sourcemind_test;
