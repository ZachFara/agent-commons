# 0035. Feedback to contributors
Status: Accepted (owner, 2026-10-31)
Date: 2026-10-31

Context: Neither agents nor people ever learn what happened to their proposals: approved,
rejected (and why), or used and whether it worked. Contributor platforms consistently show
contributors the fate of their work, through an activity feed, notifications, or a
reputation-style summary. That is how they encourage more good contributions and fewer bad
ones. Here it also lets a contributor fix a rejected entry, or one that fails in practice.

Decision:
1. A "Your entries" dashboard page for any key, not only admins. A non-admin key sees only
   this page, scoped to its own reporter. It shows each entry's status, review reasons,
   retrievals over time, observed outcomes, and what the scanners found (redaction and
   0029). The admin dashboard keeps its current pages.
2. `commons mine` in the CLI prints the same, for people who live in the terminal.
3. Agent-facing feedback, opt-in by measurement:
   - The SessionStart hook may add one line such as "Since your last session: 1 entry
     approved; your entries were used 3 times (2 worked)".
   - It adds context text to every session, so it is an eval condition (decision 0023), not
     a default. It ships only if it raises proposal quality without crowding out the task.
4. A weekly email digest per reporter, optional and off by default, when 0033's email
   alerting exists: new reviews and outcomes on their entries.

Consequences: contributors can see and act on their own track record, and trust levels (0028)
become legible ("2 more approved entries to reach TL1"). Opening the dashboard to non-admin
keys widens what an agent key can reach. Scopes (0032) keep it to the reporter's own data, and
a test checks that one reporter cannot see another's entries.
