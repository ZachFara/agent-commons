# Entry flag classifier: an admin-only model that flags likely-problem entries
Status: Proposed
Date: 2026-11-08

Context: Question `MIT-613` item 1 asked whether entries should carry an agent-facing
`harmful=true` flag. The owner said no. He wants a small model, such as logistic regression on
entry vectors, that flags likely-problem entries on the admin dashboard and has its own
observability and MLOps. Today's guards are all rules: the risk scanner (0029), duplicate
detection (0027), the failure-based reopen and failure-rate list (0026 amendment), and the
stale and never-used lists. This record sets out the model's shape. Nothing is built.
Paths are under `server/src/commons/` unless they say otherwise. Counts were taken on
2026-11-11 against local databases only.

Two facts about the code change the brief:
- **Commons stores no embeddings.** `entries` has no vector column
  (`store/migrations/0001_initial_schema.sql:48-70`, and no later migration adds one). No
  migration runs `CREATE EXTENSION vector`. Search and duplicate detection are lexical
  (`store/search.py:52-54`, `store/search.py:179-212`), and no embedding package is a
  dependency (`server/pyproject.toml:6-20`). Only the Postgres image ships pgvector
  (`db/compose.yml:7`). Hybrid retrieval, decision 0025 step 3, is "not built"
  (`docs/decisions/0025-search-relevance.md:71-73`), and it left the embedding model and its
  dimension unchosen (`0025-search-relevance.md:35-38`). So there is no model or dimension to
  reuse. When 0025's vectors do exist, they cover symptom and cause but not the fix
  (`0025-search-relevance.md:30-31`). The fix is where risky commands and injected text live.
- **Reject and retire reasons are free text.** A reject needs a reason, with no category
  (`domain/reviews.py:35-39`; the form is one text box, `web/templates/queue.html:32`). Retire
  reasons are scrubbed free text too (`service/lifecycle.py:38-60`). The latest reason is shown
  to the proposer on `/mine` (`store/contributors.py:15-16`, decision 0035). So no stored
  field says *why* an entry was bad.

## 1. What it flags

Three targets:
- **Unsafe**: risky instructions or text aimed at the reading agent.
- **Wrong**: the fix fails when used.
- **Noise**: low value, never useful.

Duplicates stay with 0027's detector (`domain/duplicates.py:11-14`), which already flags them
in the queue. They are not a fourth target.

**One binary model per target, sharing one feature pipeline and one registry.** A single
multi-label model is not recommended, for four reasons:
- The targets' labels arrive at different times. An unsafe label comes at review, within days
  (the queue alarm is at 3 days, `.github/workflows/backup.yml:212`). A wrong label needs
  outcomes over up to 90 days (`domain/trust.py:100`).
- Each target needs its own threshold, calibration and kill switch.
- Each target reaches enough labels at a different time, and can ship then.
- With a few hundred labels, a shared multi-label model gains nothing over separate ones.

## 2. Labels

Where labels can come from today, as the schema stores them:

| Source | Where it is | Target | Use |
|---|---|---|---|
| Human reject | `reviews` with `decision = 'reject'`, `source = 'human'` (`store/migrations/0001_initial_schema.sql:86-95`), excluding merges | unsafe, wrong or noise, but which one is unknown (free text) | positive, once a category exists |
| Rejected with risk findings, or purged | already counted as "harmful" for trust (`store/trust.py:17-20`) | unsafe | positive |
| Retirement | `retire` review (`store/migrations/0011_entry_lifecycle.sql:3`); `fixed_in` (`0011_entry_lifecycle.sql:9-11`) | wrong or noise | positive only when there is no `fixed_in` and no `superseded_by`. A retirement with `fixed_in` was right until a release fixed the bug, so it is a negative for "wrong". |
| Risk-scan findings | `entries.risk_report` (`store/migrations/0013_entry_safety.sql:4`), 8 kinds (`domain/risks.py:3-12`) | unsafe | **a feature, not a label.** A model trained to reproduce the scanner adds nothing. Its value is catching what the scanner misses, and only human labels show that. |
| Outcome failures | `counted_outcomes` (`store/migrations/0010_corrections.sql:30-32`), weighed as the reopen rule weighs them: used, a known result, hook-observed, not the author's (`store/trust.py:96-112`) | wrong | positive when an entry meets the reopen rule (3 or more failures from 2 or more reporters, more failures than successes, 0026 amendment). Negative when it has 3 or more such outcomes and mostly successes. Otherwise unlabeled, never negative. |
| Never used | `_APPROVED_USE` `never_used` and `unretrieved` (`store/insights.py:260-281`) | noise | weak positive, and only after its window (60 days) |
| Duplicate merges | a human review with reason `duplicate of <id>` plus `superseded_by` (`service/duplicates.py:61-68`; the dashboard detects merges this way, `store/insights.py:220-224`) | none | **left out.** A merged entry's content was fine. 0027 owns duplicates. |
| Human approval with good outcomes | `human_reviewed`, `confirmed_at` (`store/migrations/0014_review_at_scale.sql:16-18`) | all | negative |

**How many exist now (local only).** Agents may not read the live database, so these counts
come from this machine's local Postgres (`make db`). The script is read-only and refuses any
host but localhost.
- `commons`, the local store: 7 entries (3 approved, 3 pending, 1 rejected; 3 are seeds) and
  1 human reject. It is migrated only to 0006 of 21, so it has no `risk_report`, retirements,
  merges or `counted_outcomes` at all.
- `commons_demo` (fake activity from `tools/demo_data.py`): 14 entries and 1 human reject, also
  at 0006.
- `commons_eval`: 0 entries.
- A fresh database migrated to all 21 migrations (`scripts/test-db.sh`) runs every label query
  below and returns 0, which confirms the queries match the current schema.

The live store's numbers are unknown to agents. The owner can run this read-only query to see
them:

```sql
SELECT
  (SELECT count(*) FROM reviews WHERE decision = 'reject' AND source = 'human'
     AND reason NOT LIKE 'duplicate of %') AS human_rejects,
  (SELECT count(*) FROM entries WHERE status = 'rejected' AND human_reviewed
     AND jsonb_array_length(risk_report->'findings') > 0 AND superseded_by IS NULL)
     + (SELECT count(*) FROM purges WHERE restored_at IS NULL) AS harmful,
  (SELECT count(*) FROM reviews WHERE decision = 'retire'
     AND reason NOT LIKE 'duplicate of %') AS retirements,
  (SELECT count(*) FROM entries WHERE jsonb_array_length(risk_report->'findings') > 0)
     AS with_findings,
  (SELECT count(DISTINCT r.entry_id) FROM counted_outcomes o
     JOIN counted_retrievals r ON r.id = o.retrieval_id
     WHERE o.used IN ('yes', 'partial')
       AND o.outcome IN ('tests_failed', 'error_persisted', 'user_rejected'))
     AS entries_with_a_failure,
  (SELECT count(*) FROM reviews WHERE reason LIKE 'duplicate of %') AS merges;
```

**Is it enough? No.** Other records point the same way. When 0028 shipped, no reporter had
the 5 approved entries TL1 needs (`docs/decisions/0028-review-bottleneck.md:54-55`). When 0025
was built, the real `searches` table held only deploy checks
(`0025-search-relevance.md:57-58`). And the hold-out decides the threshold, so it needs
positives:
- With 10 positives, a measured recall of 0.8 has a 95% Wilson interval of 0.49 to 0.94.
- With 30 positives, the interval is 0.63 to 0.90.

**Cold start: rules first, then active labeling, then a model.**
1. **Reason categories now.** Reject and retire forms gain a required category: `unsafe`,
   `wrong`, `noise`, `duplicate` or `other`. It is stored in its own column, never in
   `reason`, so `/mine` and agents see nothing new. Every review from then on is a clean
   label, and the owner categorises the existing free-text reasons once.
2. **A rules score now**, from signals that already exist: risk findings, failure evidence,
   never-used, duplicate candidates, and the proposer's prior harmful rejections. It is a
   dashboard column and sort order with the rule that fired as its "top feature". This is the
   baseline the model must beat.
3. **Active labeling.** The queue page can sort by the rules score, or later by the model's
   uncertainty, so a review spent on an unclear entry teaches the model the most. Approved
   entries the score ranks high appear in an "admin check" list. A decision there is a label.
4. **A model per target**, only once that target clears a gate: at least 30 positives in the
   training window and at least 15 in the time-split hold-out. Below that gate the target
   stays on rules.

## 3. Features

Every feature is computed from the entry **as it was proposed**: revision 1 in
`entry_revisions` (the insert trigger records it, `store/migrations/0011_entry_lifecycle.sql:39-50`).
Edits made after review can carry the reviewer's fix and would leak the label.

- **Text vectors.** Commons has no embeddings, so v1 uses TF-IDF on symptom, cause, fix and
  repro: word 1-2-grams plus character 3-5-grams, since the text is code-heavy. These are
  built from the training snapshot, so no model is downloaded and no text leaves the machine.
  When 0025 step 3 stores embeddings, they are added as a second feature block and compared on
  the same hold-out. They cover symptom and cause only, so the fix-text features stay. 0025
  computes embeddings on approve so that pending text is not sent to a third party
  (`0025-search-relevance.md:37-38`). Any embedding of *pending* entries for this classifier
  must use a local model.
- **Risk findings.** A count per kind (`domain/risks.py:3-12`), from rescanning revision 1
  with the current scanner (`privacy/risks.py:109`). The model never reads the stored report,
  which a rescan can change after the fact.
- **Length and shape.** Characters per field, share of the fix inside code fences, URL count,
  share of non-ASCII characters, and the redaction report's counts.
- **Version scope.** Whether `environment` names libraries and pins their versions, plus the
  language and the number of domains (`store/migrations/0001_initial_schema.sql:53,57`).
- **Duplicate candidates at proposal.** The top score and the count
  (`store/migrations/0012_duplicates.sql:5-6`). Propose writes these, so they are known at
  proposal time.
- **Reporter history at proposal time.** Earlier entries, approvals, human rejects, harmful
  rejections, and the success rate of their earlier entries, all from rows created before this
  entry. The current `reporters.reliability` and `trust_level` are never used: they fold in
  outcomes that came later, possibly this entry's own. The pseudonym is never a feature.

**Leakage rules.**
- Use no column a review, outcome or merge writes: `status`, `human_reviewed`, `provisional`,
  `sampled`, `confirmed_at`, `aliases`, `superseded_by`, `fixed_in`, ranking snapshots,
  retrievals, outcomes.
- Split by time, never at random. Train on entries proposed before T, using only labels
  observed by T. Hold out the entries proposed after T, with labels observed by T plus the
  target's label window.
- For "wrong", an entry is in the hold-out only once its window is over (90 days, or 3 or
  more counted outcomes).

**Models compared** on that hold-out:
- the rules score above;
- k-nearest-neighbours, with cosine similarity on the same vectors, scoring each entry by the
  share of its 10 nearest labeled entries that are positive;
- L2-regularised logistic regression, with class weights, then Platt recalibration on a
  later slice of the training data.

Logistic regression ships for a target only if it beats both on precision at the chosen
recall, with confidence intervals reported next to the numbers. Otherwise the better of rules
and k-NN ships. Logistic regression's advantage is that its top features are its coefficients
times the inputs, which explains each flag directly.

## 4. Where it acts

Admin and reviewer only. Each target is:
- a column on the queue page and the Entries page, with the score, the model version and the
  top 3 features;
- a queue sort order "most likely problem first". The default stays oldest first
  (`store/reviews.py:27`), and the new order joins the Entries page orders
  (`store/insights.py:94-98`).

Scores live in their own table, keyed by entry, target and model version. It is
append-only, so online metrics can use the score that was showing when the admin decided.

What it never does:
- It never changes `status`, never reopens (`store/trust.py:70-87`) and never writes a
  review, so `/mine` (`store/contributors.py:15-16`) shows nothing new.
- It never touches search's condition (`store/search.py:27-33`), provenance
  (`domain/search.py:106-128`), ranking, trust levels, or any MCP reply or server text.
- It never writes into `risk_report`. A human reject of an entry with findings counts as a
  harmful rejection and demotes the proposer (`store/trust.py:17-20`,
  `store/reviews.py:76-90`). A flag stored there would make the model's guess demote people.

Nothing agents see changes. That keeps these decisions intact, checked against each record:
- **0026, entry lifecycle.**
  - Retire, restore and supersede stay human actions with a reason
    (`docs/decisions/0026-entry-lifecycle.md:30-34`).
  - The only automatic removal from search is the reopen rule. It deliberately counts only
    hook-observed outcomes from 2 or more reporters, so that one source can't pull an entry
    (`0026-entry-lifecycle.md:62-67`). A model score is a weaker source than that, and it
    removes nothing.
- **0028, review at scale.**
  - What goes live is still decided by trust level, scanner findings and likely duplicates
    (`docs/decisions/0028-review-bottleneck.md:21-29,52-53`). A flag doesn't block
    provisional approval.
  - The 1-in-4 and 1-in-10 sampling stays random (`0028-review-bottleneck.md:25,27`).
  - Queue health still counts `IN_QUEUE` (`store/reviews.py:9-13`).
  - If flags should later steer sampling or block provisional approval, that changes 0028 and
    needs its own record.
- **0029, entry safety.**
  - Findings still show in the queue, block provisional approval and reach provenance
    (`docs/decisions/0029-entry-safety.md:32-37`). The scanner and its report are not changed.
  - The agent-side rules stay "the last line of defence" (`0029-entry-safety.md:50-51`). A
    flag is one more signal for the human, not a label agents read.

## 5. "Doing well"

**Offline**, per target, on the time-split hold-out:
- precision and recall at the chosen threshold, with Wilson intervals;
- precision at a fixed review budget (the top 10 of the queue);
- calibration: Brier score and a reliability curve with 5 bins (more bins than that is noise
  at this size).

The owner picks the threshold from a precision-recall curve. Suggested aims: recall first for
unsafe, where a false flag costs one look, and precision first for noise.

**Online**, monthly, over entries a human decided in the period, using the score shown at
decision time:
- **Lift**: the share of flagged entries a human rejects or retires (without `fixed_in`),
  against the same share for unflagged entries. Below 2× means the flag isn't earning its
  column.
- **Miss share**: the share of rejected or retired entries that weren't flagged when they
  were decided.

Flags shown to the admin change the admin's decisions, which inflates lift. So each model
first runs in **shadow mode** for 4 weeks: it scores and stores, and nothing is displayed.
After that, about 10% of entries, chosen by a hash of the entry id, never show a flag, and
online lift is measured on them.

## 6. MLOps and observability

**Registry**, a Postgres table that the nightly backup covers. Each row holds:
- target, version and status (`candidate`, `shadow`, `active` or `off`);
- the artifact, as bytes plus its SHA-256 (logistic regression and its TF-IDF vocabulary come
  to a few MB);
- the training snapshot's hash: SHA-256 over the sorted (entry id, revision, label, label
  observed at) rows;
- the training cutoff T;
- the feature configuration's hash, which covers the scanner's kinds and the embedding model
  id and dimension once there is one;
- library versions, offline metrics, the threshold, and who activated it.

Activating a model is an admin action with an event line, following `admin_action`
(decision 0037).

**Drift**, computed by each scoring run against the training distribution:
- *Score drift*: the population stability index (PSI) of this week's scores.
- *Feature drift*: PSI per cheap feature, and the share of n-grams that are out of
  vocabulary.
- *Embedding or scanner version change*: a model whose feature hash doesn't match the current
  configuration refuses to score, and the dashboard says "model stale, retrain". A score built
  on different vectors would be wrong, not just worse.

**Label delay.** Each label has an "observed at" time. Training uses only labels observed by
T. A "wrong" negative needs a closed window, and an entry with an open window is unlabeled,
not negative.

**Retrain cadence.** Monthly, and only when the target has at least 25% more labels than the
active model saw. A configuration change forces a retrain. A retrained model replaces the
active one only if it does at least as well on the newest hold-out (champion and challenger),
and it starts in shadow mode.

**Kill switch.** `COMMONS_FLAGS=off`, or a target's registry status set to `off`, stops scoring
and hides the column. By construction nothing else reads the scores, so turning it off changes
nothing else.

**Where these show.**
- **OTel metrics** through the meter API the call log already uses (`mcp/call_log.py:31`):
  - `commons.flags.scored` (counter, by target and model version);
  - `commons.flags.score` (histogram);
  - `commons.flags.score_psi` and `commons.flags.feature_psi` (gauges);
  - `commons.flags.online_lift` and `commons.flags.miss_share` (gauges, monthly).

  They go to the LGTM stack (decision 0037), with a Flags panel generated by
  `tools/grafana_dashboards.py`. The privacy rule of 0024 and 0037 holds: ids, counts, scores
  and versions only, and top features never reach telemetry, because they are words from entry
  text. Two gaps, each for its build ticket:
  - The CLI never calls `telemetry.configure()`; only the server, MCP and dashboard entry
    points do (`web/app.py:622`, `mcp/server.py:358`, `hosted/app.py:70`).
  - Production has no OTLP destination yet (`docs/decisions/0037-lgtm-observability.md:47-50`).
- **An admin dashboard panel** on the queue page, read from Postgres: the active model per
  target, its offline numbers, last month's lift and miss share, drift, the label count
  against the gate, and the kill switch state. Until production has an OTLP destination, this
  panel is the production view.

## 7. Costs and risks

- **Where it runs: a nightly server-side batch.**
  - It is a separate `commons admin` command in the nightly workflow, next to `commons retain`
    (`.github/workflows/backup.yml:108`).
  - It is not inside `commons rank`: SessionStart reruns `rank` on any machine when ranking is
    stale (`service/hooks.py:58`), and the model doesn't belong on agents' machines.
  - It is not run at proposal time: that would put scikit-learn in the Cloud Run image, add
    latency to `propose`, and make a score part of the propose path.
  - The cost of nightly batch: a new pending entry waits up to a day for its score. The
    dashboard shows "not scored yet".
- **Dependencies.** scikit-learn, with numpy and scipy, in an optional dependency group that
  only the batch job installs. The server image and `server/pyproject.toml`'s runtime
  dependencies are unchanged.
- **Privacy.** Training and scoring are local to the job, with no third-party API, so pending
  text stays in the database, as 0025 requires. Entry text is already redacted on write. The
  artifact's vocabulary holds words from pending and rejected entries, so it is stored with the
  same access as that text, never in the repo or in telemetry. Reporter features are counts,
  never identity.
- **Bias.** Reporter-history features score newcomers higher. That is acceptable for a
  review-order hint, since TL0 entries all wait for a human anyway. It would not be acceptable
  as a gate, which is one more reason this stays a hint.
- **Build versus buy.**
  - Generic moderation APIs are trained on toxicity, not on wrong fixes or `curl | sh`, and
    they send pending text to a third party.
  - An LLM judge, such as a Claude call per pending entry, is the strongest "buy" for unsafe
    and wrong. It needs no labels, but it costs per entry and sends pending text out, so it
    needs its own privacy decision. Its verdicts would also need the same online metrics.
  - Open prompt-injection classifiers could be a local feature for unsafe, but they cost
    hundreds of MB.
  - Managed MLOps platforms such as MLflow or W&B are more than one table needs at this scale.
- **Effort.** Reason categories and a rules score: small, one ticket. Registry, batch job,
  dashboard columns and panel: about three tickets. Each model: a ticket once its gate is met.

## Options

- **A. Do nothing.** The existing rules stay: scanner, duplicates, reopen, failure-rate,
  stale and never-used lists. At today's volume a human reads every queued entry anyway, so a
  model has nothing to reorder. Cost: none. Risk: reject reasons stay uncategorised, so the
  labels a future model needs are never collected.
- **B. Rules and labels now, a model per target when its gate is met** (sections 2 to 6).
  Reason categories and a rules score ship first. The registry, shadow mode and metrics ship
  with the first model.
- **C. Train logistic regression now** on whatever labels exist. With the local counts above,
  and the live count likely in single digits per target, the hold-out can't distinguish a
  good model from a lucky one.
- **D. An LLM judge per pending entry** instead of a trained model. It works from day one, but
  it costs per call, sends pending text to a third party, and needs a privacy decision.

## Decision (proposed)

Option B. Start with the two pieces that need no model: required reject and retire reason
categories (stored apart from the reason text), and a rules-based "likely problem" score as a
queue and Entries column and sort order. Train a model per target only once it has 30
training positives and 15 hold-out positives. Compare logistic regression on TF-IDF and cheap
features against k-NN and the rules score, run it 4 weeks in shadow, then display it with a
10% hold-back. It stays admin-only, never hides or demotes anything, and changes nothing
agents see, which keeps 0026, 0028 and 0029 as they are. Embeddings join as a feature block
when 0025 step 3 stores them, from a local model for pending text.

Consequences:
- **Easy:** labels start accumulating at once, and each target ships when its own data
  supports it. The model can be switched off without side effects.
- **Hard:** the first model is months away at current volume. The online numbers need shadow
  mode and a hold-back to mean anything.
- **Revisit when:** a target reaches its gate; 0025 step 3 lands; the owner sets a production
  OTLP destination; or flags are proposed as an input to sampling or provisional approval,
  which would amend 0028.
