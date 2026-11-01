from dataclasses import fields
from uuid import UUID

from psycopg.rows import dict_row

from commons.domain.lifecycle import shown_despite_fix
from commons.domain.risks import risk_labels
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

# What search can show: approved entries, and retired ones with a fix version (shown to callers
# before the fix, decision 0026). Word rarity is counted over the same set, or a retired entry's
# own words would count as unknown and it could never match.
_SEARCHABLE = """(
    e.superseded_by IS NULL
    AND (e.status = 'approved' OR (e.status = 'retired' AND e.fixed_in IS NOT NULL))
)"""

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
    o.last_confirmed, e.revision, e.fixed_in, e.status::text AS status, e.risk_report,
    e.confirmed_at IS NOT NULL AS confirmed, e.imported_from,
    tsvector_to_array(to_tsvector('english', e.symptom || ' ' || array_to_string(e.aliases, ' ')
        || ' ' || array_to_string(e.domains, ' '))) AS symptom_words,
    tsvector_to_array(to_tsvector('english', e.cause || ' ' || e.fix)) AS body_words
FROM q, entries e
JOIN models m ON m.id = e.created_by_model
LEFT JOIN LATERAL (
    SELECT score FROM ranking_snapshots WHERE entry_id = e.id ORDER BY computed_at DESC LIMIT 1
) s ON true
LEFT JOIN ({stats}) o ON o.entry_id = e.id
WHERE q.text <> ''
  AND {searchable}
  AND e.search_vector @@ q.text::tsquery
  AND (
      %(language)s::text IS NULL
      OR e.environment->>'language' IS NULL
      OR lower(split_part(e.environment->>'language', ' ', 1)) = %(language)s
  )
ORDER BY ts_rank_cd(e.search_vector, q.text::tsquery) DESC, e.created_at DESC
LIMIT %(candidates)s
""".replace("{stats}", OUTCOME_STATS).replace("{searchable}", _SEARCHABLE)

_QUERY_WORDS = """
SELECT tsvector_to_array(to_tsvector('english', %(query)s)),
       tsvector_to_array(to_tsvector('english', %(generic)s)),
       (SELECT count(*) FROM entries e WHERE {searchable})
""".replace("{searchable}", _SEARCHABLE)

# How many searchable entries contain each word.
_FREQUENCY = """
SELECT word, count(*) FROM entries e, unnest(tsvector_to_array(e.search_vector)) AS word
WHERE {searchable} AND word = ANY(%(words)s) GROUP BY word
""".replace("{searchable}", _SEARCHABLE)

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
        retired = candidate["status"] == "retired"
        if retired and not shown_despite_fix(candidate["fixed_in"], asked_libraries):
            continue  # retired, and the caller is on a fixed version (decision 0026)
        if not retired:
            candidate["fixed_in"] = None
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
            values["risks"] = tuple(risk_labels(candidate["risk_report"]))
            hits.append(SearchHit(**{**values, "score": fit * versions * candidate["score"]}))
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[: request.limit]


# Approved and pending entries sharing a word with a proposal's symptom (decision 0027).
_SIMILAR = """
WITH q AS (SELECT replace(plainto_tsquery('english', %(query)s)::text, '&', '|') AS text)
SELECT e.id,
    tsvector_to_array(to_tsvector('english', e.symptom || ' ' || array_to_string(e.aliases, ' ')
        || ' ' || array_to_string(e.domains, ' '))) AS symptom_words,
    tsvector_to_array(to_tsvector('english', e.cause || ' ' || e.fix)) AS body_words
FROM q, entries e
WHERE q.text <> '' AND e.status IN ('approved', 'pending') AND e.superseded_by IS NULL
  AND e.id <> ALL(%(exclude)s) AND e.search_vector @@ q.text::tsquery
ORDER BY ts_rank_cd(e.search_vector, q.text::tsquery) DESC
LIMIT %(candidates)s
"""


# Word frequencies over everything duplicates are checked against: approved and pending.
_FREQUENCY_LIVE = """
SELECT word, count(*) FROM entries e, unnest(tsvector_to_array(e.search_vector)) AS word
WHERE e.status IN ('approved', 'pending') AND word = ANY(%(words)s) GROUP BY word
"""
_LIVE = "SELECT count(*) FROM entries WHERE status IN ('approved', 'pending')"


def similar_entries(
    conn: Connection, text: str, exclude: list[UUID], limit: int = 3
) -> list[tuple[UUID, float]]:
    """Return approved or pending entries most like `text`, with their relevance, best first."""
    row = conn.execute(_QUERY_WORDS, {"query": text, "generic": GENERIC_WORDS}).fetchone()
    if row is None or not row[0]:
        return []
    query, generic = frozenset(row[0]), frozenset(row[1])
    live = conn.execute(_LIVE).fetchone()
    total = live[0] if live else 0
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(_SIMILAR, {"query": text, "exclude": exclude, "candidates": CANDIDATES})
        candidates = cursor.fetchall()
    words = set(query).union(*(c["symptom_words"] + c["body_words"] for c in candidates))
    found = conn.execute(_FREQUENCY_LIVE, {"words": list(words)}).fetchall()
    frequency: dict[str, int] = dict(found)
    scored = [
        (
            candidate["id"],
            relevance(
                Lexemes(
                    query=query,
                    symptom=frozenset(candidate["symptom_words"]),
                    body=frozenset(candidate["body_words"]),
                    frequency=frequency,
                    entries=max(total, 1),
                    generic=generic,
                )
            ),
        )
        for candidate in candidates
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]
