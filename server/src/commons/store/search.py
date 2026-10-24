from psycopg.rows import class_row

from commons.domain.search import SearchHit, SearchRequest
from commons.store.connection import Connection
from commons.store.outcome_stats import OUTCOME_STATS, OUTCOME_STATS_PARAMS

# Decision 0012. The OR query swaps plainto_tsquery's '&' for '|', so any shared word matches.
# Score = relevance x latest snapshot score (0.5 prior). Outcome stats are live until snapshots.
_SEARCH = """
WITH q AS (SELECT replace(plainto_tsquery('english', %(query)s)::text, '&', '|') AS text)
SELECT
    e.id AS entry_id, e.symptom, e.environment, e.cause, e.fix, e.repro,
    ts_rank_cd(e.search_vector, q.text::tsquery) * coalesce(s.score, 0.5) AS score,
    m.name AS proposed_by_model, e.human_reviewed,
    coalesce(o.used_outcomes, 0) AS used_outcomes, coalesce(o.successes, 0) AS successes,
    o.last_confirmed
FROM q, entries e
JOIN models m ON m.id = e.created_by_model
LEFT JOIN LATERAL (
    SELECT score FROM ranking_snapshots WHERE entry_id = e.id ORDER BY computed_at DESC LIMIT 1
) s ON true
LEFT JOIN ({stats}) o ON o.entry_id = e.id
WHERE q.text <> ''
  AND e.status = 'approved'
  AND e.search_vector @@ q.text::tsquery
  AND (
      %(language)s::text IS NULL
      OR e.environment->>'language' IS NULL
      OR lower(split_part(e.environment->>'language', ' ', 1)) = %(language)s
  )
ORDER BY score DESC, e.created_at DESC
LIMIT %(limit)s
""".replace("{stats}", OUTCOME_STATS)


def find_entries(conn: Connection, request: SearchRequest) -> list[SearchHit]:
    """Return approved entries matching the request, best first."""
    with conn.cursor(row_factory=class_row(SearchHit)) as cursor:
        cursor.execute(
            _SEARCH,
            {
                "query": request.query,
                "language": request.language(),
                "limit": request.limit,
                **OUTCOME_STATS_PARAMS,
            },
        )
        return cursor.fetchall()
