# Agent orchestration: Linear tickets, feature branches, guard hooks
Status: Proposed (setup PR for MIT-580; accepted when Zach merges it)
Date: 2026-11-07

Context: Work moves from "one session takes the next PROGRESS.md task" to an orchestrator that
coordinates planner, researcher, coder and reviewer agents. Zach delegates a ticket in the
Linear project "Commons" (read only for agents) by moving it to Delegated; agents track their
own work in "Commons Agent Project". Agents push as Zach, so GitHub can't tell them apart from
him, and this machine holds credentials that can change the live Commons database
(`~/.config/agent-commons/`, gcloud as project owner).

Decision:
1. Linear is the task list. PROGRESS.md becomes a short summary (last run, in flight, next,
   open questions). New decision records are named `YYYY-MM-DD-<ticket>-<slug>.md`; the
   existing `NNNN-*` records keep their names and numbers.
2. Each delegated Commons ticket gets `feature/MIT-<n>-<slug>` from main. Coders open PRs into
   it from `feat/|fix/|chore/|docs/<ticket>` branches; only the orchestrator merges them
   (squash, after CI and a reviewer PASS). Zach gets one PR from the feature branch into main
   and is the only one who merges into main.
3. Role definitions live in `.claude/agents/`; the orchestrator runs as the main session with
   `claude --agent commons-orchestrator`.
4. Hooks in `.claude/settings.json` enforce the hard limits; their logic and tests are in
   `tools/agent_hooks/` (the existing `tools/` lint policy covers subprocess calls, so no new
   rule ignores), with thin entry scripts in `.claude/hooks/` that exit 2 on any failure so a
   broken guard fails closed:
   - `linear_guard`: reads allowed; issue creation only in the agent project; updates and
     comments only on issues in `.agents/owned-issues.txt` (seeded with MIT-580); links only
     to owned issues; coders get no Linear access; every other write denied.
   - `record_issue`: adds each created issue to the owned list.
   - `bash_guard`: merges only into `feature/` bases; no push to main, force push, hook
     bypass, settings-changing `gh api`/`gh repo`/`gh secret` calls, or reads of live
     credentials. Best effort against mistakes; GitHub rulesets are the backstop.
5. GitHub ruleset on `main` and `feature/**`, no bypass actors: PR required (0 approvals:
   agents push as Zach and an author can't approve their own PR), required `check`, branches
   up to date, no force pushes.
6. Data isolation: agents load `.env.agent` (local throwaway Postgres; copied into worktrees by
   `.worktreeinclude`), `scripts/test-db.sh` gives each worktree a fresh migrated database,
   and `scripts/snapshot.sh` tags main as `stable/<UTC time>` and dumps production through the
   read-only `commons_backup` role (decision 0033) before every run; `scripts/restore.sh`
   goes back to one.
7. New allowed top-level paths: `.claude/`, `scripts/`, `.worktreeinclude`,
   `.env.agent.example` (amends decision 0002's list).

Consequences: Zach reviews one PR per delegated ticket instead of one per task. The rulesets
bind Zach too: an urgent fix to main also needs a PR with a passing `check`. The guards are
string-level checks of Bash commands; a determined process could get around them, which is
why the GitHub rules don't rely on them. Snapshots hold plaintext production data on this
machine (owner-only directory outside `~/Ark`).
