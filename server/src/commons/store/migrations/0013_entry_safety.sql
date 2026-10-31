-- Entry safety (decision 0029): what the risk scanner found, per-key rate counters, and
-- revoke-and-purge records that make a purge reversible.
ALTER TABLE entries
    ADD COLUMN risk_report jsonb NOT NULL DEFAULT '{"scanned": false, "findings": []}';

CREATE TABLE rate_counts (
    key_id uuid NOT NULL REFERENCES api_keys (id),
    action text NOT NULL,
    window_start timestamptz NOT NULL,
    count integer NOT NULL CHECK (count >= 0),
    PRIMARY KEY (key_id, action, window_start)
);

CREATE TABLE purges (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    reporter_id uuid NOT NULL REFERENCES reporters (id),
    purged_by text NOT NULL,
    reason text NOT NULL,
    keys uuid[] NOT NULL DEFAULT '{}',
    retired_entries uuid[] NOT NULL DEFAULT '{}',
    correction_batch uuid,
    restored_at timestamptz,
    restored_by text
);
CREATE TRIGGER purges_set_updated_at BEFORE UPDATE ON purges
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- A suspended reporter's pending entries leave the queue until they are restored.
ALTER TABLE reporters ADD COLUMN suspended_at timestamptz;
