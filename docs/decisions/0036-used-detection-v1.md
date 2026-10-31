# 0036. `used` detection v1: weighted artifacts, code and commands over prose (T41)
Status: Accepted (owner, 2026-10-31)
Date: 2026-10-31

Context: The Stop hook decides `used` by looking for a fix's distinctive pieces in what the
session did after the retrieval (decision 0016; T39 added timing and dropped punctuation).
On 2026-10-31 a deploy check counted as a partial use of the Keycloak entry:
- `re-run` and `--server` counted as distinctive.
- `sslrequired=external` matched because the agent had typed it into PROGRESS.md while
  writing about the entry.
- A passing `make check` then supplied `tests_passed`.
Standard practice for this kind of evidence matching weights terms by how rare they are
(TF-IDF style), so common tokens carry little. It keeps evidence channels separate and
weights them by reliability, and checks the detector against labeled examples rather than
anecdotes.

Decision:
1. Weight artifacts by rarity:
   - The server publishes a small table of artifact frequencies across all entries' fixes,
     computed with `commons rank`. Hooks fetch it once a day.
   - A built-in list of generic tokens weighs zero: common flags (`--server`, `--user`,
     `--force`, `-y`), ordinary hyphenated words (`re-run`, `built-in`, `up-to-date`), and
     bare localhost URLs.
   - `used` becomes the weighted share of artifacts present, with the same yes, partial and
     no cut points.
2. Separate channels. Spool records already carry their tool and, from T39, their time:
   - Commands run and code edits count fully.
   - Edits to prose files (`.md`, `.rst`, `.txt`, docs directories) count at zero, because
     writing about a fix is not applying it.
   - Text removed counts as it does now (a fix often names what to take out).
3. Pairing with a test run: `tests_passed` is attributed to a retrieval only when the
   matching code edit or command came before that test run. A passing run with no matched
   work keeps the outcome `unknown`.
4. Labeled set: each eval session (decision 0023) has a known truth, whether the agent
   applied the entry or not. That set, plus the two 2026-10-31 cases, becomes a test fixture.
   Report precision and recall of `used: yes/partial` before and after. The change ships if
   precision rises without losing recall on true uses.

Consequences: fewer false uses, so success rates mean what they say, which is the project's
central measurement. The frequency table is one more thing hooks fetch. Rarity is weak while
the store is small, which is what the built-in generic list is for. Answers part of DESIGN
§11's open question on whether diff matching is good enough.

Implementation notes (T46, 2026-10-31):
- The server computes artifact frequencies from approved fixes on request
  (`/api/hooks/artifact-frequencies`), which is cheap at this size, rather than in
  `commons rank`; hooks cache them for a day. Against an older server, hooks fall back to equal
  weights and still apply the generic list and the prose rule.
- Weights are a smoothed IDF, log((1 + N) / (1 + df)) + 1. The cut points are unchanged: at
  least half the weight is `yes`, some is `partial`.
- Only passing runs need matched work before them. Failing runs after the retrieval still count,
  so an agent's claim of success can be checked against them (reliability, decision 0017).
- Hooks send `matched_at` with `used`. Hooks installed before this release don't, and the
  server records their turns unpaired, as before; upgrading them (`uv tool upgrade commons`)
  turns pairing on.
- `requirements*.txt`, `constraints*.txt` and `CMakeLists.txt` are code, not prose.
- Labeled set (`test_used_labeled.py`, 12 sessions: 7 true uses, 5 not): precision 0.70 to
  1.00, recall 1.00 to 1.00. Eval transcripts are not kept, so the set is built from the eval's
  known-entry scenarios, the production entries, and the 2026-10-31 cases.
