-- Duplicate resolution (decision 0027): likely duplicates found on propose, aliases that a
-- merge adds to the canonical entry, and pairs a reviewer decided are distinct.
ALTER TABLE entries
    ADD COLUMN aliases text[] NOT NULL DEFAULT '{}',
    ADD COLUMN duplicate_candidates jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(duplicate_candidates) = 'array'),
    ADD COLUMN duplicate_of uuid REFERENCES entries (id);

-- Search also matches a merged duplicate's wording (aliases weigh like the symptom).
DROP INDEX entries_search_vector_idx;
ALTER TABLE entries DROP COLUMN search_vector;
ALTER TABLE entries ADD COLUMN search_vector tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english'::regconfig, symptom), 'A')
    || setweight(to_tsvector('english'::regconfig, immutable_array_to_string(aliases)), 'A')
    || setweight(to_tsvector('english'::regconfig, immutable_array_to_string(domains)), 'B')
    || setweight(to_tsvector('english'::regconfig, cause), 'B')
    || setweight(to_tsvector('english'::regconfig, fix), 'C')
) STORED;
CREATE INDEX entries_search_vector_idx ON entries USING gin (search_vector);

CREATE TABLE distinct_pairs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    entry_a uuid NOT NULL REFERENCES entries (id),
    entry_b uuid NOT NULL REFERENCES entries (id),
    decided_by text NOT NULL,
    CHECK (entry_a < entry_b),
    UNIQUE (entry_a, entry_b)
);
CREATE TRIGGER distinct_pairs_set_updated_at BEFORE UPDATE ON distinct_pairs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
