-- Searches older than the retention window are folded into monthly counts, then deleted
-- (decision 0033). The query is already redacted when it reaches `searches`.
CREATE TABLE search_months (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    month date NOT NULL,
    query text NOT NULL,
    searches integer NOT NULL CHECK (searches >= 1),
    misses integer NOT NULL CHECK (misses >= 0 AND misses <= searches),
    UNIQUE (month, query)
);
CREATE TRIGGER search_months_set_updated_at BEFORE UPDATE ON search_months
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
