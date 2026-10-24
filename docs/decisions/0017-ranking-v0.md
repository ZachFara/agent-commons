# 0017. Ranking v0 and reporter reliability
Status: Accepted (owner, 2026-10-25)
Date: 2026-10-25

Context: DESIGN.md §7: Bayesian success rate over used outcomes, weighted by reporter
reliability, prior 0.5, decay after 90 days, computed nightly into ranking_snapshots.

Decision:
- Score = (2 × 0.5 + Σ wᵢ·sᵢ) / (2 + Σ wᵢ) over outcomes with `used` yes or partial: a Beta
  prior with mean 0.5 and the weight of two outcomes. sᵢ is 1 for tests_passed, error_resolved,
  user_accepted, else 0; `unknown` and `abandoned` outcomes are skipped.
- wᵢ = source weight × use weight × age weight. Source: 1 for hook-observed outcomes; the
  reporter's reliability for self-reported ones, 0.5 until known (owner choice: only
  self-reports are discounted, per principle 1). Use: 1 for yes, 0.5 for partial. Age: 1 up to
  90 days, then halving every further 90 days.
- Reliability = fraction of a reporter's outcomes where the agent's claimed outcome equals the
  observed one; NULL below 5 such pairs. Needs the agent's claim kept even when a hook wins, so
  `report` stores it as `evidence.agent_outcome`.
- `commons rank` writes one snapshot per approved entry with score, retrieval_count_30d,
  used_rate (used yes/partial over outcomes with known `used`), success_rate. Locally the
  SessionStart hook starts it in the background when the newest snapshot is over 24 hours old;
  hosted compose runs it daily.
- Search keeps relevance × latest score (decision 0012); entries without outcomes score 0.5.

Consequences: scores are reproducible from outcomes. Self-report accuracy feeds weights but
never the success signal itself, as DESIGN §7 requires.
