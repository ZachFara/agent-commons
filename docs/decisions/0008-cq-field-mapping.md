# 0008. Field mapping to cq knowledge units
Status: Proposed (needs owner approval before T06)
Date: 2026-10-24

Context: DESIGN.md §2 says an entry is a superset of a cq knowledge unit and we must not invent
names for fields cq already has. Checked against `cq-schema` 0.3.1
(`knowledge_unit.json`, `review.json`, `propose.json`).

cq KnowledgeUnit fields: `id` (`ku_` + 32 hex), `version`, `domains` (1-16 strings),
`insight {summary ≤500 "what happened", detail ≤8000 "why it matters", action ≤2000 "what to
do"}`, `context {languages[], frameworks[], pattern}`, `evidence {confidence, confirmations,
first_observed, last_confirmed}`, `tier` (local|private|public), `created_by`, `superseded_by`,
`extensions` (`namespace:key`), `flags[] {reason stale|incorrect|duplicate, timestamp, detail,
duplicate_of}`. Review: status pending|approved|rejected, `reviewed_by`, `reviewed_at`.

Mapping:

| DESIGN.md | cq | Proposal |
|---|---|---|
| `tags` | `domains` | **Rename to `domains`**, require at least one. Same concept. |
| `proposed_by` | `created_by` | **Rename to `created_by`**. Same concept. |
| reviews `reviewer_id` (0005) | `reviewed_by` | **Rename to `reviewed_by`**; `created_at` exports as `reviewed_at`. |
| `symptom`, `cause`, `fix` | `insight.summary`, `.detail`, `.action` | **Owner choice**, see below. |
| `environment` | `context` | Keep. Ours is richer (pinned versions, OS, tool); export derives `languages`, `frameworks`. |
| `id` (UUID) | `id` | Keep. Export as `ku_` + `uuid.hex`, which matches the pattern exactly. |
| `status` | review status | Keep; same labels plus our `retired`. |
| outcomes `evidence` | KU `evidence` | Different meaning, different table. Export fills cq `evidence` from our ranking (`confidence` = score, `confirmations` = successful used outcomes, `last_confirmed`); never from outcome evidence. |
| `privacy_mode`, reporter `privacy_default` | `tier` | Different concepts. Hosted entries export as `private`; `local` as `local`. |
| — | `superseded_by`, `flags` | Not in DESIGN v0.1. Relevant to retirement and decay (Phase 3); add then, with these names. |
| `repro`, `environment`, scores | — | Export under `extensions` as `commons:repro`, etc. |

The symptom/cause/fix choice:
- (a) Keep DESIGN names; adapter maps symptom→summary, cause→detail, fix→action. Clearer to
  agents filling them in (a symptom is raw error text; cq's detail is "why it matters", not
  "root cause"). Arguably breaks the "no new names" rule.
- (b) Use cq names in storage and tools. Literal compliance; loses the debugging-specific
  meaning in the tool signature, which drives what agents write.

Recommendation: (a). The meanings differ enough that these are our fields, not renamed cq ones;
cq's limits (summary ≤500) are applied on export by truncation, recorded in the export report.

Consequences: DESIGN.md §3-§4 change `tags`→`domains`, `proposed_by`→`created_by`; the
Phase 3 adapter becomes a near-mechanical projection.
