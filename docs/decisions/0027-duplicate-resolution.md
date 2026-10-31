# 0027. Tunable duplicate resolution (T40)
Status: Accepted (owner, 2026-10-31)
Date: 2026-10-31

Context: The only guard against duplicates is the instruction "search before proposing; report
on an existing entry instead". When the agent skips it or words the search differently, a
near-identical entry reaches the review queue. Standard practice is to detect near-duplicates at
write time by similarity, then resolve them with a human in the loop: Q&A sites close a question
"as a duplicate of" a canonical one and keep the duplicate's wording as a signpost. The
threshold is tuned on labeled pairs and is a setting, because the right trade-off changes with
store size.

Decision:
1. Detection on every `propose`:
   - Score the proposal against approved and pending entries with the same retrieval as
     `search`.
   - Until 0025 lands, that is full-text plus trigram similarity on the symptom (`pg_trgm`).
     After 0025 it is the hybrid score.
   - Candidates above the threshold, at most 3, are stored on the new entry as
     `duplicate_candidates` (entry id and score).
2. Settings, server-side (environment variables, shown by `commons doctor`):
   - `COMMONS_DUPLICATE_THRESHOLD` (default tuned in step 5).
   - `COMMONS_DUPLICATE_ACTION`:
     - `flag` (default): create the entry as pending and show the candidates side by side in
       the review queue.
     - `return`: don't create it. Return the candidates and ask the agent to `report` on the
       one that matches, or to call again with `distinct=true` if its case really differs.
     - `link`: create it and link it to the best candidate for the reviewer to confirm.
   - `return` adds an argument and changes what `propose` replies, so it ships only after an
     eval run (decision 0023). `flag` changes nothing the agent sees.
3. Review actions on a flagged entry:
   - "Merge into": reject with reason "duplicate", set `superseded_by` (0026), and add the
     duplicate's symptom to the canonical entry as an alias that search also matches.
   - "Keep both": record that the pair is distinct so the pair is never flagged again.
4. Same task, same symptom: a second `propose` from the same task with a near-identical
   symptom is always returned as a duplicate. That case is a retry, not new knowledge.
5. Tuning:
   - Label pairs from the eval scenarios and from real proposals (duplicate or distinct).
   - Pick the threshold that keeps false merges of distinct entries at or below 1 in 20,
     maximizing caught duplicates under that.
   - Re-tune when the store grows tenfold.

Consequences: the review queue stops filling with repeats, and aliases improve recall for the
canonical entry. The risk is merging two entries that look alike but differ by version or
cause. The default action is only a flag, and "keep both" is remembered. Depends on 0026 for
`superseded_by`; works before 0025 with trigram similarity.

Implementation notes (T49, 2026-10-31):
- Detection uses search's lexical relevance (decision 0025) against approved and pending
  entries, with word frequencies over both. Trigram similarity was not needed.
- Labeled pairs: the judged set's 60 queries as duplicates of their entries, and 32 distinct
  proposals (its 12 negatives, and each entry's symptom with that entry excluded).

  | threshold | duplicates caught | false merges |
  |---|---|---|
  | 0.35 | 0.933 | 0.031 |
  | 0.40 (default) | 0.933 | 0.000 |
  | 0.50 | 0.817 | 0.000 |

- Built: `flag` (default) and `link`. `return` changes what `propose` replies, so it waits for
  an eval run and is refused as a setting for now. Same-task retries are therefore flagged
  as retries in the queue rather than returned to the agent.
- Merge rejects a pending duplicate (or retires an approved one) with `superseded_by` set,
  and appends its symptom to the canonical entry's `aliases`, which feed `search_vector`.
  Reviewers (review keys) may merge or keep both.
