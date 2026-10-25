# Behavioral eval results

Raw per-session results from `tools/agent_eval.py` (decision 0023). One JSON file per session:
`<condition>-<scenario>-<run>.json`. Transcripts stay out of the repo (temp paths, size).

| Condition | Date | Model | Sessions | What it measures |
|---|---|---|---|---|
| `baseline` | 2026-10-25 | sonnet | 65 (13 scenarios × 5) | The system as shipped, before any change to what the agent sees |

Summarize with `uv run python tools/agent_eval.py report --condition <name>` after copying a
condition's JSON files into `.scratch/eval/<name>/`.

## Baseline (2026-10-25)

Discovery scenarios (45 sessions): fixed the bug 45/45, searched 4/45, proposed 2/45 (both
on-topic, correctly caused, version-pinned). Controls (20): searched 1/20, proposed 0/20.
Known-entry scenario: 1/5 searched, found, used (`yes`, hook-observed `tests_passed`), and
reported. Injection scenario: no session searched, so the planted instructions were never seen.

By trigger: contradicted docs 2/20 searched, 2/20 proposed; version-specific 2/15, 0/15;
multi-attempt 0/10, 0/10. Mean test attempts 1.9-2.6, which says the scenarios are easy enough
to feel routine: searching never happened in a one-attempt session and rose only slightly with
attempts (3/27 at two attempts, 1/6 at three).

Reading: agents are strong at the task and at restraint, and weak at consulting the commons at
all. Search is the bottleneck; propose can't happen without it. The scenario bugs are familiar
patterns (mutable defaults, silent kwargs, half-open ranges), which the eval model has seen
many times even with invented library names; the hold-out set should use real post-cutoff
breaking changes instead (see decision 0023, amendment).
