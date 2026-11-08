"""Read-only queries behind the dashboard pages. Rows are plain dicts: they only feed templates."""

from typing import Any
from uuid import UUID

from psycopg import sql
from psycopg.rows import dict_row

from commons.domain.outcomes import SUCCESS_OUTCOMES
from commons.store.connection import Connection

Row = dict[str, Any]
_SUCCESS = {"success": list(SUCCESS_OUTCOMES)}

_DAILY = """
WITH days AS (
    SELECT generate_series(current_date - (%(days)s - 1), current_date, interval '1 day')::date
        AS day
)
SELECT d.day,
       (SELECT count(*) FROM counted_retrievals r WHERE r.created_at::date = d.day) AS retrievals,
       (SELECT count(*) FROM counted_outcomes o WHERE o.created_at::date = d.day
          AND o.outcome = ANY(%(success)s::outcome_kind[])) AS successes,
       (SELECT count(*) FROM counted_outcomes o WHERE o.created_at::date = d.day
          AND o.outcome IN ('tests_failed', 'error_persisted', 'user_rejected')) AS failures,
       (SELECT count(*) FROM entries e WHERE e.created_at::date = d.day) AS proposals
FROM days d ORDER BY d.day
"""

_KPIS = """
SELECT
  (SELECT count(*) FROM entries WHERE status = 'approved') AS approved,
  (SELECT count(*) FROM entries WHERE NOT human_reviewed AND status <> 'retired') AS awaiting,
  (SELECT count(*) FROM counted_retrievals WHERE created_at > now() - interval '30 days')
      AS retrievals_30d,
  (SELECT count(DISTINCT reporter_id) FROM tasks WHERE created_at > now() - interval '30 days')
      AS active_reporters,
  (SELECT count(*) FILTER (WHERE outcome = ANY(%(success)s::outcome_kind[]))::float
          / nullif(count(*) FILTER (WHERE outcome <> 'unknown' AND outcome <> 'abandoned'), 0)
     FROM counted_outcomes WHERE evidence->>'outcome_source' = 'hook'
       AND created_at > now() - interval '30 days') AS observed_success_rate,
  (SELECT count(*) FROM counted_outcomes WHERE evidence->>'outcome_source' = 'hook'
       AND created_at > now() - interval '30 days') AS observed_outcomes,
  (SELECT avg(reliability) FROM reporters WHERE reliability IS NOT NULL) AS mean_reliability,
  (SELECT count(*) FROM reporters WHERE reliability IS NOT NULL) AS rated_reporters
"""

_OUTCOME_MIX = """
SELECT outcome::text AS outcome,
       count(*) FILTER (WHERE evidence->>'outcome_source' = 'hook') AS observed,
       count(*) FILTER (WHERE coalesce(evidence->>'outcome_source', '') <> 'hook') AS self_reported
FROM counted_outcomes WHERE created_at > now() - interval '30 days'
GROUP BY outcome ORDER BY count(*) DESC
"""

_LATEST_SNAPSHOT = """
LEFT JOIN LATERAL (
    SELECT score, retrieval_count_30d, used_rate, success_rate FROM ranking_snapshots s
    WHERE s.entry_id = e.id ORDER BY computed_at DESC LIMIT 1
) s ON true
"""

_ENTRIES = (
    """
SELECT e.id, e.created_at, e.status::text AS status, e.human_reviewed, e.symptom, e.domains,
       m.name AS model, rp.pseudonym AS proposed_by,
       s.score, s.retrieval_count_30d, s.used_rate, s.success_rate
FROM entries e JOIN models m ON m.id = e.created_by_model
JOIN reporters rp ON rp.id = e.created_by
"""
    + _LATEST_SNAPSHOT
    + """
WHERE (%(status)s::text IS NULL OR e.status::text = %(status)s)
  AND (%(query)s::text IS NULL OR e.symptom ILIKE %(like)s OR e.cause ILIKE %(like)s
       OR e.fix ILIKE %(like)s OR %(query)s = ANY(e.domains))
ORDER BY {order} NULLS LAST, e.created_at DESC
LIMIT %(limit)s
"""
)

_ORDERS = {
    "score": "s.score DESC",
    "newest": "e.created_at DESC",
    "used": "s.retrieval_count_30d DESC",
}

# Token cost per entry over the outcomes that say whether it worked: used, with a known
# outcome (the filter of `_SAMPLES` in store/ranking.py), with tokens recorded by the Stop
# hook (MIT-600), and neither row excluded. A row without tokens counts in none of these.
_COSTS = """
SELECT r.entry_id,
       count(*) AS costed,
       sum(o.tokens_in::bigint + o.tokens_out)::bigint AS tokens,
       count(*) FILTER (WHERE o.outcome = ANY(%(success)s::outcome_kind[])) AS successes,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY o.tokens_in::bigint + o.tokens_out)
           FILTER (WHERE o.outcome = ANY(%(success)s::outcome_kind[])) AS median_success,
       count(*) FILTER (WHERE o.outcome <> ALL(%(success)s::outcome_kind[])) AS failures,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY o.tokens_in::bigint + o.tokens_out)
           FILTER (WHERE o.outcome <> ALL(%(success)s::outcome_kind[])) AS median_failure
FROM counted_outcomes o JOIN counted_retrievals r ON r.id = o.retrieval_id
WHERE r.entry_id = ANY(%(ids)s) AND o.used IN ('yes', 'partial')
  AND o.outcome NOT IN ('unknown', 'abandoned')
  AND o.tokens_in IS NOT NULL AND o.tokens_out IS NOT NULL
GROUP BY r.entry_id
"""

# Every counted retrieval of an entry, by what came of it. The buckets add up: retrieved =
# not_used + used_unknown + used, and used = worked + failed + no_result. A retrieval with no
# counted outcome (none yet, or the outcome excluded) is used_unknown, like `used IS NULL`.
# Worked and failed match `_DAILY` and `_COSTS`; no_result is `unknown` or `abandoned`.
_FUNNEL = """
SELECT count(*) AS retrieved,
       count(*) FILTER (WHERE o.used = 'no') AS not_used,
       count(*) FILTER (WHERE o.used IS NULL) AS used_unknown,
       count(*) FILTER (WHERE o.used IN ('yes', 'partial')) AS used,
       count(*) FILTER (WHERE o.used IN ('yes', 'partial')
           AND o.outcome = ANY(%(success)s::outcome_kind[])) AS worked,
       count(*) FILTER (WHERE o.used IN ('yes', 'partial')
           AND o.outcome NOT IN ('unknown', 'abandoned')
           AND o.outcome <> ALL(%(success)s::outcome_kind[])) AS failed,
       count(*) FILTER (WHERE o.used IN ('yes', 'partial')
           AND o.outcome IN ('unknown', 'abandoned')) AS no_result
FROM counted_retrievals r LEFT JOIN counted_outcomes o ON o.retrieval_id = r.id
WHERE r.entry_id = %(id)s
"""

# The retrievals `r` that a search `s` returned. A retrieval has no search id: it is written
# in the search's transaction, so it shares the search's task, reporter, query and created_at
# (now() is the transaction's start).
_RETRIEVALS_OF_SEARCH = sql.SQL(
    "r.task_id = s.task_id AND r.reporter_id = s.reporter_id AND r.query = s.query "
    "AND r.created_at = s.created_at"
)

# Recent searches whose every counted retrieval has a counted outcome with used = 'no'.
_UNUSED_SEARCHES = sql.SQL("""
SELECT s.id, s.created_at, s.query, rp.pseudonym AS reporter, m.name AS model, hits.entries
FROM counted_searches s JOIN reporters rp ON rp.id = s.reporter_id
JOIN models m ON m.id = s.model_id
JOIN LATERAL (
    SELECT count(*) AS retrieved, count(*) FILTER (WHERE o.used = 'no') AS not_used,
           json_agg(json_build_object('entry_id', r.entry_id, 'rank', r.rank_position,
               'relevance', r.relevance, 'symptom', e.symptom) ORDER BY r.rank_position)
               AS entries
    FROM counted_retrievals r JOIN entries e ON e.id = r.entry_id
    LEFT JOIN counted_outcomes o ON o.retrieval_id = r.id
    WHERE {linked}
) hits ON hits.retrieved > 0 AND hits.not_used = hits.retrieved
WHERE s.created_at > now() - interval '30 days'
ORDER BY s.created_at DESC LIMIT %(limit)s
""").format(linked=_RETRIEVALS_OF_SEARCH)

# Every counted search of the last `days` days, oldest first, with what came of each counted
# retrieval it returned (decision 0025 step 1). A search that found nothing has no results.
# The views leave out excluded rows, and so test-key rows (decision 0030).
_SEARCH_RESULTS = sql.SQL("""
SELECT s.id, s.created_at, s.query, m.name AS model, coalesce(hits.results, '[]') AS results
FROM counted_searches s JOIN models m ON m.id = s.model_id
LEFT JOIN LATERAL (
    SELECT json_agg(json_build_object('entry_id', r.entry_id, 'rank', r.rank_position,
               'relevance', r.relevance, 'used', o.used, 'outcome', o.outcome,
               'assessment', o.agent_assessment) ORDER BY r.rank_position) AS results
    FROM counted_retrievals r LEFT JOIN counted_outcomes o ON o.retrieval_id = r.id
    WHERE {linked}
) hits ON true
WHERE s.created_at > now() - make_interval(days => %(days)s)
ORDER BY s.created_at, s.id
""").format(linked=_RETRIEVALS_OF_SEARCH)

# What became of the entries proposed in the last 30 days, by proposing model (DESIGN §11,
# open question 1). Each entry lands in exactly one bucket, the first that matches, so the
# buckets add up to `proposals`:
#   suspended       its proposer is suspended (decision 0029: reporters.suspended_at)
#   merged          merged into another entry as a duplicate (decision 0027). A merge
#                   (service/duplicates.merge) sets superseded_by, then rejects a pending
#                   duplicate or retires an approved one with the human review reason
#                   "duplicate of <canonical id>". That reason naming the current successor
#                   is what tells a merge from an admin's supersede (decision 0026 §3),
#                   which also sets superseded_by but with the admin's own reason.
#   retired         retired, with or without a successor (decision 0026): superseded
#                   entries that weren't merged count here
#   rejected        rejected by a human; by the reason of the latest human reject review
#   human_approved  approved by a human (human_reviewed, decision 0005)
#   auto_approved   approved on the proposer's trust, no human review yet (decision 0028:
#                   provisional, sampled or not)
#   agent_approved  approved by an agent review only (decision 0005)
#   flagged         waiting for a human, with likely duplicates (decision 0027:
#                   duplicate_candidates)
#   pending         waiting for a human: pending, or rejected by an agent review only
# A human review leaves `provisional` set, so human_approved is tested first. Seed entries
# aren't proposals.
FUNNEL_BUCKETS = (
    "human_approved",
    "auto_approved",
    "agent_approved",
    "rejected",
    "merged",
    "flagged",
    "pending",
    "retired",
    "suspended",
)
_PROPOSAL_FUNNEL = """
WITH proposals AS (
    SELECT m.name AS model,
           CASE WHEN rp.suspended_at IS NOT NULL THEN 'suspended'
                WHEN e.status IN ('rejected', 'retired') AND EXISTS (
                    SELECT 1 FROM reviews v WHERE v.entry_id = e.id AND v.source = 'human'
                      AND v.decision IN ('reject', 'retire')
                      AND v.reason = 'duplicate of ' || e.superseded_by::text)
                    THEN 'merged'
                WHEN e.status = 'retired' THEN 'retired'
                WHEN e.human_reviewed AND e.status = 'rejected' THEN 'rejected'
                WHEN e.human_reviewed AND e.status = 'approved' THEN 'human_approved'
                WHEN e.status = 'approved' AND e.provisional THEN 'auto_approved'
                WHEN e.status = 'approved' THEN 'agent_approved'
                WHEN jsonb_array_length(e.duplicate_candidates) > 0 THEN 'flagged'
                ELSE 'pending' END AS bucket,
           (SELECT v.reason FROM reviews v WHERE v.entry_id = e.id AND v.decision = 'reject'
              AND v.source = 'human' ORDER BY v.created_at DESC LIMIT 1) AS reason
    FROM entries e JOIN models m ON m.id = e.created_by_model
    JOIN reporters rp ON rp.id = e.created_by
    WHERE e.created_at > now() - interval '30 days' AND rp.pseudonym <> 'agent-commons-seed'
), reasons AS (
    SELECT model, json_agg(json_build_object('reason', reason, 'count', n)
               ORDER BY n DESC, reason) AS reasons
    FROM (SELECT model, reason, count(*) AS n FROM proposals WHERE bucket = 'rejected'
          GROUP BY model, reason) rejected
    GROUP BY model
)
SELECT c.*, coalesce(r.reasons, '[]') AS reasons
FROM (SELECT model, count(*) AS proposals,
             count(*) FILTER (WHERE bucket = 'human_approved') AS human_approved,
             count(*) FILTER (WHERE bucket = 'auto_approved') AS auto_approved,
             count(*) FILTER (WHERE bucket = 'agent_approved') AS agent_approved,
             count(*) FILTER (WHERE bucket = 'rejected') AS rejected,
             count(*) FILTER (WHERE bucket = 'merged') AS merged,
             count(*) FILTER (WHERE bucket = 'flagged') AS flagged,
             count(*) FILTER (WHERE bucket = 'pending') AS pending,
             count(*) FILTER (WHERE bucket = 'retired') AS retired,
             count(*) FILTER (WHERE bucket = 'suspended') AS suspended
      FROM proposals GROUP BY model) c
LEFT JOIN reasons r USING (model)
ORDER BY c.proposals DESC, c.model
"""

# How approved entries fare in search, by proposing model. `unretrieved`: proposed 60 or
# more days ago and no counted retrieval in the last 60 days. `never_used`: 3 or more counted
# retrievals ever, and no counted outcome saw it used. Both leave out suspended reporters'
# entries, which `suspended` counts instead.
_APPROVED_USE = """
SELECT m.name AS model,
       count(*) FILTER (WHERE rp.suspended_at IS NULL) AS approved,
       count(*) FILTER (WHERE rp.suspended_at IS NULL
           AND e.created_at <= now() - interval '60 days'
           AND NOT EXISTS (SELECT 1 FROM counted_retrievals r WHERE r.entry_id = e.id
               AND r.created_at > now() - interval '60 days')) AS unretrieved,
       count(*) FILTER (WHERE rp.suspended_at IS NULL
           AND (SELECT count(*) FROM counted_retrievals r WHERE r.entry_id = e.id) >= 3
           AND NOT EXISTS (SELECT 1 FROM counted_retrievals r
               JOIN counted_outcomes o ON o.retrieval_id = r.id
               WHERE r.entry_id = e.id AND o.used IN ('yes', 'partial'))) AS never_used,
       count(*) FILTER (WHERE rp.suspended_at IS NOT NULL) AS suspended
FROM entries e JOIN models m ON m.id = e.created_by_model
JOIN reporters rp ON rp.id = e.created_by
WHERE e.status = 'approved' AND rp.pseudonym <> 'agent-commons-seed'
GROUP BY m.name ORDER BY approved DESC, m.name
"""


def daily_activity(conn: Connection, days: int) -> list[Row]:
    """Per day for the last `days` days: retrievals, successes, failures, proposals."""
    return _rows(conn, _DAILY, {"days": days, **_SUCCESS})


def kpis(conn: Connection) -> Row:
    """Headline numbers for the overview."""
    rows = _rows(conn, _KPIS, _SUCCESS)
    return rows[0]


def outcome_mix(conn: Connection) -> list[Row]:
    """Outcomes in the last 30 days by kind, split observed vs self-reported."""
    return _rows(conn, _OUTCOME_MIX, {})


def entries(
    conn: Connection, *, status: str | None, query: str | None, order: str, limit: int = 200
) -> list[Row]:
    """Entries with their latest ranking snapshot, filtered and ordered."""
    text = _ENTRIES.replace("{order}", _ORDERS.get(order, _ORDERS["score"]))
    params = {"status": status, "query": query, "like": f"%{query}%", "limit": limit}
    return _rows(conn, text, params)


def entry(conn: Connection, entry_id: UUID) -> Row | None:
    """One entry with everything its page shows, or None."""
    rows = _rows(
        conn,
        "SELECT e.*, e.status::text AS status, m.name AS model, rp.pseudonym AS proposed_by, "
        "s.score, s.retrieval_count_30d, s.used_rate, s.success_rate "
        "FROM entries e JOIN models m ON m.id = e.created_by_model "
        "JOIN reporters rp ON rp.id = e.created_by " + _LATEST_SNAPSHOT + "WHERE e.id = %(id)s",
        {"id": entry_id},
    )
    return rows[0] if rows else None


def entry_costs(conn: Connection, entry_ids: list[UUID]) -> dict[UUID, Row]:
    """Return each entry's token cost: tokens per success and median tokens by result.

    An entry with no costed outcome is left out. Every figure comes with its n: `costed`
    outcomes behind `tokens`, `successes` and `failures` behind the two medians.
    """
    rows = _rows(conn, _COSTS, {"ids": entry_ids, **_SUCCESS})
    for row in rows:
        row["tokens_per_success"] = row["tokens"] / row["successes"] if row["successes"] else None
    return {row["entry_id"]: row for row in rows}


def entry_funnel(conn: Connection, entry_id: UUID) -> Row:
    """Return how the entry's counted retrievals ended, bucketed as `_FUNNEL` describes."""
    return _rows(conn, _FUNNEL, {"id": entry_id, **_SUCCESS})[0]


def entry_reviews(conn: Connection, entry_id: UUID) -> list[Row]:
    """Return the entry's reviews, oldest first."""
    return _rows(
        conn,
        "SELECT r.created_at, r.decision::text AS decision, r.source::text AS source, r.reason, "
        "rp.pseudonym AS reviewer FROM reviews r JOIN reporters rp ON rp.id = r.reviewed_by "
        "WHERE r.entry_id = %(id)s ORDER BY r.created_at",
        {"id": entry_id},
    )


def score_history(conn: Connection, entry_id: UUID) -> list[Row]:
    """Return the entry's ranking snapshots, oldest first."""
    return _rows(
        conn,
        "SELECT computed_at, score FROM ranking_snapshots WHERE entry_id = %(id)s "
        "ORDER BY computed_at",
        {"id": entry_id},
    )


def entry_retrievals(conn: Connection, entry_id: UUID, limit: int = 50) -> list[Row]:
    """Recent retrievals of the entry with their outcomes."""
    return _rows(
        conn,
        "SELECT r.id AS retrieval_id, o.id AS outcome_id, "
        "r.excluded OR coalesce(o.excluded, false) AS excluded, "
        "r.created_at, r.task_id, r.query, r.rank_position, r.relevance, "
        "rp.pseudonym AS reporter, "
        "m.name AS model, o.used::text AS used, o.outcome::text AS outcome, "
        "o.evidence->>'outcome_source' AS source, o.agent_assessment "
        "FROM retrievals r JOIN reporters rp ON rp.id = r.reporter_id "
        "JOIN models m ON m.id = r.model_id LEFT JOIN outcomes o ON o.retrieval_id = r.id "
        "WHERE r.entry_id = %(id)s ORDER BY r.created_at DESC LIMIT %(limit)s",
        {"id": entry_id, "limit": limit},
    )


def tasks(conn: Connection, limit: int = 100) -> list[Row]:
    """Recent agent sessions with what they retrieved, reported, and tested."""
    return _rows(
        conn,
        "SELECT t.id, t.created_at, rp.pseudonym AS reporter, m.name AS model, "
        "t.privacy_mode::text AS privacy, "
        "(SELECT count(DISTINCT r.entry_id) FROM counted_retrievals r WHERE r.task_id = t.id) "
        "  AS retrieved, "
        "(SELECT count(*) FROM counted_retrievals r "
        " JOIN counted_outcomes o ON o.retrieval_id = r.id "
        " WHERE r.task_id = t.id AND o.agent_assessment IS NOT NULL) AS reported, "
        "(SELECT count(*) FROM task_events e WHERE e.task_id = t.id AND e.kind = 'test_run' "
        " AND (e.data->>'exit_code')::int = 0) AS tests_passed, "
        "(SELECT count(*) FROM task_events e WHERE e.task_id = t.id AND e.kind = 'test_run' "
        " AND (e.data->>'exit_code')::int <> 0) AS tests_failed "
        "FROM tasks t JOIN reporters rp ON rp.id = t.reporter_id "
        "LEFT JOIN models m ON m.id = t.model_id ORDER BY t.created_at DESC LIMIT %(limit)s",
        {"limit": limit},
    )


def search_totals(conn: Connection) -> Row:
    """Count searches, and searches that found nothing, over the last 30 days."""
    rows = _rows(
        conn,
        "SELECT count(*) AS searches, count(*) FILTER (WHERE hit_count = 0) AS misses, "
        "count(DISTINCT reporter_id) AS reporters FROM counted_searches "
        "WHERE created_at > now() - interval '30 days'",
        {},
    )
    return rows[0]


def missed_queries(conn: Connection, limit: int = 100) -> list[Row]:
    """Return queries that found nothing in the last 30 days, most often searched first."""
    return _rows(
        conn,
        "SELECT query, count(*) AS times, count(DISTINCT reporter_id) AS reporters, "
        "max(created_at) AS last_seen, "
        "string_agg(DISTINCT language, ', ') AS languages "
        "FROM counted_searches WHERE hit_count = 0 AND created_at > now() - interval '30 days' "
        "GROUP BY query ORDER BY times DESC, last_seen DESC LIMIT %(limit)s",
        {"limit": limit},
    )


def recent_searches(conn: Connection, limit: int = 100) -> list[Row]:
    """Return the latest searches with who ran them and how many entries came back."""
    return _rows(
        conn,
        "SELECT s.id, s.excluded, s.created_at, s.task_id, s.query, s.language, s.hit_count, "
        "s.privacy_mode::text AS privacy, rp.pseudonym AS reporter, m.name AS model "
        "FROM searches s JOIN reporters rp ON rp.id = s.reporter_id "
        "JOIN models m ON m.id = s.model_id ORDER BY s.created_at DESC LIMIT %(limit)s",
        {"limit": limit},
    )


def unused_searches(conn: Connection, limit: int = 50) -> list[Row]:
    """Return searches of the last 30 days where hooks saw no returned entry used.

    Newest first. Each row's `entries` lists what came back in rank order with its relevance,
    None for a retrieval logged before relevance was (MIT-602).
    """
    return _rows(conn, _UNUSED_SEARCHES, {"limit": limit})


def search_results(conn: Connection, days: int) -> list[Row]:
    """Return the counted searches of the last `days` days with their results, oldest first.

    Each row's `results` lists its counted retrievals in rank order: relevance (None before
    MIT-602), and the outcome's `used`, `outcome` and `assessment` (None without an outcome).
    """
    return _rows(conn, _SEARCH_RESULTS, {"days": days})


def proposal_funnel(conn: Connection) -> list[Row]:
    """Return per model the last 30 days' proposals in `FUNNEL_BUCKETS`, which add up.

    `reasons` lists the rejected ones' reasons with how many had each, most frequent first.
    """
    return _rows(conn, _PROPOSAL_FUNNEL, {})


def approved_use(conn: Connection) -> list[Row]:
    """Return per model the approved entries search never shows, or shows but nobody uses."""
    return _rows(conn, _APPROVED_USE, {})


def reporters(conn: Connection) -> list[Row]:
    """Every reporter with contribution and accuracy figures."""
    return _rows(
        conn,
        "SELECT rp.pseudonym, rp.privacy_default::text AS privacy, rp.reliability, "
        "rp.suspended_at, "
        "EXISTS (SELECT 1 FROM api_keys k WHERE k.reporter_id = rp.id AND k.scope = 'admin' "
        "  AND k.revoked_at IS NULL AND (k.expires_at IS NULL OR k.expires_at > now())) "
        "  AS is_admin, "
        "(SELECT count(*) FROM entries e WHERE e.created_by = rp.id) AS proposed, "
        "(SELECT count(*) FROM entries e WHERE e.created_by = rp.id AND e.status = 'approved') "
        "  AS approved, "
        "(SELECT count(*) FROM counted_retrievals r WHERE r.reporter_id = rp.id) AS retrievals, "
        "(SELECT count(*) FROM counted_retrievals r "
        " JOIN counted_outcomes o ON o.retrieval_id = r.id "
        " WHERE r.reporter_id = rp.id) AS outcomes, "
        "(SELECT max(t.created_at) FROM tasks t WHERE t.reporter_id = rp.id) AS last_seen "
        "FROM reporters rp WHERE rp.pseudonym <> 'agent-commons-seed' "
        "ORDER BY retrievals DESC, rp.pseudonym",
        {},
    )


def models(conn: Connection) -> list[Row]:
    """Every model with retrievals and observed success rate (DESIGN §7 model attribution)."""
    return _rows(
        conn,
        "SELECT m.name, count(r.id) AS retrievals, "
        "count(o.id) FILTER (WHERE o.evidence->>'outcome_source' = 'hook') AS observed, "
        "count(o.id) FILTER (WHERE o.evidence->>'outcome_source' = 'hook' "
        "  AND o.outcome = ANY(%(success)s::outcome_kind[]))::float "
        "  / nullif(count(o.id) FILTER (WHERE o.evidence->>'outcome_source' = 'hook' "
        "  AND o.outcome NOT IN ('unknown', 'abandoned')), 0) AS observed_success_rate, "
        "(SELECT count(*) FROM entries e WHERE e.created_by_model = m.id) AS proposed "
        "FROM models m LEFT JOIN counted_retrievals r ON r.model_id = m.id "
        "LEFT JOIN counted_outcomes o ON o.retrieval_id = r.id "
        "WHERE m.name NOT IN ('seed', 'unknown') "
        "GROUP BY m.id, m.name ORDER BY retrievals DESC",
        _SUCCESS,
    )


def _rows(conn: Connection, query: str | sql.Composed, params: dict[str, Any]) -> list[Row]:
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def corrections(conn: Connection, limit: int = 200) -> list[Row]:
    """Every correction, newest first (decision 0030)."""
    return _rows(
        conn,
        "SELECT id, batch_id, created_at, corrected_by, target_table, target_id, field, "
        "old_value, new_value, reason, undone_at, undone_by FROM corrections "
        "ORDER BY created_at DESC LIMIT %(limit)s",
        {"limit": limit},
    )


def entry_revisions(conn: Connection, entry_id: UUID) -> list[Row]:
    """Every version of the entry's text, newest first (decision 0026)."""
    return _rows(
        conn,
        "SELECT revision, created_at, source, edited_by, reason, symptom, cause, fix "
        "FROM entry_revisions WHERE entry_id = %(id)s ORDER BY revision DESC",
        {"id": entry_id},
    )


STALE_DAYS = 90


def stale_candidates(conn: Connection, limit: int = 20) -> list[Row]:
    """Approved entries to retire or revise (decision 0026, until Phase 3's flags).

    Still retrieved in the last 90 days but no successful use in that time, or used at
    least 3 times in the last 30 days with under half of those working.
    """
    return _rows(
        conn,
        "SELECT e.id, e.symptom, "
        "(SELECT count(*) FROM counted_retrievals r WHERE r.entry_id = e.id "
        "  AND r.created_at > now() - make_interval(days => %(days)s)) AS retrievals, "
        "(SELECT max(o.created_at) FROM counted_outcomes o JOIN counted_retrievals r "
        "  ON r.id = o.retrieval_id WHERE r.entry_id = e.id AND o.used IN ('yes', 'partial') "
        "  AND o.outcome = ANY(%(success)s::outcome_kind[])) AS last_success, "
        "recent.used, recent.worked "
        "FROM entries e LEFT JOIN LATERAL ("
        "  SELECT count(*) AS used, "
        "    count(*) FILTER (WHERE o.outcome = ANY(%(success)s::outcome_kind[])) AS worked "
        "  FROM counted_outcomes o JOIN counted_retrievals r ON r.id = o.retrieval_id "
        "  WHERE r.entry_id = e.id AND o.used IN ('yes', 'partial') "
        "  AND o.created_at > now() - interval '30 days') recent ON true "
        "WHERE e.status = 'approved' AND e.superseded_by IS NULL "
        "AND ((SELECT count(*) FROM counted_retrievals r WHERE r.entry_id = e.id "
        "      AND r.created_at > now() - make_interval(days => %(days)s)) > 0 "
        "     AND NOT EXISTS (SELECT 1 FROM counted_outcomes o JOIN counted_retrievals r "
        "       ON r.id = o.retrieval_id WHERE r.entry_id = e.id "
        "       AND o.used IN ('yes', 'partial') AND o.outcome = ANY(%(success)s::outcome_kind[]) "
        "       AND o.created_at > now() - make_interval(days => %(days)s)) "
        "  OR (recent.used >= 3 AND recent.worked * 2 < recent.used)) "
        "ORDER BY retrievals DESC LIMIT %(limit)s",
        {"days": STALE_DAYS, "limit": limit, **_SUCCESS},
    )


def duplicate_flags(conn: Connection) -> dict[UUID, list[Row]]:
    """Return, per entry awaiting review, the existing entries it looks like (decision 0027)."""
    rows = _rows(
        conn,
        "SELECT e.id AS entry_id, (c->>'entry_id')::uuid AS other_id, (c->>'score')::float "
        "AS score, coalesce((c->>'retry')::boolean, false) AS retry, o.symptom, "
        "o.status::text AS status, (o.id = e.duplicate_of) AS linked "
        "FROM entries e, jsonb_array_elements(e.duplicate_candidates) c "
        "JOIN entries o ON o.id = (c->>'entry_id')::uuid "
        "WHERE NOT e.human_reviewed ORDER BY score DESC",
        {},
    )
    flags: dict[UUID, list[Row]] = {}
    for row in rows:
        flags.setdefault(row["entry_id"], []).append(row)
    return flags
