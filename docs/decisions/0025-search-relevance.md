# 0025. Search relevance: measure, cut off, then hybrid retrieval
Status: Accepted (owner, 2026-10-31)
Date: 2026-10-31

Context: `search` ORs every query word (decision 0012), so any shared word matches. On
2026-10-31 deploy-check searches returned unrelated Cosmo entries for "error" and "check". As
the store grows, agents get noisy results, report them as unhelpful, and learn to skip commons.
Standard practice for this problem is: build a judged test set and measure first, add a
relevance threshold, then move to hybrid retrieval (lexical plus vector, fused by rank).
Domain filters such as versions come last.

Decision:
1. Measure first. Build a judged relevance set:
   - For every entry, 3-5 queries written the way agents actually search: the raw error line,
     a paraphrase, and the error with unrelated log noise around it.
   - Real queries from the `searches` table (decision 0024), labeled from `report`
     assessments.
   - Unrelated queries as negatives.
   Metrics: precision@5, recall@5, and "returned nothing when something relevant existed".
   Add a `make search-eval` command that prints them, so every later step is judged by numbers.
   The set lives in `server/tests/search_relevance/` as data.
2. Lexical fixes, which are cheap and ship first:
   - Weight terms by rarity across the store (IDF), so "error", "failed", "check" and
     "exception" count for little.
   - Score by query coverage: what share of the query's distinctive terms an entry matches.
   - Add a minimum-score cutoff tuned on the set to hold precision@5 at or above a target the
     owner picks (suggested 0.8).
   - Below the cutoff, return nothing: an empty result already invites `propose`.
3. Hybrid retrieval with pgvector, already planned in DESIGN §2:
   - Store an embedding of symptom + cause in a `vector` column with an HNSW index. The fix
     is excluded: it describes the cure, not the problem.
   - Embed the query at search time.
   - Merge the lexical and vector rankings with Reciprocal Rank Fusion (k = 60), then apply
     the outcome score (decision 0017) as today.
   - The embedding model is chosen on the relevance set: a hosted embedding API (one new
     secret, a small cost per call) against a small local model (no cost, but Cloud Run memory
     and a cold-start penalty). Embeddings are computed on approve, not on propose, so pending
     text never leaves for a third party unreviewed.
4. Version awareness:
   - When the query's `environment.libraries` names a library the entry also names, compare
     versions with the ecosystem's own rules (PEP 440 via `packaging`, npm semver).
   - Boost a same-major.minor match; demote a different major.
   - With 0026, an entry can carry `fixed_in`; hide it from callers at or past that version.
5. Rollout: each step lands behind the relevance numbers. A step that lowers precision@5 or
   recall@5 against the previous one does not ship.

Consequences: search stays useful as entries accumulate, and the cutoff makes "nothing found"
honest, which also makes the `searches` miss list (decision 0024) a better signal. Costs: an
embedding dependency and a migration that backfills vectors. The cutoff can hide a relevant
entry when the query is poorly worded; recall@5 on the set guards against that. Revisit the
model choice when the store passes ~1,000 entries.

Implementation notes (T47, 2026-10-31):
- Judged set: `server/tests/search_relevance/judged.json`, 20 entries (the seeds, the eval's
  scenario entries, the production entries, and 12 well-known public problems), 60 answerable
  queries (raw error line, paraphrase, error inside log noise) and 12 negatives, including the
  2026-10-31 deploy-check queries. The real `searches` table had only those deploy checks, so
  it could not supply labeled queries yet. `make search-eval` prints the metrics, and
  `test_search_relevance.py` fails if precision@5 or recall@5 drops below the recorded numbers.
- Lexical v1: candidates still share at least one stemmed word. Each is scored by the
  geometric mean of two rarity-weighted coverages: of the entry's symptom by the query, and of
  the query's known words by the entry. Words the store has never seen don't count, so log
  noise doesn't hurt. A short list of generic words (error, failed, exception names, ...)
  weighs a tenth. The cutoff is 0.25, the highest that keeps recall@5.

  | | precision@5 | recall@5 | empty when answerable | noise on negatives |
  |---|---|---|---|---|
  | v0 (0012) | 0.243 | 0.983 | 0.000 | 0.750 |
  | v1 | 0.899 | 0.983 | 0.017 | 0.333 |

- Hybrid retrieval (step 3) is not built: the owner chose to decide on the numbers, and lexical
  v1 holds recall on paraphrases in this set. Revisit when real `searches` misses show
  paraphrase failures, or past ~1,000 entries.
- Versions: the leading major.minor is compared, where PEP 440 and npm semver agree. A shared
  library on a different major multiplies the score by 0.5; the same major.minor by 1.2.
  Hiding entries `fixed_in` an older version waits for 0026's `fixed_in`.
