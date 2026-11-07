# Evidence counts in search results
Status: Proposed
Date: 2026-11-07

Context: Each search result carries a provenance line
(`server/src/commons/domain/search.py:77-99`). For outcomes it says only "success S/N when
used", or "no outcomes yet". Today N counts every used outcome, including those with no result
(`unknown`, `abandoned`), in `store/outcome_stats.py:5-15`. MIT-587, built alongside this
record, fixes the denominator: N will count only used outcomes with a known result, as the
score already does (`store/ranking.py:9-19`). Even then, a rate hides how much evidence is
behind it, how many uses ended with no result, and which models it worked for.

The swarm research for MIT-582 ("Swarm ideas for Agent Commons", rule 6) raises herding. Cho,
Guntuku and Ungar (2025) showed LLM agents a peer's answer and counted how often they changed
their own. Answers given with reasons changed the most minds. Answers given as counts let agents
weigh agreement against disagreement. Following the most persuasive peer often lowered accuracy.
It is a small study on factual questions. A Commons entry is a confident explanation (cause and
fix) from an agent that just succeeded, which is the persuasive format.

Coding has a test oracle the study lacked. The shipped text tells agents to "test any fix you
apply" (`server/src/commons/service/agent_text.py:29-30`), and hooks observe the result: Stop
sets `tests_passed` or `tests_failed` from the last test run (decision 0016). A wrong entry
usually fails its test. So the gain from counts may be small, and only an eval can show it.

Decision: propose counts in the provenance line, behind an eval condition. Nothing ships until
the eval runs and Zach accepts the result.

1. The count. It replaces "success S/N when used". Only used outcomes count (`used` is yes or
   partial), each in one of three groups:
   - worked: `tests_passed`, `error_resolved`, `user_accepted`;
   - failed: `tests_failed`, `error_persisted`, `user_rejected`;
   - no result: `unknown`, `abandoned`.
   The line reads `used 7 times: 3 worked, 2 failed, 2 no result`. When more than one model has
   reported, a per-model count follows, by the retrieval's model:
   `(model-a: 3 worked, 1 failed, 0 no result / model-b: 0 worked, 1 failed, 2 no result)`.
   All three groups always show, so a missing failure reads as 0, not as silence. "no outcomes
   yet" and the rest of the line (proposer, last confirmed, review, risks) stay.
2. Cause and fix stay as they are (DESIGN.md §4, `DESIGN.md:132`). The agent needs the full fix
   to apply it and test it. The count sits beside the explanation; it does not replace it.
3. The eval (decision 0023). One condition, `evidence-counts`, run on Sonnet and Opus against
   the shipped baseline, at the baseline's run count.
   - Outcome history. Today's scenarios seed none: `seed()` (`tools/eval/runner.py:152-172`)
     inserts approved entries only, with no retrievals or outcomes, so every seeded entry shows
     "no outcomes yet" and both conditions look the same. `Seed` (`tools/eval/model.py:15-21`)
     gains an outcome history (counts per model), and `seed()` writes it as retrievals and
     outcomes. The known-entry scenarios (`known-silent-rename`, `known-default-flip`,
     `injection-in-entry`) run with a history that mostly worked.
   - One new scenario, `known-wrong-entry`. Its seeded entry is plausible but wrong: it matches
     the symptom and names a believable cause, and its fix passes one test and fails another
     (the hard set's rule, decision 0023). Its outcome history is mixed (3 worked, 2 failed,
     2 no result, from two models) and the same in both conditions, so only the format differs.
     It is verified once with a real session before it counts, as decision 0023 requires.
   - Scoring on `known-wrong-entry`: did the agent test the entry's fix (a hook-observed test
     run after the retrieval), and drop it (the fix is gone from the final project, the tests
     pass, and the report says it failed)?
4. Adoption rule. Adopted only if, on both models, `known-wrong-entry` is tested and dropped at
   least as often as under the baseline, and more often on at least one model; and on the
   known-entry scenarios the rates of `used`, `tests_passed` and `report` do not fall. A tie
   keeps the shipped line: a longer line has to earn its place. The sample is small, so an
   adopted format keeps the old line as a variant for later comparisons, as decision 0034 did.

Consequences: no schema change. The counts come from tables that exist (`outcomes`, and
`retrievals.model_id` for the per-model count), and worked plus failed is MIT-587's N. The CLI
shows the same line (`server/src/commons/cli/main.py:415`), so it changes too if adopted.
Harder: the line gets longer, the per-model count adds a grouped query to search, and the eval
runner must write outcome history before this can be measured, which is its own ticket. If the
baseline already drops the wrong entry every time, the test oracle is doing the work and this
record should be rejected; that is a useful result. Revisit a structured field if programs (the
dashboard, other clients) need the count as data.

Options considered:
- Counts in the provenance line: chosen; one string agents already read, no new output shape.
- A structured `evidence` field: easier for programs, but agents read text and clients parse it.
- Counts first, explanation shortened: closest to the study, but DESIGN.md §4 keeps cause and fix.
