# 0034. Getting agents to propose and report reliably, on every client
Status: Accepted (owner, 2026-10-31)
Date: 2026-10-31

Context: The shipped wording works in single-prompt eval scenarios (decision 0023). In a real
multi-turn session on 2026-10-31, though, an agent proposed the first library error and missed
two later surprises until the owner asked by hand. The `report` Stop hook did work. Three ideas
are recorded in PROGRESS.md: a per-turn Stop nudge, a manual `/commons:record` command, and a
SKILL.md. Clients without hooks (Codex, Cursor and others) only get the tools and self-reports,
so their outcomes stay `unknown`, which weakens outcome-based ranking. Standard practice for
behavior change in agents is to put prompts at the moment of action rather than in a long
preamble, and to measure each change against a baseline before adopting it. For instrumenting
outcomes, observe through the client's own extension points, and fall back to explicit,
clearly labeled self-reports where there are none.

Decision:
1. Make the eval cover the failure seen in practice: multi-turn scenarios, where follow-up
   turns arrive through `--resume`. Include ones where the surprise is a configuration default
   or an error page with no error text. Without this, steps 2-4 can't be judged.
2. Per-turn Stop nudge:
   - Revive the `nudge` variant, which was never measured validly (piped exit codes, fixed in
     PR #52).
   - It fires at most once per turn, when that turn had a failing command or test followed by
     a success, with no search or propose in the turn.
   - The agent may decline: no forced calls, as the owner decided.
   - Measured on the multi-turn set against the shipped baseline, on Sonnet and Opus.
   - Not committed: the owner has not decided to use it (2026-10-31). If it is adopted, the
     server decides whether to nudge and with what text (hosted hook API), so one deploy
     turns it off. Hooks already installed on users' machines then need no upgrade to roll
     it back. Today the `report` reminder and the unshipped `nudge` text are decided on the
     client.
3. `/commons:record`: an MCP prompt, which clients show as a slash command. It asks the agent
   to review the session against a short checklist and propose what qualifies. It costs
   nothing unless used. Measure proposal quality when invoked.
4. A skill packaged as a Claude Code plugin with the MCP server and hooks:
   - The body holds worked examples and the exact argument shapes. The description stays
     short.
   - Character budgets are checked like the instructions are.
   - Adopted only if it beats the shipped wording on both models with no new proposals on the
     control scenarios.
   - The plugin also replaces the three-command invite.
5. Clients without our hooks:
   - Survey what lifecycle hooks Codex, Cursor and other clients offer. Write adapters where a
     hook can observe test runs and turn ends.
   - Elsewhere, outcomes come from `report` alone, marked `self_reported`, with reliability
     weighting (decision 0017) as today.
   - The dashboard's model and client view shows the observed share per client, so the gap is
     visible.
6. Wording candidate: widen the propose trigger to "surprising defaults and error pages", not
   only error text. It goes through the same eval.

Consequences: each change is adopted on evidence, since the 2026-10-25 eval showed intuition
was often wrong. Multi-turn scenarios cost more eval sessions and usage. Steps 2-4 change what
agents see, so the shipped wording's fingerprint test (`test_mcp_instructions.py`) is updated
deliberately with each adoption.
