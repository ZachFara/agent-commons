-- Review at scale (decision 0028): reporter roles, trust levels, provisional approval with
-- sampled review, and confirmation by observed outcomes.
CREATE TYPE reporter_role AS ENUM ('contributor', 'reviewer', 'admin');
ALTER TYPE review_source ADD VALUE 'auto';
ALTER TYPE review_decision ADD VALUE 'reopen';

ALTER TABLE reporters
    ADD COLUMN role reporter_role NOT NULL DEFAULT 'contributor',
    ADD COLUMN trust_level smallint NOT NULL DEFAULT 0 CHECK (trust_level BETWEEN 0 AND 2);
UPDATE reporters SET role = 'admin'
    WHERE id IN (SELECT reporter_id FROM api_keys WHERE scope = 'admin');
UPDATE reporters SET role = 'reviewer'
    WHERE role = 'contributor' AND id IN (SELECT reporter_id FROM api_keys WHERE scope = 'review');

ALTER TABLE entries
    ADD COLUMN provisional boolean NOT NULL DEFAULT false,  -- approved by trust, not a human
    ADD COLUMN sampled boolean NOT NULL DEFAULT false,  -- a provisional entry picked for review
    ADD COLUMN confirmed_at timestamptz;  -- enough observed successes from other reporters
