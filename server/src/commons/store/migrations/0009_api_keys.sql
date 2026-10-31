-- Several keys per reporter, each scoped, expirable and revocable (decision 0032). Only a
-- key's SHA-256 is stored, plus its first characters so a listing can identify it.
CREATE TYPE key_scope AS ENUM ('agent', 'review', 'admin');

CREATE TABLE api_keys (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    reporter_id uuid NOT NULL REFERENCES reporters (id),
    prefix text,  -- NULL for keys migrated from reporters, whose text was never stored
    key_hash text NOT NULL UNIQUE,
    scope key_scope NOT NULL,
    label text,
    last_used_at timestamptz,
    expires_at timestamptz,
    revoked_at timestamptz,
    test boolean NOT NULL DEFAULT false
);
CREATE INDEX api_keys_reporter_idx ON api_keys (reporter_id);
CREATE TRIGGER api_keys_set_updated_at BEFORE UPDATE ON api_keys
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Each reporter's single key becomes one api_keys row: admin if the reporter was an admin.
INSERT INTO api_keys (reporter_id, key_hash, scope, label)
SELECT id, api_key_hash, CASE WHEN is_admin THEN 'admin' ELSE 'agent' END::key_scope, 'migrated'
FROM reporters WHERE api_key_hash IS NOT NULL;

ALTER TABLE reporters DROP COLUMN api_key_hash, DROP COLUMN is_admin;
