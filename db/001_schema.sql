-- Activity log schema (PostgreSQL 16).
--
-- Idempotent: safe to run more than once (docker-entrypoint-initdb.d, CI, integration tests).
-- Plain SQL only (no psql meta-commands) so drivers can execute it too.
--
-- The passwords below are LOCAL DEMO credentials for docker compose / CI only. A real deployment
-- creates the roles out of band (or uses IAM auth) and never ships passwords in a migration.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app') THEN
        CREATE ROLE app LOGIN PASSWORD 'app';            -- API: reads the grid, writes audit rows
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'exporter') THEN
        CREATE ROLE exporter LOGIN PASSWORD 'exporter';  -- worker: read-only
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS activity_event (
    event_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL,
    actor_id    TEXT        NOT NULL,  -- pseudonymous id; never a name or e-mail
    area        TEXT        NOT NULL
        CONSTRAINT activity_event_area_check
        CHECK (area IN ('accounts', 'billing', 'catalog', 'orders', 'settings')),
    action      TEXT        NOT NULL
        CONSTRAINT activity_event_action_check
        CHECK (action IN ('create', 'update', 'delete', 'view', 'export', 'sign_in')),
    entity_type TEXT        NOT NULL,
    entity_id   TEXT        NOT NULL,
    details     JSONB       NOT NULL DEFAULT '{}'  -- [{field, old, new}]; detailed exports only
);

COMMENT ON TABLE activity_event IS
    'Append-only activity log. UPDATE, DELETE and TRUNCATE are rejected by triggers.';

-- Keyset reads: WHERE (occurred_at, event_id) < (:ts, :id) ORDER BY occurred_at DESC, event_id DESC
CREATE INDEX IF NOT EXISTS ix_activity_area_time
    ON activity_event (area, occurred_at DESC, event_id DESC);
CREATE INDEX IF NOT EXISTS ix_activity_time
    ON activity_event (occurred_at DESC, event_id DESC);

-- Append-only enforcement. Privileges already deny UPDATE/DELETE/TRUNCATE to the app roles; the
-- triggers also stop the table owner, so "fixing" history requires dropping a trigger on purpose.
CREATE OR REPLACE FUNCTION activity_event_append_only() RETURNS trigger
    LANGUAGE plpgsql AS
$$
BEGIN
    RAISE EXCEPTION 'activity_event is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege',
              HINT = 'Record a new event instead of changing an existing one.';
END
$$;

CREATE OR REPLACE TRIGGER activity_event_no_update_delete
    BEFORE UPDATE OR DELETE ON activity_event
    FOR EACH ROW EXECUTE FUNCTION activity_event_append_only();

CREATE OR REPLACE TRIGGER activity_event_no_truncate
    BEFORE TRUNCATE ON activity_event
    FOR EACH STATEMENT EXECUTE FUNCTION activity_event_append_only();

-- Least privilege.
REVOKE ALL ON activity_event FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app, exporter;
GRANT SELECT, INSERT ON activity_event TO app;  -- identity columns need no sequence grant
GRANT SELECT ON activity_event TO exporter;
ALTER ROLE exporter SET default_transaction_read_only = on;
