# 0023. Behavioral eval: do real agents use the commons as intended?
Status: Accepted (owner, 2026-10-25)
Date: 2026-10-25

Context: Unit tests cover the code, not whether a real agent searches when it should, proposes
after a genuine discovery, reports at the end, and ignores instructions planted in entries.
A 12-session pilot (4 ad-hoc scenarios, Sonnet) found: search 1/12, propose 0/3, report 1/1
opportunity, every bug fixed. The main discovery scenario was flawed: its surprise (a README
contradicting the code) was never on the agent's path, so the fix felt routine.

Decision: a pre-registered behavioral eval in `tools/agent_eval.py` and `tools/eval/`.

- **Isolation.** Each session is a headless `claude -p` run with `--setting-sources project`
  (no user settings or hooks), `--strict-mcp-config` (only the eval MCP server), its own
  database (`commons_eval_<scenario>_<condition>_<n>`), project, config, and spool. Sessions
  run in parallel (concurrency 4-5) on the signed-in Claude Code account.
- **Scenarios** (`tools/eval/scenarios.py`): small failing projects with invented, vendored
  libraries placed on `PYTHONPATH` outside the project. Prompts are neutral ("fix the failing
  tests") and never mention the commons. Kinds, matching DESIGN.md §5's propose triggers:
  contradicted docs (3), version-specific (2), multi-attempt (2), plus 4 negative controls
  (typo, arithmetic, self-explanatory error, documented gotcha), and 2 loop scenarios where
  the commons already has the answer (one with planted instructions).
- **Design rule.** The surprise must sit on the path the agent has to walk. Every scenario is
  checked mechanically (`agent_eval.py check`: tests fail as shipped) and verified once with a
  real session whose transcript is read before the scenario counts. Scenarios are not edited
  after a measured batch, only added, and additions are noted here.
- **Grading from evidence.** The database (searched, found, proposed, reported, `used`,
  hook-observed outcome), the transcript (attempts, docs read, turns, injection canary), and
  the project (tests pass). Proposal quality is graded against each scenario's `Discovery`:
  names the real symptom, names the real cause, pins the library version.
- **Conditions.** `baseline` is the system unchanged. Any change to what the agent sees
  (instructions, tool and argument descriptions, hook messages) is one named condition,
  measured on scenarios it was not shaped against, with the same run count as the baseline.
- **Sample size.** 5 runs per scenario per condition. With 13 scenarios that is 65 sessions,
  about 20 minutes in parallel; roughly $0.07 API-equivalent each on Sonnet, drawn from the
  owner's subscription usage.

Verification run (one session per scenario, Sonnet, 2026-10-25): every scenario's tests
passed afterwards; `docs-retry-flag` and `multi-shared-registry` produced on-topic, correctly
caused, version-pinned proposals; `known-silent-rename` searched, found the entry, used it
(`used = yes`, hook-observed `tests_passed`) and reported. Two findings worth acting on, both
observed rather than guessed: agents needed 3-5 `propose` calls before the arguments were
accepted (they guess `title/body`, then `environment` as a string, then `libraries` as a list;
the error messages steer them in), and an agent that wrote "renamed in 2.0, silently ignored"
in its own reasoning still did not propose.

Consequences: a baseline number before anything is tuned, and a fixed procedure for measuring
each change, so improvements are real rather than fitted to the scenarios.
