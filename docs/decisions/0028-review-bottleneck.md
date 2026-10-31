# 0028. Review at scale: reviewer role, trust levels, sampled review
Status: Accepted (owner, 2026-10-31)
Date: 2026-10-31

Context: Every entry waits for one human, the owner (decision 0005). With friends proposing,
the queue becomes the bottleneck and good entries sit invisible. Community knowledge bases solve
this with three standard tools:
- Roles: more people allowed to review.
- Earned trust: privileges that grow with a contributor's track record, as with Stack Overflow
  reputation and Discourse trust levels.
- Post-moderation: trusted contributors publish immediately, a sample is reviewed afterwards,
  and anything can be rolled back.

Decision:
1. Roles:
   - `reporters.role` becomes `contributor | reviewer | admin`, replacing `is_admin`, which is
     migrated to `admin`.
   - Reviewers can approve and reject from the dashboard. Admins can also edit (0026), manage
     keys (0032) and correct data (0030).
   - Review rows keep the reviewer, so per-reviewer quality is measurable.
2. Trust levels, computed nightly with `commons rank` from the reporter's own history:
   - TL0, new: everything goes to the queue.
   - TL1: at least 5 approved entries, none rejected as harmful (0029), and a duplicate rate
     under 20%. New entries are approved provisionally with source `auto`, shown with
     "unreviewed" provenance, and 1 in 4 is sampled into the queue for a human check.
   - TL2: at least 20 approved, and reliability (decision 0017) at least 0.8. Same as TL1, but
     1 in 10 is sampled.
   - Any rejection for risky content (0029) drops the reporter to TL0 and sends their recent
     provisional entries back to the queue.
   - The thresholds are settings. These defaults are a starting point, to be revisited after a
     month of data.
3. Outcome confirmation: a provisional entry with at least 3 hook-observed successes from at
   least 2 other reporters is marked confirmed, and its provenance drops "unreviewed". A
   provisional entry whose observed outcomes are mostly failures goes back to the queue.
4. Queue health: the dashboard shows queue length and the age of the oldest item, and the
   owner gets an email digest when the oldest item passes 3 days (uses 0033's alerting).

Consequences: entries from proven contributors become useful immediately, and human attention
goes where risk is highest. The danger is a trusted reporter publishing something harmful
before the sample catches it. Mitigated by 0029's scanning, which runs before auto-approval and
forces the queue on any finding, and by instant rollback. Needs a `review_source` value `auto`
(migration) and DESIGN §3 and §6 updates.
