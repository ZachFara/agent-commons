from psycopg.rows import class_row

from commons.domain.outcomes import SUCCESS_OUTCOMES
from commons.domain.search import SearchHit, SearchRequest
from commons.store.connection import Connection

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
LEFT JOIN LATERAL (
    SELECT
        count(*) AS used_outcomes,
        count(*) FILTER (WHERE oc.outcome = ANY(%(success)s::outcome_kind[])) AS successes,
        max(oc.created_at) FILTER (WHERE oc.outcome = ANY(%(success)s::outcome_kind[]))
            AS last_confirmed
    FROM retrievals r JOIN outcomes oc ON oc.retrieval_id = r.id
    WHERE r.entry_id = e.id AND oc.used IN ('yes', 'partial')
) o ON true
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
"""


def find_entries(conn: Connection, request: SearchRequest) -> list[SearchHit]:
    """Return approved entries matching the request, best first."""
    with conn.cursor(row_factory=class_row(SearchHit)) as cursor:
        cursor.execute(
            _SEARCH,
            {
                "query": request.query,
                "language": request.language(),
                "limit": request.limit,
                "success": list(SUCCESS_OUTCOMES),
            },
        )
        return cursor.fetchall()
