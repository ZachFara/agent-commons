-- Entry lifecycle (decision 0026): every version of an entry's text, supersession, and
-- retirement with the version that fixed the problem. Nothing is deleted.
ALTER TYPE review_decision ADD VALUE 'retire';
ALTER TYPE review_decision ADD VALUE 'restore';

ALTER TABLE entries
    ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    ADD COLUMN superseded_by uuid REFERENCES entries (id) CHECK (superseded_by <> id),
    ADD COLUMN fixed_in jsonb CHECK (
        fixed_in IS NULL OR (fixed_in ? 'library' AND fixed_in ? 'version')
    );

CREATE TABLE entry_revisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    entry_id uuid NOT NULL REFERENCES entries (id),
    revision integer NOT NULL CHECK (revision >= 1),
    symptom text NOT NULL,
    environment jsonb NOT NULL,
    cause text NOT NULL,
    fix text NOT NULL,
    repro text,
    domains text[] NOT NULL,
    source text NOT NULL CHECK (source IN ('proposal', 'human', 'agent')),
    edited_by text,  -- pseudonym of whoever edited; NULL for the original proposal
    reason text,
    redaction_report jsonb,
    UNIQUE (entry_id, revision)
);
CREATE TRIGGER entry_revisions_set_updated_at BEFORE UPDATE ON entry_revisions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

INSERT INTO entry_revisions (entry_id, revision, symptom, environment, cause, fix, repro,
    domains, source, redaction_report)
SELECT id, 1, symptom, environment, cause, fix, repro, domains, 'proposal', redaction_report
FROM entries;

-- A proposed entry is its own first revision.
CREATE FUNCTION record_first_revision() RETURNS trigger AS $$
BEGIN
    INSERT INTO entry_revisions (entry_id, revision, symptom, environment, cause, fix, repro,
        domains, source, redaction_report)
    VALUES (NEW.id, NEW.revision, NEW.symptom, NEW.environment, NEW.cause, NEW.fix, NEW.repro,
        NEW.domains, 'proposal', NEW.redaction_report);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER entries_first_revision AFTER INSERT ON entries
    FOR EACH ROW EXECUTE FUNCTION record_first_revision();

-- Which revision a retrieval showed, so outcomes credit the text the agent actually saw.
ALTER TABLE retrievals ADD COLUMN revision integer;
UPDATE retrievals SET revision = 1;
