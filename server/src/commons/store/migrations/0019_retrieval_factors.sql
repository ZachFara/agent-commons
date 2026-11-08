-- The three factors of score_at_retrieval (relevance x version factor x outcome score), so a
-- poor match and a poor entry can be told apart afterwards. Older rows stay NULL.
ALTER TABLE retrievals
    ADD COLUMN relevance double precision,
    ADD COLUMN version_factor double precision,
    ADD COLUMN outcome_score double precision;

-- counted_retrievals is SELECT *, whose columns froze when 0010 created it.
CREATE OR REPLACE VIEW counted_retrievals AS SELECT * FROM retrievals WHERE NOT excluded;
