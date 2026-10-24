-- What secret scanning removed before the entry was written (DESIGN.md §4), for reviewers.
ALTER TABLE entries ADD COLUMN redaction_report jsonb NOT NULL DEFAULT '{"scanned": false, "findings": []}';
