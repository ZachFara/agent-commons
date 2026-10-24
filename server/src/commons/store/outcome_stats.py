from commons.domain.outcomes import SUCCESS_OUTCOMES

# Per-entry stats over outcomes where the entry was used (DESIGN.md §7). Live until ranking
# snapshots (T25). Queries embedding it must pass OUTCOME_STATS_PARAMS.
OUTCOME_STATS = """
SELECT
    r.entry_id,
    count(*) AS used_outcomes,
    count(*) FILTER (WHERE oc.outcome = ANY(%(success)s::outcome_kind[])) AS successes,
    max(oc.created_at) FILTER (WHERE oc.outcome = ANY(%(success)s::outcome_kind[]))
        AS last_confirmed
FROM retrievals r JOIN outcomes oc ON oc.retrieval_id = r.id
WHERE oc.used IN ('yes', 'partial')
GROUP BY r.entry_id
"""

OUTCOME_STATS_PARAMS: dict[str, object] = {"success": list(SUCCESS_OUTCOMES)}
