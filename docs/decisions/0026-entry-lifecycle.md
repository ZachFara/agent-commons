# 0026. Entry lifecycle: revisions, supersession, retirement
Status: Accepted (owner, 2026-10-31)
Date: 2026-10-31

Context: An entry can only be proposed, approved or rejected. Nobody can fix a typo, narrow a
version range, point an entry at a better one, or retire it once the library fixes the bug.
`retired` exists in the status enum, but nothing sets it. Phase 3 plans staleness flags
(DESIGN §7) with nothing to act on them. Knowledge bases conventionally keep an append-only
revision history with the current version on top. They link replaced articles to their
successor rather than deleting them, and they retire with a reason. Ratings and usage are
credited to the revision that was actually shown.

Decision:
1. Revisions:
   - A new `entry_revisions` table holds every version of symptom, environment, cause, fix,
     repro and domains, with editor, source (human or agent), reason and redaction report.
   - `entries` keeps the current text plus `revision` (an integer).
   - Every edit goes through the same redaction as `propose` and the same scanning (0029).
   - `retrievals` records the revision shown, so outcomes credit the text the agent actually
     saw. Ranking (decision 0017) keeps scoring the entry as a whole for now.
2. Who edits:
   - Admins edit directly from the entry page. A human edit is recorded like a human review.
   - Agents propose an edit with `propose(..., revises=<entry_id>)`. It becomes a pending
     revision in the review queue, shown as a diff. This adds an argument to `propose`, which
     changes eval-measured text, so it ships only after an eval run (decision 0023).
3. Supersession:
   - A `superseded_by` column links an entry to its replacement.
   - Search never returns a superseded entry. Its retrievals and outcomes stay for the dataset.
   - Merging duplicates (0027) uses the same link.
4. Retirement:
   - Admins retire with a reason and, where it applies, `fixed_in` (library and version).
   - A retired entry leaves search, except that with 0025 version awareness, a caller below
     `fixed_in` can still be shown it, marked "fixed in X".
   - Retirement is reversible. It is a status change recorded in `reviews`, never a delete.
5. Staleness:
   - When the Phase 3 decay and absorption flags exist, they feed a dashboard list:
     "candidates to retire or revise".
   - Until then, the same list uses simple rules: no successful use in 90 days while still
     retrieved, or a falling success rate.

Consequences: the store can stay correct over time without losing history, and the dataset
gains a clean record of when knowledge went stale, which DESIGN §7 wants. It adds a table, two
columns and a dashboard editor. Revisions complicate the export (DESIGN §8), which should ship
the revision chain.

Implementation notes (T48, 2026-10-31):
- Built: revisions (the first comes from a trigger on insert; existing entries were backfilled
  as revision 1), admin edits on the entry page through `propose`'s validation and redaction,
  `superseded_by`, retirement with `fixed_in`, restore, and the stale-candidates list on the
  Entries page.
- Not built: agents' `propose(..., revises=...)`. It changes eval-measured tool text, so it
  waits for an eval run the owner approves (decision 0023).
- Retire and restore are new `review_decision` values recorded as human reviews; superseding
  is a retirement with `superseded_by` set. The review tools still accept only approve/reject.
- Risk scanning of edits arrives with 0029 (T50), on the same path as proposals.
