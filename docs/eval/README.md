# Behavioral eval results

Raw per-session results from `tools/agent_eval.py` (decision 0023). One JSON file per session:
`<condition>-<scenario>-<run>.json`. Transcripts stay out of the repo (temp paths, size).

| Condition | Date | Model | Sessions | What it measures |
|---|---|---|---|---|
| `baseline` | 2026-10-25 | sonnet | 65 (13 scenarios × 5) | The system as shipped, before any change to what the agent sees |
| `baseline-holdout` | 2026-10-25 | sonnet | 20 (4 real-change scenarios × 5) | Same system on the hold-out set (real post-cutoff breaking changes) |
| `dev-examples` | 2026-10-25 | sonnet | 4 (2 dev scenarios × 2) | Variant `examples` (worked argument shapes in tool descriptions), development check only |
| `verify-hard` | 2026-10-25 | sonnet | 4 (hard set × 1) | Verification of the hard set: obvious fix must fail (see decision 0023) |
| `baseline-hard` | 2026-10-25 | sonnet | 20 (hard set × 5) | Same system on the hard set: search 4/20, propose 0/20, fixed 20/20 |
| `rule` | 2026-10-25 | sonnet | 20 (hard set × 5) | Propose rule keyed on the cause (library or version), not attempt count |
| `start` | 2026-10-25 | sonnet | 20 (hard set × 5) | SessionStart context asks for a search on the first failure |
| `invite` | 2026-10-25 | sonnet | 20 (hard set × 5) | An empty search result points at `propose` |
| `nudge` | 2026-10-25 | sonnet | 20 (hard set × 5) | **Invalid**: Stop hook could not see failing runs (piped output, PR #52) |

Summarize with `uv run python tools/agent_eval.py report --condition <name>` after copying a
condition's JSON files into `.scratch/eval/<name>/`.

## Trial ledger

`trials.jsonl` is append-only: one line per session, stamped with the **variant** name and a
**text fingerprint** (a hash over every surface the agent sees under that variant: instructions,
tool descriptions, hook messages). Change one word and the fingerprint changes, so a result can
always be traced to the exact text that produced it. `uv run python tools/agent_eval.py trials`
summarizes it per variant, fingerprint, and scenario kind. The three baselines were back-filled
from their committed results.

## Baseline (2026-10-25)

Discovery scenarios (45 sessions): fixed the bug 45/45, searched 4/45, proposed 2/45 (both
on-topic, correctly caused, version-pinned). Controls (20): searched 1/20, proposed 0/20.
Known-entry scenario: 1/5 searched, found, used (`yes`, hook-observed `tests_passed`), and
reported. Injection scenario: no session searched, so the planted instructions were never seen.

By trigger: contradicted docs 2/20 searched, 2/20 proposed; version-specific 2/15, 0/15;
multi-attempt 0/10, 0/10. Mean test attempts 1.9-2.6, which says the scenarios are easy enough
to feel routine: searching never happened in a one-attempt session and rose only slightly with
attempts (3/27 at two attempts, 1/6 at three).

Reading: agents are strong at the task and at restraint, and weak at consulting the commons at
all. Search is the bottleneck; propose can't happen without it. The scenario bugs are familiar
patterns (mutable defaults, silent kwargs, half-open ranges), which the eval model has seen
many times even with invented library names; the hold-out set should use real post-cutoff
breaking changes instead (see decision 0023, amendment).

## Hold-out baseline (2026-10-25)

Real post-cutoff changes (SQLModel 0.0.45, whenever 0.11.0, Python 3.14 sqlite3 and itertools
removals): fixed 20/20, searched 0/20, proposed 0/20, mean attempts 1.0-2.8. Transcripts show
the agent recognizing the version change ("Python 3.14 removed copy and pickle support …") and
deciding "the fix was routine". The bottleneck is the decision to consult the commons at all.

## Condition `examples` (development check, 2026-10-25)

Worked argument shapes in the `propose`/`search` descriptions. 0/4 searched on the development
scenarios. Expected in hindsight: clearer arguments help an agent that has already decided to
call the tool; they can't change that decision. Kept as a variant (it addresses the 3-5 failed
calls seen in every baseline proposal) but not measured on the hold-out, since it cannot move
the metric that is at zero.

## Structural screen (2026-10-25)

One surface changed per condition, 20 sessions each on the hard set, against `baseline-hard`.
Intervals are 95% Wilson.

| Condition | Search | 95% CI | Propose | 95% CI | Good proposals | Fixed |
|---|---|---|---|---|---|---|
| `baseline-hard` | 4/20 | 8-42% | 0/20 | 0-16% | 0 | 20/20 |
| `rule` | 2/20 | 3-30% | 1/20 | 1-24% | 1 | 20/20 |
| `start` | **20/20** | 84-100% | 0/20 | 0-16% | 0 | 20/20 |
| `invite` | 1/20 | 1-24% | 0/20 | 0-16% | 0 | 20/20 |
| `nudge` | invalid | | | | | |

- **`start` solves search.** Asking for a search in the session context, once, took search from
  4/20 to 20/20, the only interval in the screen that does not overlap the baseline. It cost
  two turns per session (8.3 vs 6.3) and nothing in fix rate.
- **Searching does not lead to proposing.** In all 20 `start` sessions the search came back
  empty, the agent fixed the bug, and then closed with some form of "the search returned no
  entries, so there was nothing to report." Two said why they did not propose: "since this was
  a routine fix" and "since this was a quick fix in this project's own code" (the second was a
  SQLAlchemy 2.1 behavior change). One tried to propose and was rejected on argument shape
  (`environment.tool must include a version`) and did not retry.
- **`rule` produced the screen's only complete loop**: searched, found nothing, fixed, proposed
  a correct, version-pinned entry, in 1 of 20. Its effect on its own is not distinguishable from
  zero at this sample size, and it can only act in sessions that search.
- **`invite` never got the chance**: it changes the empty-search result, and only 1 of 20
  sessions searched.
- **`nudge` never fired.** Every eval session (210 of 210) ran its tests as
  `pytest -q 2>&1 | tail -n`, so the shell's exit code was tail's 0 and the hooks saw no failing
  run. This was a real bug in hook-observed outcomes, fixed in PR #52. The 20 trials stay in the
  ledger marked `invalid` and are not counted.

Next: the surfaces act at different steps of the funnel, so they are combined on top of `start`
rather than picked between: `start` delivers the search, `invite` speaks at the empty result,
`rule` removes the "routine" reason, and worked argument shapes keep a first propose from being
rejected.
