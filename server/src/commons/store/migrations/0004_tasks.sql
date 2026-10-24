-- One task per agent session (decision 0016). id is the task_id agents pass to tools.
CREATE TABLE tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    session_id text NOT NULL UNIQUE,
    reporter_id uuid NOT NULL REFERENCES reporters (id),
    model_id uuid REFERENCES models (id),  -- observed by hooks; NULL until seen
    privacy_mode privacy_default NOT NULL
);
CREATE TRIGGER tasks_set_updated_at BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
