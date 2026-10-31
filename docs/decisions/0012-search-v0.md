# 0012. Search v0: matching, ordering, environment, task id
Status: Accepted (owner, 2026-10-24); ordering superseded by 0025
Date: 2026-10-24

Context: DESIGN.md v0.1 left four things open for `search`: no `task_id` (but every retrievals
row needs one), AND-matching that fails on long error messages, recency ordering that lets a weak
recent match beat a strong old one, and no defined meaning for `environment`.

Decision:
- `search` takes a required `task_id`, like `propose` and `report`.
- Matching: an entry matches if it shares any stemmed word with the query (OR of
  `plainto_tsquery` lexemes). Only `approved` entries are returned, reviewed or not
  (decision 0005).
- Ordering: `ts_rank_cd` relevance × the entry's latest snapshot score (0.5 prior until T25
  writes snapshots), newest first on ties. `score_at_retrieval` logs that product.
- `environment.language`, if given, filters by language name (its first word, case-insensitive):
  entries in another language are excluded, entries without a language are kept. Versions and
  other keys don't filter yet (DESIGN §11 asks whether environment filtering beats vectors).
- Provenance is computed live from outcomes until snapshots exist: proposing model, success rate
  over used outcomes with its sample size, last confirmed date, and "unreviewed" when no human
  has reviewed it.

Consequences: DESIGN.md §4 and §7 updated. Retrieval quality will be judged from the retrievals
and outcomes data; any change to matching or ordering gets a new decision.
