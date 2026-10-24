-- Initial schema: DESIGN.md §3, with reviews (decision 0005) and cq names (decision 0008).
-- Every table has id, created_at, updated_at. updated_at is maintained by a trigger.

CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- array_to_string is only STABLE, which generated columns reject. Joining text[] with a
-- constant separator is immutable in practice.
CREATE FUNCTION immutable_array_to_string(text[]) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT array_to_string($1, ' ') $$;

CREATE TYPE entry_status AS ENUM ('pending', 'approved', 'rejected', 'retired');
CREATE TYPE entry_privacy_mode AS ENUM ('entry', 'full_trace');
CREATE TYPE privacy_default AS ENUM ('local', 'entry', 'full_trace');
CREATE TYPE usage AS ENUM ('yes', 'partial', 'no');
CREATE TYPE outcome_kind AS ENUM (
    'tests_passed', 'tests_failed', 'error_resolved', 'error_persisted',
    'user_accepted', 'user_rejected', 'abandoned', 'unknown'
);
CREATE TYPE review_decision AS ENUM ('approve', 'reject');
CREATE TYPE review_source AS ENUM ('agent', 'human');

CREATE TABLE reporters (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    pseudonym text NOT NULL UNIQUE,
    -- NULL until the nightly job has outcomes to compute it from.
    reliability double precision CHECK (reliability BETWEEN 0 AND 1),
    privacy_default privacy_default NOT NULL DEFAULT 'entry',
    api_key_hash text UNIQUE
);

CREATE TABLE models (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    name text NOT NULL,
    version text NOT NULL DEFAULT '',
    provider text NOT NULL DEFAULT '',
    UNIQUE (provider, name, version)
);

CREATE TABLE entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    symptom text NOT NULL,
    environment jsonb NOT NULL CHECK (jsonb_typeof(environment) = 'object'),
    cause text NOT NULL,
    fix text NOT NULL,
    repro text,
    domains text[] NOT NULL CHECK (cardinality(domains) BETWEEN 1 AND 16),
    status entry_status NOT NULL DEFAULT 'pending',
    human_reviewed boolean NOT NULL DEFAULT false,
    created_by uuid NOT NULL REFERENCES reporters (id),
    created_by_model uuid NOT NULL REFERENCES models (id),
    privacy_mode entry_privacy_mode NOT NULL DEFAULT 'entry',
    trace_id uuid,  -- foreign key added after traces exists
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english'::regconfig, symptom), 'A')
        || setweight(to_tsvector('english'::regconfig, immutable_array_to_string(domains)), 'B')
        || setweight(to_tsvector('english'::regconfig, cause), 'B')
        || setweight(to_tsvector('english'::regconfig, fix), 'C')
    ) STORED
);
CREATE INDEX entries_search_vector_idx ON entries USING gin (search_vector);
CREATE INDEX entries_status_idx ON entries (status);

CREATE TABLE traces (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    entry_id uuid REFERENCES entries (id),
    task_id uuid,
    content text NOT NULL,  -- redacted before write
    redaction_report jsonb NOT NULL DEFAULT '{}',
    CHECK (entry_id IS NOT NULL OR task_id IS NOT NULL)
);
ALTER TABLE entries ADD FOREIGN KEY (trace_id) REFERENCES traces (id);

CREATE TABLE reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    entry_id uuid NOT NULL REFERENCES entries (id),
    reviewed_by uuid NOT NULL REFERENCES reporters (id),
    decision review_decision NOT NULL,
    reason text,
    source review_source NOT NULL
);
CREATE INDEX reviews_entry_id_idx ON reviews (entry_id, created_at);

CREATE TABLE retrievals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    entry_id uuid NOT NULL REFERENCES entries (id),
    reporter_id uuid NOT NULL REFERENCES reporters (id),
    model_id uuid NOT NULL REFERENCES models (id),
    task_id uuid NOT NULL,
    query text NOT NULL,
    rank_position integer NOT NULL CHECK (rank_position >= 1),
    score_at_retrieval double precision NOT NULL
);
CREATE INDEX retrievals_task_id_idx ON retrievals (task_id);
CREATE INDEX retrievals_entry_id_idx ON retrievals (entry_id, created_at);

CREATE TABLE outcomes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    -- One outcome per retrieval; hooks and the agent's report merge into it.
    retrieval_id uuid NOT NULL UNIQUE REFERENCES retrievals (id),
    used usage,  -- NULL means unknown: no hooks observed this task
    outcome outcome_kind NOT NULL DEFAULT 'unknown',
    evidence jsonb NOT NULL DEFAULT '{}',
    agent_assessment text,
    tokens_in integer CHECK (tokens_in >= 0),
    tokens_out integer CHECK (tokens_out >= 0),
    wall_clock_seconds double precision CHECK (wall_clock_seconds >= 0)
);

CREATE TABLE counterfactual_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    task_id uuid NOT NULL,
    entry_id uuid NOT NULL REFERENCES entries (id),
    with_entry boolean NOT NULL,
    outcome outcome_kind NOT NULL,
    tokens_in integer CHECK (tokens_in >= 0),
    tokens_out integer CHECK (tokens_out >= 0),
    wall_clock_seconds double precision CHECK (wall_clock_seconds >= 0),
    UNIQUE (task_id, entry_id, with_entry)
);

CREATE TABLE ranking_snapshots (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    entry_id uuid NOT NULL REFERENCES entries (id),
    computed_at timestamptz NOT NULL,
    score double precision NOT NULL,
    retrieval_count_30d integer NOT NULL CHECK (retrieval_count_30d >= 0),
    -- NULL when there were no retrievals or used outcomes to compute a rate from.
    used_rate double precision CHECK (used_rate BETWEEN 0 AND 1),
    success_rate double precision CHECK (success_rate BETWEEN 0 AND 1)
);
CREATE INDEX ranking_snapshots_entry_idx ON ranking_snapshots (entry_id, computed_at DESC);

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'reporters', 'models', 'entries', 'traces', 'reviews', 'retrievals', 'outcomes',
        'counterfactual_runs', 'ranking_snapshots'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION set_updated_at()',
            t || '_set_updated_at', t
        );
    END LOOP;
END;
$$;
