-- Entries moved from a local store to a hosted one keep where they came from (decision 0031).
ALTER TABLE entries ADD COLUMN imported_from jsonb CHECK (
    imported_from IS NULL OR (imported_from ? 'local_entry_id' AND imported_from ? 'created_at')
);
