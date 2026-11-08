# Outcome signals: what counts as an entry working
Status: Proposed (MIT-603 for MIT-567; Zach picks the option)
Date: 2026-11-07

Context: Ranking is computed from outcomes, so which outcomes count, and how much, decides what
"this entry works" means. Today the only observed signal is a test run. Nothing observes
whether a human kept the change, and nothing notices a later revert. The kill criterion
(`2026-11-07-MIT-567-kill-criterion.md`) uses test signals only, so this record is about the
ranking signal outside that experiment.

What counts today (decision 0017):
- An outcome counts only when `used` is yes or partial, and is skipped when it is `unknown` or
  `abandoned` (`server/src/commons/store/ranking.py:17-18`).
- Success means an outcome in `SUCCESS_OUTCOMES`: `tests_passed`, `error_resolved`,
  `user_accepted` (`server/src/commons/domain/outcomes.py:21`). Everything else counts as a
  failure.
- Weight = source × use × age (`server/src/commons/ranking/score.py:15-26`):
  - source: 1 for a hook-observed outcome; for a self-report, the reporter's reliability, or
    0.5 while it is unknown (`score.py:9`, `score.py:17-22`);
  - use: 1 for yes, 0.5 for partial (`score.py:12`);
  - age: 1 up to 90 days, then halving every further 90 days (`score.py:10-11`, `score.py:23-25`).
- Score = a Beta prior with mean 0.5 and the weight of two outcomes, plus the weighted
  outcomes (`score.py:7-8`, `score.py:29-37`).
- Reporter reliability is the share of self-reports whose success matches the observed test
  outcome, unknown below 5 pairs (`server/src/commons/ranking/reliability.py:9-27`).
- Hooks observe only `tests_passed` and `tests_failed` (`domain/outcomes.py:109`,
  `reliability.py:7`), and an observed outcome is never overwritten by a self-report
  (`domain/outcomes.py:70-72`).

Gaps:
- No observed human acceptance. `user_accepted` and `user_rejected` come only from the agent's
  own report, so they are self-reports weighted by reliability.
- No revert detection. A fix that passes its tests and is reverted the next day still counts
  as a success.

Decision: Zach picks one:
- (a) Keep as is. Test runs are the only observed signal; acceptance stays self-reported.
- (b) A delayed observed signal. A later SessionStart in the same repo checks locally whether
  the change from an earlier task survived or was reverted. A revert counts as a failure. A
  change that survived counts as a success at about 0.5 weight, since survival is weaker
  evidence than a passing test. The check runs on the machine; only derived data, such as
  "survived" or "reverted" and the hashes already stored, leaves it (decision 0016).
- (c) Infer acceptance from user messages, for example "thanks, that works". This is noisy,
  and it means reading the user's private text.

**Recommended:** (a) for the 20-task run, then (b) as a later hook ticket.

Consequences: (a) changes nothing, so the 20-task run measures the system as it is, and it
uses test signals anyway. It leaves ranking blind to reverts. (b) adds the first observed
signal that a human kept a change, with no code content leaving the machine, but only when the
same repo gets another session; one-off repos never produce it. It also needs a rule for which
later edits count as a revert rather than ordinary change. (c) would cross the privacy line of
decision 0016 for a weak signal; it is worth a look only if (b) proves too sparse. We'd revisit
after (b) has run long enough to show how often it fires.
