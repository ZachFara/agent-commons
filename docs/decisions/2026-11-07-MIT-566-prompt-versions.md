# Searches and proposals record the prompt version the server served
Status: Proposed
Date: 2026-11-07

Context: What an agent does depends on the text it reads: the server instructions, tool
names, descriptions and argument descriptions, and the notes in a search result. That text
changes with `COMMONS_INSTRUCTIONS_VARIANT`, with a new shipped wording, and with the opt-in
extras (the record prompt, the turn nudge, session feedback), but no row says which text an
agent saw, so a change in search or propose rates can't be tied to the text that caused it.
`agent_text.fingerprint` names a variant for the eval ledger, but it hashes the `Variant`
fields, not what FastMCP serves, and the ledger depends on its values staying as they are.

Decision:
1. A `prompt_versions` table: `hash` (SHA-256 as 64 lowercase hex characters, unique, shown
   as the first 12), `label` (up to 120 characters) and `surfaces` (jsonb, {surface: text}).
   `searches`, `retrievals` and `entries` get a nullable, indexed `prompt_version_id` foreign
   key. Migration 0017 also recreates `counted_retrievals` and `counted_searches`, whose
   `SELECT *` was expanded in 0010, so they show the new column (and `retrievals.revision`).
2. `build_server` builds the version once, from the same `Tool` and `Prompt` objects it gives
   FastMCP, so the hash can't drift from what is served. The surfaces are the instructions;
   per tool `tool/<name>/description`, `tool/<name>/input_schema` (`json.dumps` with sorted
   keys) and `tool/<name>/_meta`; `prompt/<name>/description` for registered prompts;
   `note/untrusted` and `note/empty`; and, only when switched on, `record_prompt`,
   `turn_nudge` (the template, before the task id is filled in) and `session_feedback` (the
   marker `on`; the line itself is formatted with counts). Templates, never formatted text,
   so ids and counts never make a new version. The hash is over canonical JSON (sorted keys,
   no spaces), so the order of surfaces never matters and every character does.
3. The label is the `COMMONS_INSTRUCTIONS_VARIANT` name, with the wording name after a colon
   for the aliases `baseline` and `previous` (whose text moves when a new wording ships), and
   `+turn-nudge` and `+record` when those are on. Two names for one text (`original` and
   `start`, which differ only in the SessionStart line the server never sends) share one row,
   and the first label to serve a hash keeps it.
4. `search` and `propose` take a keyword `prompt`. Inside their transaction the store runs
   `INSERT ... ON CONFLICT (hash) DO NOTHING` and then selects the id; `DO UPDATE` would lock
   the one shared row on every search. Imports and seeds pass no version, so their rows, like
   rows from before 0017, stay NULL. Tool responses are unchanged.
5. `agent_text.fingerprint` is left as it is.

Consequences: Rates can be grouped by the exact text served, and `surfaces` shows what
changed between two versions. A FastMCP or pydantic upgrade that changes the schema JSON
(sorted keys absorb a change of key order, not a new, renamed or reordered value) creates a new
version with no wording change; compare `surfaces` before reading anything into it. A hosted
redeploy doesn't refresh the text in a running session: hosted MCP is stateless HTTP, and
Claude Code reads the instructions only at initialize, so rows from a session that began
before a deploy point at the new version although the agent may still be reading the old
instructions. The turn nudge and feedback switches are read when the server is built; over
stdio the hook processes read their own environment, which can differ. We'd revisit recording
the version per session at initialize if those cases turn out to matter.
