# 0024. Log every search, and every tool call
Status: Accepted (owner, 2026-10-31)
Date: 2026-10-31

Context: `retrievals` only records searches that returned an entry, so a search that found
nothing left no trace, and nobody could see what agents look for that the store lacks. Cloud
Run's request log shows every MCP call as the same `POST /mcp`, without the tool. The owner
asked for both to be logged, with redaction, and for privacy modes to govern the search log.

Decision:
- A `searches` table gets one row per `search` call, misses included: task, reporter, model,
  privacy mode, redacted query, language filter, limit, hit count. `commons search` (the CLI
  preview) and invalid searches log nothing. `retrievals` is unchanged.
- What a row keeps follows the existing privacy modes (owner chose these over a new mode):
  - `entry` (default) and `local`: the fields above. `local` only reaches a store on the
    reporter's own machine (decision 0014).
  - `full_trace`: also the redacted environment and the returned entries (id, rank, score).
  - The mode is the task's if hooks recorded the task, else the reporter's default, so a later
    change to the default never widens what an earlier task shared. A check constraint keeps
    `environment` and `results` empty outside full_trace.
- MCP middleware writes one JSON line per tool call: tool, reporter id, task id, model (agent
  fields capped at 80 characters), ok or error code, result count, milliseconds, with a
  `severity` Cloud Logging reads. Hosted entry points send it to stdout; over stdio nothing is
  written, since stdout is the MCP channel. Lines never hold queries or entry text: server logs
  sit outside the privacy modes and keep Cloud Logging's default 30 days.
- The dashboard gets a Searches page: queries that found nothing in the last 30 days, grouped,
  then recent searches.

Consequences: the store's gaps are visible and countable, which DESIGN §7's absorption
detection (query volume per entry) also needs. Searches add one row each; at current volume
that is negligible, but there is no retention policy yet for `searches` or the call log.
