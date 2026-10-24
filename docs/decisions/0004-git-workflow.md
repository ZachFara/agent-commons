# 0004. Git workflow
Status: Accepted
Date: 2026-10-24

Context: Multiple agent sessions, one human approver.

Decision: Nothing is committed to `main` directly except the initial bootstrap commit. One task
from PROGRESS.md per branch, named `feat/`, `fix/`, `chore/`, or `docs/` plus a short slug.
Conventional commit messages. Every branch merges by PR after CI passes; squash-merge so `main`
reads as one commit per task. PR descriptions state what changed, why, and how it was tested.

Consequences: requires a GitHub remote with branch protection on `main` (required `check`
status). Until it exists, branches are pushed and merged only after the owner sets it up.
