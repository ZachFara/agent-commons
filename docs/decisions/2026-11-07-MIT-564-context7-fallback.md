# Context7 lookups and the fallback question
Status: Proposed
Date: 2026-11-07

Context: An empty Commons gives agents nothing. A new store has no entries, so every search is
a miss. Agents already consult Context7 for
library docs. Zach's ticket suggested five things: Context7 as the fallback on a miss,
randomized routing to avoid selection bias, ratings keyed to (library, version, topic),
pointers instead of copies, and correction entries ("docs for X say this, version Y needs
that"). PROGRESS.md (at `fe1a155`, "Under consideration: integrate an external data source")
left four questions open: ingest or point, which source first, how to credit a fix that came
from both, and which phase.

Terms used below:
- A **lookup** is one Context7 `query-docs` call.
- A **miss** is a Commons `search` that returned no entry; a **hit** returned at least one.
- A **pointer** is a lookup's library id and version, with no doc text.
- A **correction entry** is a Commons entry that says where a library's docs are wrong for a
  version.

Decision:

Built now. Nothing an agent sees changes.
- The PostToolUse hook observes lookups and stores each one as a pointer in a new
  `doc_lookups` table (MIT-622, PR #110, migration `0020_doc_lookups.sql`). The library id
  and version come from the tool input. The tool's response is never read. Counts read the
  `counted_doc_lookups` view, so excluded rows drop out (0030-data-correction.md).
- The dashboard shows misses next to lookups (MIT-623). A miss followed by a lookup in the
  same task shows where Commons had nothing and the agent went to the docs.
- Storage is a new table. A new `task_events` kind would need the same migration anyway, for
  privacy mode, `excluded` and retention, and corrections can't write its jsonb payload. A
  generalized `retrievals` table has 23 readers to change, and `used` matching can't work on a
  lookup, because we hold no doc text to match.

Proposed. Each item has options and a recommendation.

1. **Query text in `entry` mode.**
   - (a) Library id and version only. The query is kept only in `full_trace`.
   - (b) The redacted query, as `searches` keeps it in every mode
     (0024-search-log-and-call-log.md).
   - Recommend (a). It stays inside 0016-hooks-and-observed-outcomes.md: only derived data
     leaves the machine, and a query can quote the user's code. MIT-622 already builds (a);
     a CHECK keeps `query` empty outside `full_trace`.
2. **Ingest or point.**
   - Ingest: copy doc text into Commons as entries. Entries then need provenance, licensing
     and a refresh story.
   - Point: keep pointers only.
   - Recommend point. No doc text, ever.
3. **Licensing check.**
   - Context7's sources have varied licenses.
   - Recommend a licensing check before anything beyond pointers is stored, and before
     `commons export` publishes per-library ratings. Pointers alone copy no text.
4. **Randomized routing.** Commons can't route, because agents call Context7 themselves. Zach's
   goal was to avoid selection bias: agents look up docs exactly when Commons misses.
   - (a) No randomization in production. Compare the two sources in the eval, with the kill
     criterion drafted for `MIT-567`.
   - (b) Withhold Commons results for 5% of hit searches.
   - (c) Add a docs note to the search result on a random share of searches.
   - Recommend (a). (b) breaks DESIGN §4's honest "nothing found": an agent would be told
     Commons has nothing when it has an answer. (c) changes what agents see, so it would need
     its own eval condition anyway.
5. **Correction-entry trigger.** How an agent learns to propose a correction entry.
   - (a) A worked "docs for X say this, version Y needs that" example in the `propose`
     argument description, as one eval condition.
   - (b) A Stop nudge that asks about a correction entry after a turn with a lookup.
   - (c) A Context7 id inside `environment` on `propose`, for example
     `"docs": "/org/project/v1.2.0"`. Not a new top-level field, so entries stay cq-compatible
     (0008-cq-field-mapping.md maps `environment` to cq's `context`).
   - Recommend (a) with (c). All three change what agents see, so all three are eval-gated
     (0023-behavioral-eval.md, 0034-reliable-propose-and-report.md). The 0034 nudge fired
     mostly on control sessions, which is why (b) is not recommended.
6. **Rating lookups.** Later, after about two weeks of lookup data and a researcher pass on a
   metric. The artifact matching in 0036-used-detection-v1.md doesn't carry over: it matches
   an entry's fix text against the session, and a pointer has no text. Ratings would be keyed
   to (library, version, topic), and topic needs the query, which `entry` mode doesn't keep.
7. **Eval feasibility.** The eval runs with `--strict-mcp-config` and invented libraries
   (0023-behavioral-eval.md). Context7 has no docs for an invented library. A fallback
   condition (item 8) therefore needs:
   - Context7 in the eval MCP config;
   - network access in eval sessions;
   - scoring only on the hold-out scenarios built on real post-cutoff changes, where Context7
     can have real docs.
8. **Fallback on a miss.** What an agent is told, or given, when `search` finds nothing. Today
   a miss returns an empty `data` with the shipped empty-result note (`INVITE_NOTE` in
   `agent_text.py`, wording surface `f1`): "No entries match", then an invitation to `propose`
   once solved. It says nothing about docs. Context7 is only observed.
   - (a) Nothing changes on a miss.
     - For agents: nothing. They read the propose invitation and go to the docs on their own,
       and the hook records the lookup.
     - Eval: none. This is the baseline the other options are measured against.
   - (b) The miss note also names Context7. One sentence follows the propose invitation, for
     example "If you have Context7, call `resolve-library-id` for whenever, then `query-docs`
     for version 0.11.0." The library and version come from the caller's
     `environment.libraries`. When the call passed none, the sentence names the tools without a
     library. `data` stays empty, so "nothing found" stays honest (DESIGN §4), and nothing is
     stored beyond what `searches` keeps today.
     - For agents: one more sentence on a miss, pointing at a lookup. The server can't tell
       whether the client has Context7, so the sentence is conditional. `query-docs` takes a
       Context7 id (`/org/project`), not a package name, so the note names
       `resolve-library-id` first. Only agents that pass `environment.libraries` get a named
       library, and the `search` argument description mentions only `language`. Asking for
       libraries there is a second surface.
     - Eval: one named condition, a new option of the empty-result surface, measured on
       scenarios it was not shaped against (0023-behavioral-eval.md). Adopt it only if it
       beats the shipped note on both models with no new proposals on the controls (0034), and
       good proposals don't drop, since the note shares space with the propose invitation. It
       is the miss-only, every-time form of item 4 (c), so it is not randomized in production
       (item 4 (a)). It needs item 7's setup: Context7 in the eval MCP config, network access,
       scoring on the hold-outs.
   - (c) The server calls Context7 itself on a miss and returns pointers. The response gains
     the Context7 library id and version for each library in `environment.libraries` that
     resolves. No doc text is returned or stored (items 2 and 3).
     - For agents: a pointer they can pass straight to `query-docs`, which saves the
       `resolve-library-id` call. They still need Context7 to read the docs, and the pointer is
       only as good as the server's resolve.
     - Costs: the store makes its first call to a third party while serving a request, with a
       Context7 key, rate limits, and latency and failures on every miss. The server reads
       Context7's responses, which the hook never does. Only library names may be sent: the query can quote the
       user's code, and Commons forwarding it to a third party goes past what
       0016-hooks-and-observed-outcomes.md lets leave the machine and what item 1 (a) keeps. A
       `local` store (0014) calls no third party today (telemetry export is opt-in and goes
       only to the configured endpoint), so it would be off there. Server-side resolves are
       not agent lookups and stay out of `doc_lookups`.
     - Eval: a named condition too, since the response changes what agents see. It needs item
       7's setup, plus network access for the eval's own server.
   - Recommend (a) now, and (b) as the first fallback condition once item 7's setup exists. (b)
     is one sentence on the text agents already read after a miss, needs no new code path, and
     can be measured. (c) only if the (b) eval shows agents skip the lookup because they can't
     resolve the library: it adds a third party, a key and outbound calls to save one tool call.

**Credit.** When a fix came from both a Commons entry and a lookup, both are recorded: the
entry's outcome in `outcomes`, the lookup in `doc_lookups`. Credit is not split. Entry scores
read only entry outcomes (0017-ranking-v0.md), so a lookup never moves an entry's score.

**Amends 0033-operations.md.** Its retention list covers `searches`, call logs and spools.
MIT-623 adds a retention rule for `doc_lookups`.

Consequences: Commons can count where it misses and where agents go instead, before anything
an agent sees changes. Recording pointers keeps the privacy modes as they are and copies no
licensed text. Harder: lookups can't be rated until there is a metric that works without doc
text, and `entry` mode loses the topic. Random routing is ruled out in production, so the
comparison between Commons and Context7 rests on the eval, which needs network access and
real libraries. The cold-start problem is measured here, not solved. Revisit when two weeks of
lookup data exist, when the eval has a fallback condition (item 8 (b)), or if Zach wants doc
text ingested after a licensing check.
