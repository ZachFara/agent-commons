# 0030. Data correction: audited corrections and exclusions from the dashboard
Status: Accepted (owner, 2026-10-31)
Date: 2026-10-31

Context: On 2026-10-31 two wrong `used` values in production needed hand-written SQL to fix.
Two deploy-check searches stay on the Searches page as noise. The project's value is its
outcome data (DESIGN §1), so mistakes must be correctable. Corrections must also stay visible:
silent edits would undermine the dataset. Standard practice for research data is to keep raw
observations unchanged and to record corrections and exclusions as separate, attributed,
reversible records that every reader applies.

Decision:
1. A `corrections` table records every correction: who, when, target table and row, field, old
   value, new value, reason. Corrections write the new value into the row, so every reader
   stays simple, and the old value lives in `corrections`.
2. An `excluded` flag, with reason and who, on `retrievals`, `outcomes` and `searches`.
   Excluded rows are skipped by ranking, reliability, `status`, the dashboard charts and the
   export, and shown greyed out where they are listed.
3. Dashboard actions, admin only (reviewers too, once 0028 exists), each asking for a reason:
   - On an outcome: set `used` or `outcome`.
   - On a retrieval, search or whole task: exclude, or mark as a test. Marking a task as a
     test excludes all its rows.
   - Undo any correction.
   - A "Corrections" page lists them all.
   The same actions are in the CLI as `commons admin correct` for scripting.
4. After any correction that touches outcomes, the affected entries' scores are recomputed at
   once, without waiting for the nightly `commons rank`.
5. Test traffic up front: `search` and `propose` accept no flag for this. Instead a key can be
   marked `test` (0032), and everything it writes is excluded automatically. Verification
   checks like 2026-10-31's would use a test key.

Consequences: no more hand-written production SQL, and the dataset keeps an audit trail. The
2026-10-31 manual fixes are recorded in PROGRESS.md and would be backfilled into
`corrections`. Every reader must honour `excluded`. A shared SQL view per table keeps that in
one place, with a test that each insight query uses the view.
