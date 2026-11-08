from commons.domain.outcomes import SUCCESS_OUTCOMES

# Per-entry stats over outcomes where the entry was used and the result is known: unknown and
# abandoned are skipped, as ranking skips them (decision 0017). An entry with none has no row,
# so used_outcomes is never 0. Live until ranking snapshots (T25). Queries embedding it must
# pass OUTCOME_STATS_PARAMS.
OUTCOME_STATS = """
SELECT
    r.entry_id,
    count(*) AS used_outcomes,
    count(*) FILTER (WHERE oc.outcome = ANY(%(success)s::outcome_kind[])) AS successes,
    max(oc.created_at) FILTER (WHERE oc.outcome = ANY(%(success)s::outcome_kind[]))
        AS last_confirmed
FROM counted_retrievals r JOIN counted_outcomes oc ON oc.retrieval_id = r.id
WHERE oc.used IN ('yes', 'partial') AND oc.outcome NOT IN ('unknown', 'abandoned')
GROUP BY r.entry_id
"""

OUTCOME_STATS_PARAMS: dict[str, object] = {"success": list(SUCCESS_OUTCOMES)}
