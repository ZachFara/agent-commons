-- Audited corrections and exclusions (decision 0030). Raw rows stay; a correction records who
-- changed what and why, and excluded rows drop out of every count through the counted_ views.
CREATE TABLE corrections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    batch_id uuid,  -- corrections made together (a whole task) are undone together
    corrected_by text NOT NULL,  -- reporter pseudonym, or who ran the CLI
    target_table text NOT NULL CHECK (target_table IN ('outcomes', 'retrievals', 'searches')),
    target_id uuid NOT NULL,
    field text NOT NULL CHECK (field IN ('used', 'outcome', 'excluded')),
    old_value jsonb,
    new_value jsonb,
    reason text NOT NULL CHECK (length(trim(reason)) > 0),
    undone_at timestamptz,
    undone_by text
);
CREATE INDEX corrections_target_idx ON corrections (target_table, target_id);
CREATE INDEX corrections_batch_idx ON corrections (batch_id);
CREATE TRIGGER corrections_set_updated_at BEFORE UPDATE ON corrections
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE retrievals ADD COLUMN excluded boolean NOT NULL DEFAULT false;
ALTER TABLE outcomes ADD COLUMN excluded boolean NOT NULL DEFAULT false;
ALTER TABLE searches ADD COLUMN excluded boolean NOT NULL DEFAULT false;

-- Every count, rate and score reads these, never the tables (a test checks the readers).
-- An outcome counts only if neither it nor its retrieval is excluded.
CREATE VIEW counted_retrievals AS SELECT * FROM retrievals WHERE NOT excluded;
CREATE VIEW counted_outcomes AS
    SELECT o.* FROM outcomes o JOIN retrievals r ON r.id = o.retrieval_id
    WHERE NOT o.excluded AND NOT r.excluded;
CREATE VIEW counted_searches AS SELECT * FROM searches WHERE NOT excluded;
