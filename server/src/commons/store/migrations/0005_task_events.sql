-- Derived observations from hooks (decision 0016). Never code content: commands are scrubbed,
-- file changes are hashes. Edit text and raw commands stay in the local spool.
CREATE TYPE task_event_kind AS ENUM ('test_run', 'file_change');
CREATE TABLE task_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    task_id uuid NOT NULL REFERENCES tasks (id),
    kind task_event_kind NOT NULL,
    data jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX task_events_task_idx ON task_events (task_id, kind, created_at);
CREATE TRIGGER task_events_set_updated_at BEFORE UPDATE ON task_events
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
