-- The text the installed hook client shows (MIT-609): its SessionStart sentence, its Stop
-- report reminder, and the variant's start line and nudge, hashed into a `hooks` version.
-- A task points at the hook version its SessionStart sent; tasks from older clients, from
-- test keys and from before this migration stay NULL.
CREATE TYPE prompt_kind AS ENUM ('server', 'hooks');
ALTER TABLE prompt_versions ADD COLUMN kind prompt_kind NOT NULL DEFAULT 'server';

-- A hosted client sends only the first 12 hex characters of its hash. The server labels it
-- from the hook texts it knows; one it doesn't know is stored as sent, with no surfaces,
-- because the server never stores text a client sent.
ALTER TABLE prompt_versions ALTER COLUMN surfaces DROP NOT NULL;
ALTER TABLE prompt_versions DROP CONSTRAINT prompt_versions_hash_check;
ALTER TABLE prompt_versions ADD CONSTRAINT prompt_versions_hash_check CHECK (
    (hash ~ '^[0-9a-f]{64}$' AND surfaces IS NOT NULL)
    OR (kind = 'hooks' AND hash ~ '^[0-9a-f]{12}$' AND surfaces IS NULL)
);

ALTER TABLE tasks ADD COLUMN hook_prompt_version_id uuid REFERENCES prompt_versions (id);
CREATE INDEX tasks_hook_prompt_version_idx ON tasks (hook_prompt_version_id);
