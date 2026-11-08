# Kill criterion: when to stop building agent-facing features
Status: Proposed (MIT-603 for MIT-567; Zach picks the margin)
Date: 2026-11-07

Context: The Commons has no test yet of whether it makes agents better at real work. Decision
0023 measured behavior (search, propose, report), not benefit, and in it every bug was fixed.
We need a rule, fixed before the run, that says whether to keep building agent-facing features
or stop. The Context7 fallback (Commons ticket `MIT-564`) and the swarm research (`MIT-582`)
restate this criterion, so this record is the one place it is defined.

Decision:
1. **Arms.** Two arms:
   - the Context7 arm: Context7 alone;
   - the Commons arm: the Commons plus Context7.
2. **Task set.** Commons ticket `MIT-565`: 10-20 real GitHub tasks. Its harness is being built
   on `feature/MIT-565-github-tasks`. Each task runs several times in each arm, and results are
   paired by task.
3. **Metrics.** Two:
   - pass rate: the share of runs whose task tests pass;
   - tokens per passed task: all tokens an arm spent, divided by its passed runs. Tokens are
     input + cache creation + cache read + output, taken from each session's final usage.
4. **Interval.** Every comparison uses a 95% paired-bootstrap interval: resample tasks with
   replacement, keep each task's runs in both arms together, and recompute the difference.
5. **Margin.** Zach picks one:
   - **(a) Recommended.** Continue if the Commons arm's pass rate is at least 10 points
     higher, or its tokens per passed task are at least 20% lower with a pass rate no more
     than 5 points lower. The interval for the deciding metric must exclude zero. Kill when
     the interval rules the margin out: the best case it allows is below the margin for both
     metrics. Anything else is inconclusive.
   - (b) The same rule at +5 points / 10% lower tokens.
   - (c) Tokens only. Continue if the Commons arm's tokens per passed task are at least
     15-20% lower, with the interval excluding zero. Pass rate does not decide.
   - (d) Continue on any improvement in either metric whose 95% paired-bootstrap interval
     excludes zero.
6. **Sample size.** At 3 runs × 20 tasks per arm, the pass-rate interval is about ±12 points.
   That is wide next to a 10-point margin. There is also a ceiling: in decision 0023 agents
   fixed every bug, so both arms may pass nearly everything. If so, tokens per passed task
   likely decide.
7. **Seeding (cold start).** An empty Commons cannot help, so the Commons arm is seeded first.
   A seeding wave runs on sibling tasks: other tasks hit by the same upstream change, split
   into folds so no task is measured in the wave that seeded it (cross-fold). Where a task has
   no sibling, the seeding wave runs on the same task, and its results are labelled as seeded
   from the same task and reported apart. Entries from the seeding wave go through the usual
   approval before the measured runs, since search does not show pending entries (it shows
   approved ones, and retired ones that have a fix version).
8. **Signals.** Only test signals count for this experiment: a run passes when its task's tests
   pass. Agent self-reports and the Commons' own outcome rows are not used.
9. **What stop means.** Kill means no more agent-facing features: no new tools, instructions
   or hook prompts aimed at changing agent behavior. Data collection continues: hooks keep
   recording outcomes, and the store keeps running.

Consequences: The run has a fixed rule before any number is seen, so a result cannot be argued
after the fact. Option (a) can return inconclusive at this sample size; that is the honest
answer, and it calls for more tasks, not a looser margin. Option (c) bets on the ceiling: it
ignores pass rate, so it could continue even if the Commons arm passed fewer tasks. Option
(d) is the most permissive: it could keep a feature line alive on a trivial gain. Cross-fold
seeding needs tasks that share an upstream change; if few do, most results carry the seeded-from-the-same-task label, which
overstates the benefit. We'd revisit the margin if the task set grows past 20 or the ceiling
makes pass rate useless.
