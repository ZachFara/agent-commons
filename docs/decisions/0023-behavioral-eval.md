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

Amendment (2026-10-25, after the baseline): the owner pointed out that the scenario author and
the eval model share training data, so an invented bug that feels surprising to the author is
usually a pattern the eval model has seen many times, and the low attempt counts bear that out.
Hold-out scenarios will therefore be built from **real breaking changes released after the
models' training cutoff**, found on the web and reproduced with the real version pinned, so the
fix cannot be recited by either side. Development scenarios for shaping conditions:
`docs-retry-flag` and `version-silent-rename`; everything else is held out. Experiments run in
this order, each a separate named condition at the baseline's run count: (a) worked examples in
tool argument descriptions, (b) a Stop-hook propose nudge, (c) search-trigger wording.
Hold-out set (2026-10-25, `tools/eval/holdout.py`): four scenarios on real breaking changes
released after the models' cutoff, each confirmed by executing the real package or interpreter:
SQLModel 0.0.45 (naive datetimes rejected; the `default_factory=datetime.now` makes the obvious
fix insufficient), whenever 0.11.0 (`ignore_dst` removed from `add()`), Python 3.14 sqlite3
(sequence parameters with named placeholders now raise), Python 3.14 itertools (copy/pickle of
iterators removed). A pytest 9.1 generator-argvalues scenario was dropped: the check showed it
only warns in a clean project (my earlier reproduction had inherited this repo's
`filterwarnings = error`), so the breaking behavior lands in pytest 10. Pyproject scenarios pin
the interpreter with `.python-version`, since `uv run` otherwise picks the newest.

Verification (one session each, Sonnet): every bug fixed in 1-3 attempts; no session searched or
proposed. Unlike the invented scenarios, the surprises reached the agent: it read whenever's
deprecation warning and picked the new flag, and wrote "Python 3.14 removed copy and pickle
support for itertools iterators", then "I didn't call the commons tools, since the fix was
routine." The agent recognizes version-specific breaking changes and classifies them as routine.
The sqlite3 change was fixed as a plain caller bug without noticing the version dependence. The
first experiment therefore targets the "routine" judgement: name the signal rather than ask the
agent to judge difficulty.

Hard set (2026-10-25, `tools/eval/holdout.py` `HARD`): the first hold-outs were solved in 1-3
attempts because each error message named its own fix. The owner asked for scenarios that are
non-trivial on sight. New rule: **the obvious fix must fail**, enforced by giving every scenario
one test the first fix passes and one it does not. Four scenarios, each on a real post-cutoff
change confirmed by running it, with the misleading signal noted: pandas 3.0 Copy-on-Write
(filtered-subset assignment silently leaves the parent unchanged; `.copy()` does nothing),
SQLAlchemy 2.1 unconditional autoflush (a Core lookup now sees pending rows, so a
get-or-create skips a row the app's test expects), SQLModel 0.0.45 reading legacy naive rows as
UTC-aware (`TypeError: can't compare offset-naive and offset-aware` raised in app code, far
from the database), Python 3.14 deferred annotations (a TYPE_CHECKING-only import passes
construction and fails only in `get_type_hints`; the runtime import is circular for one model).
PyTorch and PySpark were considered and set aside: no silent change could be pinned to a
version runnable on this machine, and a 127 MB wheel or a JVM per session is a poor fit for
20-session batches.

Verification (one session each, Sonnet): all four fixed, three in **one attempt and four
turns**. The agent diagnosed each correctly on first read ("The cause was SQLAlchemy's
autoflush", fixed with `session.no_autoflush`). It then declined to share and said why: "I
didn't search or report to Agent Commons. It was a one-attempt fix." Two conclusions. Attempt
count cannot be the difficulty signal: these are hard for a human and easy for Sonnet. And the
propose rule's own wording ("took more than one attempt") is the bottleneck, applied literally
by the agent to exclude exactly the cases the commons exists to collect. The signal the agent
states in every transcript, and the one the next conditions key on, is whether the cause was
a library or version behavior rather than the app's own logic. The owner's confidence-based
rule is kept as a wording variant but is unlikely to fire here: the agent was confident and
right.

Structural screen (2026-10-25, 20 sessions per condition on the hard set, results and intervals
in `docs/eval/README.md`): `start` raised search from 4/20 to 20/20; nothing raised propose
above 1/20. Every `start` session searched, got nothing back, fixed the bug, and treated the
empty result as the end of its obligations. The funnel therefore has two separate gates, and a
condition that opens one does nothing for the other, so the next round combines surfaces on top
of `start` instead of choosing one. The `nudge` condition is void: all 210 eval sessions piped
their test output through `tail`, the hooks read tail's exit code, and no failure was ever
observed (fixed in PR #52, a production bug in hook-observed outcomes as well). The ledger keeps
those 20 lines marked `invalid`; `agent_eval.py trials` does not count them.

Round two (2026-10-25, combinations on top of `start`, 20 sessions each on the hard set):
`start-rule` proposed in 7/20 and `start-all` in 9/20, every proposal graded good; `start-invite`
1/20. The rule's wording, not the moment it is shown, was the second gate. Two limits remain:
agents don't propose a version change they can't recognize as one (SQLAlchemy 2.1, 0/15), and
14 of 30 propose calls were rejected on argument shape, mostly an unversioned `tool` that the
worked example never shows. Next: false proposals on the controls under `start-all`, then the
wording search within it, including the owner's confidence-based rule and an example that shows
`tool` with a version.

Wording search (2026-10-25). The owner rejected `start` for adoption: a forced search runs on
every session, typos included (15/20 controls), and nobody would install that. Search and
propose must come from the text alone, propose weighted highest, with every surface the agent
reads varied (framing, search and propose rules, tool and argument descriptions, empty-search
note, a session-start line that never asks for a call, tool names) and each exact text repeated
rather than sampled once. Balanced random configs (204 sessions) pointed at the owner's
hindsight rule for propose and a pinned-version rule for search; repeating four configs (160)
and confirming on 17 scenarios the search never saw (153) gave config B
(`w-a0b2c2d1e0f1g1h0n0`: pinned-version search rule, hindsight propose rule, worked example
with a versioned `tool`, an invitation on an empty search, and a "connected" session-start
line): good proposals 19/21 on discoveries and 9/12 on real post-cutoff changes against 3/21
and 0/12 for today's text, search 17/21 on discoveries and 0/12 on controls, no false proposal
on a genuine control, no planted instruction followed. Two procedural findings: a control
whose documented fix cannot pass is not a control (`control-documented-gotcha`, superseded by
`control-documented-option`), and an exemption clause in a rule ("skip bugs plainly in this
code") becomes the default reading. B is proposed for adoption as the baseline text.

Cross-model adoption (2026-10-25). Config B failed the bar on Opus 5.5 (2/12 control proposals
on documented behavior; duplicates in 10/12 known-entry sessions because Opus rarely searches),
so the owner asked for text that works for every model. Rounds three and four ran every config
on Sonnet and Opus together, ranked by the worse model, and added options for each failure; 26
missed sessions were resumed and asked why, and published guidance was surveyed (no study tests
voluntary search and write-back for a shared store; memory systems leave the decision to the
harness). The adopted text, `w-a0b4c5d1e0f1g1h0n0s1`, passed on both models on 13 scenarios no
round was tuned on: good proposals 25/27 vs 18/27 (Opus) and 18/27 vs 2/27 (Sonnet), searches on
26/27 and 21/27 discoveries and 0/18 controls, no control proposal, every bug fixed. Finding for
future work: Claude Code defers MCP tools behind tool search by default, so before an agent
loads our tools it sees only their names and the server instructions; the next candidate change
is marking `search` and `propose` with `_meta["anthropic/alwaysLoad"]`, measured like any other.
