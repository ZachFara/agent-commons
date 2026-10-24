# 0005. Provisional agent reviews, human reviews on top
Status: Accepted (owner, 2026-10-24)
Date: 2026-10-24

Context: DESIGN.md v0.1 made approve/reject human-only behind a CLI prompt, but in local mode the
agent calls them over stdio MCP, where no human prompt exists.

Decision: Reviews are rows, not a status flip. A `reviews` table holds any number of reviews per
entry (reviewer, approve/reject, reason, source agent|human). An agent's approve/reject is
provisional: it sets the entry's status, but `human_reviewed` stays false and search provenance
says "unreviewed". Human reviews come from channels the agent can't use as itself: `commons
review` (local), the admin dashboard, admin API keys (hosted). Status follows the latest human
review if any, else the latest agent review. DESIGN.md §3-§4 updated to match.

Consequences: the loop works locally with no blocking prompt, and every entry carries its review
history. Quorum rules ("two human approvals") can be added later as a query over reviews without
a schema change. Open: whether agent-approved, unreviewed entries should be visible to other
reporters in hosted mode (PROGRESS.md).
