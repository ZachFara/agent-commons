# Decision records

One short file per decision: `YYYY-MM-DD-<ticket>-<kebab-title>.md` (for example
`2026-11-07-MIT-580-agent-orchestration.md`), never a sequence number. Records up to 0038 keep
their `NNNN-` names. Never edit an accepted record to change its decision; write a new one that
supersedes it and mark the old one `Superseded by <file name>`.

Template:

```
# Title
Status: Proposed | Accepted | Superseded by <file name>
Date: YYYY-MM-DD

Context: why a decision is needed (2-4 sentences).
Decision: what we chose.
Consequences: what this makes easy, what it makes hard, what we'd revisit.
```
