from dataclasses import fields

from psycopg.rows import dict_row

from commons.domain.search import (
    GENERIC_WORDS,
    MIN_RELEVANCE,
    Lexemes,
    SearchHit,
    SearchRequest,
    relevance,
    version_factor,
)
from commons.store.connection import Connection
from commons.store.outcome_stats import OUTCOME_STATS, OUTCOME_STATS_PARAMS

CANDIDATES = 50

# Decision 0025, replacing 0012's ordering. Candidates share at least one stemmed word with
# the query (OR of plainto_tsquery lexemes); `relevance` then scores each by rarity-weighted
# coverage, drops those under MIN_RELEVANCE, and the score is relevance x the latest snapshot
# score (0.5 prior).
_CANDIDATES = """
WITH q AS (SELECT replace(plainto_tsquery('english', %(query)s)::text, '&', '|') AS text)
SELECT
    e.id AS entry_id, e.symptom, e.environment, e.cause, e.fix, e.repro,
    coalesce(s.score, 0.5) AS score,
    m.name AS proposed_by_model, e.human_reviewed,
    coalesce(o.used_outcomes, 0) AS used_outcomes, coalesce(o.successes, 0) AS successes,
    o.last_confirmed,
    tsvector_to_array(to_tsvector('english',
        e.symptom || ' ' || array_to_string(e.domains, ' '))) AS symptom_words,
    tsvector_to_array(to_tsvector('english', e.cause || ' ' || e.fix)) AS body_words
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
ORDER BY ts_rank_cd(e.search_vector, q.text::tsquery) DESC, e.created_at DESC
LIMIT %(candidates)s
""".replace("{stats}", OUTCOME_STATS)

_QUERY_WORDS = """
SELECT tsvector_to_array(to_tsvector('english', %(query)s)),
       tsvector_to_array(to_tsvector('english', %(generic)s)),
       (SELECT count(*) FROM entries WHERE status = 'approved')
"""

# How many approved entries contain each word.
_FREQUENCY = """
SELECT word, count(*) FROM entries e, unnest(tsvector_to_array(e.search_vector)) AS word
WHERE e.status = 'approved' AND word = ANY(%(words)s) GROUP BY word
"""

_HIT_FIELDS = {f.name for f in fields(SearchHit)}


def find_entries(conn: Connection, request: SearchRequest) -> list[SearchHit]:
    """Return approved entries relevant to the request, best first."""
    row = conn.execute(_QUERY_WORDS, {"query": request.query, "generic": GENERIC_WORDS}).fetchone()
    if row is None or not row[0]:
        return []
    query, generic, total = frozenset(row[0]), frozenset(row[1]), row[2]
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            _CANDIDATES,
            {
                "query": request.query,
                "language": request.language(),
                "candidates": CANDIDATES,
                **OUTCOME_STATS_PARAMS,
            },
        )
        candidates = cursor.fetchall()
    words = set(query).union(*(c["symptom_words"] + c["body_words"] for c in candidates))
    frequency: dict[str, int] = dict(conn.execute(_FREQUENCY, {"words": list(words)}).fetchall())
    asked_libraries = (request.environment or {}).get("libraries")
    hits = []
    for candidate in candidates:
        lexemes = Lexemes(
            query=query,
            symptom=frozenset(candidate["symptom_words"]),
            body=frozenset(candidate["body_words"]),
            frequency=frequency,
            entries=total,
            generic=generic,
        )
        fit = relevance(lexemes)
        if fit >= MIN_RELEVANCE:
            versions = version_factor(asked_libraries, candidate["environment"].get("libraries"))
            values = {k: v for k, v in candidate.items() if k in _HIT_FIELDS}
            hits.append(SearchHit(**{**values, "score": fit * versions * candidate["score"]}))
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[: request.limit]
