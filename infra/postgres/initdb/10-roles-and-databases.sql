-- Roles and databases for Forge (spec 001, ADR-0003).
-- Run ONCE by the Postgres image entrypoint, as superuser, when the data volume is empty.
-- Passwords come from the container environment (psql >= 15 \getenv): no secret in this file.
-- Tables, RLS, policies, functions and grants are NOT created here: Alembic migrations do it
-- as forge_owner.

\set ON_ERROR_STOP on

\getenv owner_password FORGE_OWNER_PASSWORD
\getenv app_password FORGE_APP_PASSWORD

\if :{?owner_password}
\else
  \warn 'FORGE_OWNER_PASSWORD is not set'
  SELECT 1 / 0 AS forge_owner_password_missing;
\endif
\if :{?app_password}
\else
  \warn 'FORGE_APP_PASSWORD is not set'
  SELECT 1 / 0 AS forge_app_password_missing;
\endif

-- Owns the schema and every table. Used only by migrations. Not a superuser, no RLS bypass.
CREATE ROLE forge_owner LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
  PASSWORD :'owner_password';

-- The API's login role: owns nothing, subject to RLS, table-by-table grants (migration 0002).
CREATE ROLE forge_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
  PASSWORD :'app_password';

-- Owns the few read-only SECURITY DEFINER functions ("my memberships", "my invitations").
-- Cannot log in, does not bypass RLS: it has its own policies, limited to the current user.
CREATE ROLE forge_self_reader NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

-- Lets migrations (forge_owner) hand those functions over to forge_self_reader
-- (ALTER FUNCTION ... OWNER TO), without forge_owner inheriting its privileges.
GRANT forge_self_reader TO forge_owner WITH INHERIT FALSE, SET TRUE;

CREATE DATABASE forge OWNER forge_owner;
CREATE DATABASE forge_test OWNER forge_owner;
