-- The agent-facing text a server served (decision 2026-11-07-MIT-566-prompt-versions): one
-- row per distinct hash of the instructions, tool definitions, prompts and notes. Searches,
-- their retrievals and proposed entries point at the version that was served; rows written
-- outside the MCP tools (imports, seeds, rows from before this migration) stay NULL.
CREATE TABLE prompt_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    hash text NOT NULL UNIQUE CHECK (hash ~ '^[0-9a-f]{64}$'),  -- SHA-256 of the surfaces
    label text NOT NULL CHECK (length(label) <= 120),  -- the variant that first served it
    surfaces jsonb NOT NULL  -- {surface: text}
);
CREATE TRIGGER prompt_versions_set_updated_at BEFORE UPDATE ON prompt_versions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE searches ADD COLUMN prompt_version_id uuid REFERENCES prompt_versions (id);
ALTER TABLE retrievals ADD COLUMN prompt_version_id uuid REFERENCES prompt_versions (id);
ALTER TABLE entries ADD COLUMN prompt_version_id uuid REFERENCES prompt_versions (id);
CREATE INDEX searches_prompt_version_idx ON searches (prompt_version_id);
CREATE INDEX retrievals_prompt_version_idx ON retrievals (prompt_version_id);
CREATE INDEX entries_prompt_version_idx ON entries (prompt_version_id);

-- `SELECT *` in a view is expanded when the view is created, so the counted_ views from 0010
-- would not show columns added since. Re-expanding them appends the new columns.
CREATE OR REPLACE VIEW counted_retrievals AS SELECT * FROM retrievals WHERE NOT excluded;
CREATE OR REPLACE VIEW counted_searches AS SELECT * FROM searches WHERE NOT excluded;
