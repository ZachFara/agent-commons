-- Doc lookups follow corrections and retention like searches (MIT-623, decisions 0030, 0033).
-- Corrections may now target doc_lookups (only `excluded`, which the domain enforces).
ALTER TABLE corrections DROP CONSTRAINT corrections_target_table_check;
ALTER TABLE corrections ADD CONSTRAINT corrections_target_table_check
    CHECK (target_table IN ('outcomes', 'retrievals', 'searches', 'doc_lookups'));

-- Lookups older than the retention window are folded into monthly counts by library and
-- version, then deleted. No query or reporter is kept here.
CREATE TABLE doc_lookup_months (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    month date NOT NULL,
    library_id text NOT NULL,
    version text NOT NULL,
    lookups integer NOT NULL CHECK (lookups >= 1),
    UNIQUE (month, library_id, version)
);
CREATE TRIGGER doc_lookup_months_set_updated_at BEFORE UPDATE ON doc_lookup_months
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
