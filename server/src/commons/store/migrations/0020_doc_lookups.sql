-- Context7 documentation lookups the PostToolUse hook saw (MIT-622). One row per query-docs
-- call. The library id and version come from the tool input; the tool's response is never
-- read. The query is redacted and kept only in full_trace mode; the check enforces it.
CREATE TABLE doc_lookups (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    task_id uuid NOT NULL REFERENCES tasks (id),
    reporter_id uuid NOT NULL REFERENCES reporters (id),
    model_id uuid REFERENCES models (id),  -- the task's model; NULL until hooks see it
    privacy_mode privacy_default NOT NULL,
    library_id text NOT NULL,  -- /org/project
    version text NOT NULL,  -- the id's third segment, '' when it has none
    query text,
    excluded boolean NOT NULL DEFAULT false,
    CHECK (privacy_mode = 'full_trace' OR query IS NULL)
);
CREATE INDEX doc_lookups_created_idx ON doc_lookups (created_at);
CREATE INDEX doc_lookups_task_idx ON doc_lookups (task_id);
CREATE TRIGGER doc_lookups_set_updated_at BEFORE UPDATE ON doc_lookups
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Counts read this view, never the table, so excluded rows (test keys) drop out (0030).
CREATE VIEW counted_doc_lookups AS SELECT * FROM doc_lookups WHERE NOT excluded;
