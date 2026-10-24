# 0006. Hook logic lives in the CLI layer
Status: Accepted (owner, 2026-10-24)
Date: 2026-10-24

Context: DESIGN.md puts hook definitions in `client/hooks.json`. Hook logic (test capture, file
hashing, `used` computation) is real code that needs types and tests.

Decision: `client/hooks.json` only invokes `uv run --directory <path>/server commons hook
<event>`. The logic lives in `commons.cli` (parsing the hook payload) and `commons.service`
(what to record). `client/` stays config and prose.

Consequences: all hook code goes through the full check. Hooks pay uv start-up time on each
call; if that's too slow, revisit with a long-running local process.
