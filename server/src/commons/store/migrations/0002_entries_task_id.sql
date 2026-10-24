-- The session that proposed the entry (owner-approved, 2026-10-24). NULL for imported entries.
ALTER TABLE entries ADD COLUMN task_id uuid;
