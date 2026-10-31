-- Every search, including ones that found nothing (decision 0024). The query is redacted.
-- environment and results are kept only in full_trace mode; the check enforces it.
CREATE TABLE searches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    task_id uuid NOT NULL,
    reporter_id uuid NOT NULL REFERENCES reporters (id),
    model_id uuid NOT NULL REFERENCES models (id),
    privacy_mode privacy_default NOT NULL,
    query text NOT NULL,
    language text,
    result_limit integer NOT NULL CHECK (result_limit >= 1),
    hit_count integer NOT NULL CHECK (hit_count >= 0),
    environment jsonb,
    results jsonb,  -- [{entry_id, rank, score}]
    CHECK (privacy_mode = 'full_trace' OR (environment IS NULL AND results IS NULL))
);
CREATE INDEX searches_created_idx ON searches (created_at);
CREATE INDEX searches_task_idx ON searches (task_id);
CREATE TRIGGER searches_set_updated_at BEFORE UPDATE ON searches
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
