# Exploration in search ranking
Status: Proposed
Date: 2026-11-07

Context: Search ranks by evidence, and evidence only reaches entries that agents are shown.
Today's order:
- An entry without outcomes scores 0.5 (`server/src/commons/store/search.py:42`).
- The score is the mean of decision 0017's Beta posterior, with a prior of 0.5 weighted as two
  outcomes (`server/src/commons/ranking/score.py:7-8`, `29-37`). Outcomes older than 90 days
  lose half their weight every further 90 days, so an unrenewed score decays toward the prior
  (`ranking/score.py:10-11`, `23-25`).
- Each hit scores relevance × version factor × that mean. The list is sorted, the same way
  every time, and cut at `limit` (`store/search.py:123-125`). This is the mean ranking.
- `limit` defaults to 5 (`server/src/commons/mcp/server.py:103`). Hits under the relevance
  cutoff of 0.25 are dropped before the sort (`server/src/commons/domain/search.py:111`).
- Every retrieval already logs its rank and score (`server/src/commons/store/retrievals.py:16-34`).

The swarm research for MIT-582 ("Swarm ideas for Agent Commons", rules 4 and 5) names the risk.
The first decent entry for a symptom is shown, earns outcomes, ranks higher, and crowds out a
better entry written later. Decay alone does not fix this: an entry below the cut earns nothing.
The research suggests showing a less-proven entry now and then; it calls the bandit framing its
own suggestion, not a tested result. Nobody knows yet whether Commons has this problem.
MIT-590 added two measures to the Searches page that can tell (`server/src/commons/store/insights.py`):
- **Full result sets** (`search_totals`, `insights.py:183-197`, the `full` count at line 192):
  searches over 30 days that returned as many hits as their limit, so more entries may have
  matched than the agent was shown.
- **Use by rank** (`use_by_rank`, `insights.py:200-221`): for ranks 1, 2, 3 and 4+, the
  retrievals over 30 days with a known use, and the share used.

Decision: set the trigger now, so exploration is switched on by numbers, not intuition. This
record changes no behavior. Exploration is built only when the trigger fires, and ships only
after the eval below.

1. The trigger. Zach sets the thresholds; these are suggested values. All three must hold on the
   Searches page:
   - Full result sets are at least 20% of searches over 30 days. Below that, few entries sit
     below the cut, and there is little to explore.
   - The share used at rank 1 is at least twice the share used at ranks 2-4+ (the rows
     for ranks 2, 3 and 4+, pooled).
     Agents then act mostly on the top result, so the top result collects most of the evidence.
   - The sample is large enough: at least 200 searches over 30 days, and at least 50
     retrievals with a known use at rank 1 and 50 at ranks 2-4+.
   A high share used at rank 1 can also mean the mean ranking is right. That is why the
   mechanism keeps proven entries on top and the eval decides.
2. The mechanism: a Thompson draw. For each hit above the cutoff, draw a success rate from the
   entry's 0017 Beta posterior, Beta(1 + Σw·s, 1 + Σw − Σw·s), in place of its mean. The hit
   scores relevance × version factor × the draw. Sort and cut at `limit` as today.
   - The score is already this posterior's mean, so the draw adds no tuning setting. Entries
     with much evidence draw close to their mean and keep their place. Entries with little
     evidence vary widely, so they sometimes rise. This is the exploration.
   - Exploration is one on/off switch, off by default, like the other server settings.
   - The relevance cutoff still applies first. A draw never shows an entry below 0.25.
   - Snapshots store only the mean (`store/migrations/0001_initial_schema.sql:142-153`), and the
     mean alone does not give the posterior. A migration adds Σw and Σw·s to
     `ranking_snapshots`, and `commons rank` writes them (`service/ranking.py:38-42`). An entry
     without them draws from the prior, Beta(1, 1).
   - The draw lives in `ranking`, which does no I/O. It takes a random generator as an argument.
3. What is logged, so rankings can be evaluated later. Each retrieval also records:
   - `explored`: the entry was shown, but the mean ranking would have cut it.
   - its probability of being shown in that search, estimated by repeating the draw (suggested
     200 times) over the same hits. With it, a later ranking can be scored on logged searches,
     each result weighted by 1 / its probability.
   The rank and score logged today stay; the score is the drawn one.
4. The `commons search` preview keeps the mean ranking (`server/src/commons/cli/main.py:408-418`,
   `service/search.py:36-39`). A human checking the store sees the same order every time.
   `make search-eval` and the relevance test (decision 0025) also measure the mean ranking.
5. Tests pass a seeded random generator, so every order they check is reproducible.
   Production uses an unseeded one.
6. The eval. Exploration changes result order, which is what the agent sees. So it is one
   decision 0023 condition, `exploration`, run on Sonnet and Opus against the baseline at the
   baseline's run count. Today's scenarios seed entries with no outcomes
   (`tools/eval/runner.py:150-169`), so no entry has evidence to lose its place. The condition
   needs scenarios where more entries match than are shown, some with an outcome history. The
   outcome history that MIT-588's record proposes for `seed()` is a prerequisite.
   Exploration ships only if, on both models, the rates of `used`, `tests_passed` and `report`
   do not fall against the baseline.
7. What it supersedes, once accepted:
   - In 0017, "Search keeps relevance × latest score (decision 0012)" becomes relevance × a draw
     from the posterior while exploration is on. The snapshot bullet gains Σw and Σw·s.
   - In 0025, step 3's "then apply the outcome score (decision 0017) as today" becomes "apply
     the draw". Step 5's rule (no step ships if precision@5 or recall@5 falls) is judged on the
     mean ranking. Exploration is judged by the eval in point 6.

Consequences: the trigger is checked from numbers the Searches page already shows. If it never
fires, nothing is built. If it fires, the change is small: one draw per hit, two snapshot
columns, two retrieval columns. Harder: search results stop being the same for the same query,
which makes a single bad result harder to reproduce; the logged probability and the preview's
mean ranking cover this. A new entry with no outcomes draws from a flat prior, so it can rise
above a proven one often; that is the cost of letting it earn evidence. Revisit the switch if
the share used at ranks 2-4+ falls after shipping, or if full result sets drop below the
threshold.

Options considered:
- Not now: keep the mean ranking; today's choice until the trigger fires.
- An epsilon slot: on a share of searches, put one low-evidence entry in the last slot; needs a
  new setting (the share) and ignores how much evidence each entry has.
- The Thompson draw: chosen; uses the posterior 0017 already defines, with no new setting.
- An upper credible bound (UCB): rank by a high quantile of the posterior; deterministic, but
  needs a new setting (the quantile) and keeps showing the same uncertain entry.
