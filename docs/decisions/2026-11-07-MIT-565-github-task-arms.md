# GitHub-task arms: the run, fixed before it starts
Status: Proposed (MIT-617 for Commons ticket MIT-565; the run waits for question MIT-619)
Date: 2026-11-07

Context: The Proposed kill-criterion record (`2026-11-07-MIT-567-kill-criterion.md`, on its
own branch) defines when to stop building agent-facing features. Its recommended option (a)
compares a Commons arm with a Context7 arm on real GitHub tasks. The harness for that run is
built on `feature/MIT-565-github-tasks`. It has the GitHub task records, four arms with their
tokens, retrievals and patches, an experiment store, counterfactual pairs, and the `compare`
report. This record fixes everything the verdict depends on before any session runs, so no
number can be argued after it is seen. Question MIT-619, on the task-set size and the store,
is still open. **The run waits for its answer.** The run counts below are the recommended
defaults until then.

**Amended 2026-11-11 (MIT-679).** Items 3 and 9 and the run sequence in item 4 are amended
below, each marked "Amended 2026-11-11". The reasons, from the first GitHub-task runs:

- **The tasks were too easy.** Sonnet and Haiku passed all 10 tasks in
  `tools/eval/github_tasks.py` with no tools, so no arm could do better than another. Those
  10 are now the smoke test (`SMOKE_TASKS`, `make eval-smoke`, condition `github-smoke`),
  which checks the harness end to end and never enters a verdict.
- **There were no siblings.** None of the 10 shares an `upstream_change`, so folds A and B
  were empty and every task fell into the same-task set, whose result is inflated by design.
- **The review let a workaround through.** On `gh-table-extraction-bool-form`, 4 of 6 Commons
  sessions copied an approved entry's workaround fix. The review had no step that compared an
  entry's fix with the task's upstream fix.

Decision:
1. **Arms.** All four (`tools/eval/runner.py`, `ARM_SERVERS`):
   - `both`: the Commons plus Context7 (the kill-criterion record's Commons arm);
   - `context7`: Context7 alone (its Context7 arm);
   - `commons`: the Commons alone;
   - `none`: blind, no MCP server.

   The **primary comparison**, the only one that decides, is `both` against `context7`. The
   secondary comparisons, `commons` against `none` and `context7` against `none`, are
   reported and decide nothing.
2. **Tasks and run counts (recommended defaults, pending MIT-619).** Every task in
   `tools/eval/github_tasks.py`: 10-20 accepted tasks (`docs/eval/README.md`, "GitHub tasks").
   Each task runs **3 times in each arm** (`EVAL_ARMS_RUNS=3`) on **one model, Sonnet**. That
   is 4 × 3 × tasks sessions, 240 at 20 tasks. Sessions are interleaved task × arm × repeat,
   and each records its repeat, so the same repeat of a task pairs across arms.
3. **Folds.** As in the kill-criterion record (§7), no task is measured on entries its own
   sessions wrote. Siblings are tasks with the same `upstream_change`. The tasks are split
   once, before any seed wave, by this rule: within each group of siblings, sorted by name,
   tasks alternate between fold A and fold B (A, B, A, ...). A task with no sibling goes to the
   **same-task** set. The three lists are written into this record (a table under this item)
   when the task set is checked in, and are not changed after the first seed wave.

   *Amended 2026-11-11.* The same-task set gets a verdict of its own, but one that can only
   be **kill** or **not decided**, never continue. Its sessions can find entries their own
   task's sessions wrote, so a win there is inflated and proves nothing. A loss even with that
   help still counts. The rule is item 8's kill rule with the same constants (`PASS_MARGIN`,
   `TOKEN_MARGIN`, `PASS_FLOOR`): where item 8 says kill, the same-task verdict is kill, and
   otherwise it is not decided (`same_task_verdict` in `tools/agent_eval.py`). It applies to
   `github-arms-same-task` and to every condition in `SAME_TASK_CONDITIONS`, which also lists
   the 2026-11-11 run `github-same10`. The fold verdict of `github-arms` is unchanged.
4. **Stores.** One experiment store per fold, plus one for the same-task set, all on the local
   Postgres. The harness refuses any database not on `localhost` or `127.0.0.1` (the rule
   `scripts/test-db.sh` follows), so none is ever the live Commons database.

   | Store | Seeded by | Measured on it | Measured condition |
   |---|---|---|---|
   | `commons_eval_store_a` | fold A's tasks | fold B's tasks | `github-arms` |
   | `commons_eval_store_b` | fold B's tasks | fold A's tasks | `github-arms` |
   | `commons_eval_store_same` | the same-task set | the same-task set | `github-arms-same-task` |

   - A **seed wave** (`arms --store NAME --seed-wave`) writes every session into the store
     itself. The store is created and migrated if it doesn't exist, and is never dropped.
   - The owner then reviews what each wave proposed, against its store (`commons pending`,
     then `commons review`, with `COMMONS_DATABASE_URL` set to the store). Search shows only
     approved entries, so this review is what the measured sessions can find.
     *Amended 2026-11-11:* the reviewer checks each pending entry's fix against the upstream
     fix (`fix_url`) of the task that proposed it. An entry whose fix is a workaround is
     rejected with that reason, or edited to the upstream fix. `agent_eval.py review-sheet
     --store NAME` prints the sheet for this: each pending entry's id, symptom, cause and fix
     next to its task's `fix_url` and `issue_url`. It reads only, and links an entry to its
     task by the task ids the session's ledger line records. An entry it can't link shows
     "task unknown".
   - In a **measured run** (`make eval-arms`), each session gets its own
     `CREATE DATABASE ... TEMPLATE <store>`, dropped after grading. Every session of a fold
     starts from the same reviewed store, which only the *other* fold's tasks seeded, and never
     sees another measured session's work.
   - Grading counts a session's proposals and reports by its own task ids: the tasks its hooks
     started under its Claude session ids, plus every task id it passed to a commons tool.
     It never counts by the shared `eval-agent` pseudonym.

   The run, in order. `<A>`, `<B>` and `<same>` are the fold lists from item 3, written as
   `--scenario T` per task in the `arms` commands and space-separated in `EVAL_ARMS_TASKS`
   (*amended 2026-11-11:* step 0, the review in step 2, and the combined verdict in step 4):
   ```
   # 0. The smoke test: the 10 smoke-test tasks, all four arms, as github-smoke (no verdict).
   make eval-smoke
   # 1. Seed waves: the commons arm, once per task, into each fold's own store.
   uv run python tools/agent_eval.py arms --store commons_eval_store_a --seed-wave \
     --arm commons --runs 1 --condition github-arms-seed-a <A>
   uv run python tools/agent_eval.py arms --store commons_eval_store_b --seed-wave \
     --arm commons --runs 1 --condition github-arms-seed-b <B>
   uv run python tools/agent_eval.py arms --store commons_eval_store_same --seed-wave \
     --arm commons --runs 1 --condition github-arms-seed-same <same>
   # 2. Review each store's pending entries (repeat for _b and _same): check each fix against
   #    its task's upstream fix, and reject a workaround with that reason or edit it.
   uv run python tools/agent_eval.py review-sheet --store commons_eval_store_a
   COMMONS_DATABASE_URL=postgresql://commons:commons@localhost:5432/commons_eval_store_a \
     uv run commons review
   # 3. Measured runs: each fold on the other fold's store.
   make eval-arms EVAL_STORE=commons_eval_store_b EVAL_ARMS_TASKS="<A>"
   make eval-arms EVAL_STORE=commons_eval_store_a EVAL_ARMS_TASKS="<B>"
   make eval-arms EVAL_STORE=commons_eval_store_same EVAL_ARMS_TASKS="<same>" \
     EVAL_ARMS_CONDITION=github-arms-same-task
   # 4. The fold verdict, the same-task set's kill-only verdict, and the combined verdict (item 9).
   make eval-compare EVAL_ARMS_CONDITION=github-arms
   make eval-compare EVAL_ARMS_CONDITION=github-arms-same-task
   ```
   Both folds are measured under `github-arms`, so the fold verdict pools them. The seed
   waves' conditions, `github-smoke` and `github-arms-same-task` are never part of it:
   `compare` reads one condition. Only the combined verdict (item 9) reads both `github-arms`
   and `github-arms-same-task`, and no verdict ever reads `github-smoke`.
   `eval-arms` refuses to run without both `EVAL_ARMS_TASKS` and `EVAL_STORE`.
5. **Pass.** A session passes when the harness's own test run after the session passes
   (`tests_pass`). Hook outcomes, the Commons' outcome rows and agent self-reports are not
   used.
6. **Tokens.** A session's tokens are `tokens_in + tokens_out`: input, cache creation, cache
   read and output tokens, summed over every result event (one per turn). **Tokens per
   success** is all the tokens an arm spent on the compared tasks, divided by its passed
   sessions.
7. **Interval.** A paired bootstrap over tasks. Only tasks that both arms ran are compared.
   Each resample draws that many tasks with replacement and keeps every session of a drawn
   task, in both arms, together. Then it recomputes the pass-rate difference and the
   tokens-per-success ratio. There are 10,000 resamples (`BOOTSTRAP_RESAMPLES`) with seed
   20261107 (`BOOTSTRAP_SEED`), so the same ledger always gives the same interval. The
   interval is the 2.5th to 97.5th percentile. If more than one model ran, models are pooled
   and tasks stay the resampling unit.
8. **Margins.** These are the kill-criterion record's option (a), as named constants in
   `tools/agent_eval.py`:
   - `PASS_MARGIN = 0.10`: a pass rate at least 10 points higher;
   - `TOKEN_MARGIN = 0.80`: tokens per success at least 20% lower;
   - `PASS_FLOOR = -0.05`: and in that case, a pass rate no more than 5 points lower.

   The verdict is one of three:
   - **Continue** if the pass-rate difference is at least `PASS_MARGIN` and its interval
     lies above zero. Or if the tokens ratio is at most `TOKEN_MARGIN`, its interval lies
     below 1, and the pass-rate difference is at least `PASS_FLOOR`.
   - **Kill** if the intervals rule out both margins. The pass-rate interval's top must be
     below `PASS_MARGIN`. The tokens interval's bottom must be above `TOKEN_MARGIN`, or the
     pass-rate interval's top below `PASS_FLOOR`.
   - **Inconclusive** otherwise.
9. **The verdict command.** After both folds' measured runs, the verdict is the `verdict:` line
   of
   ```
   make eval-compare EVAL_ARMS_CONDITION=github-arms
   ```
   which runs `uv run python tools/agent_eval.py compare --condition github-arms`. It reads
   only the committed ledger, `docs/eval/trials.jsonl`, and only `github-arms` lines: both
   folds, never the seed waves or `github-arms-same-task`. Lines marked `invalid` are not
   counted. Errored sessions never reach the ledger, and `RESUME=1` runs them again.

   *Amended 2026-11-11.* The decision is the **combined verdict**: **kill** if the folds'
   verdict or the same-task set's verdict (item 3) is kill, **continue** only if the folds'
   verdict is continue, and inconclusive otherwise. `compare` prints it as a
   `combined verdict, grader vN:` line, under the report of either `github-arms` or
   `github-arms-same-task`, once both conditions have counted rows of that grader version.
   Until then the folds' `verdict:` line stands alone. `compare --condition github-smoke`
   prints `verdict: none`: the smoke test never enters a verdict.
10. **Reported, not deciding.** `compare` also prints:
    - per arm and model: sessions, the pass rate with a 95% Wilson interval, tokens per
      success, median tokens and median wall clock, and, for the Commons arms, how many
      sessions searched, found, used and proposed;
    - the two secondary comparisons;
    - herding. That is the failed sessions with the Commons whose retrieved entry was used,
      and, per arm, the share of failed sessions whose patch is their task's most common wrong
      patch (by `patch_hash`, when at least two failed sessions share it).

    `agent_eval.py counterfactuals --condition github-arms --store commons_eval_store_b`, and
    again with `--store commons_eval_store_a`, writes the DESIGN.md §7 pairs to each store's
    `counterfactual_runs` for the per-entry effect estimates later. Each run writes the pairs
    whose entry that store holds and skips the rest. The pairs are `both` with `context7` and
    `commons` with `none`, for the same task, repeat and model.

Consequences: Once MIT-619 is answered and this record is accepted, the run is the sequence in
item 4: three seed waves, a review of each store, and three measured runs (one per fold, one
for the same-task set). The verdict comes from one command that reads only the ledger. Splitting
siblings across folds keeps every measured task off entries its own sessions wrote, but a fold
can only learn from its siblings, so the Commons arms see less than a single shared store
would give them; that understates the benefit rather than inflating it. Tasks without a
sibling are measured anyway, under their own condition, so their inflated result is visible but
never decides. At 3 runs × 20 tasks the pass-rate interval is about ±12 points, wide next to
a 10-point margin. So inconclusive is a likely and honest answer, and it calls for more tasks,
not a looser margin. Clones keep the measured sessions independent, but every session of a
fold sees the same seeded entries. A bad entry approved in the review can mislead every Commons session
at once, which the herding section is there to show. Pooling models hides a difference between
them. The default run has one model, so pooling only matters if MIT-619 adds one. If the two
Proposed records end up disagreeing on a margin, the kill-criterion record wins, and the
constants change before the run, never after.
