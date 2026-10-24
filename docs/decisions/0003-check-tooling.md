# 0003. The one check command
Status: Accepted
Date: 2026-10-24

Context: Several agent sessions will work on the repo. Quality has to be enforced by tooling
that runs identically everywhere, not by memory or convention.

Decision: `make check` (= `uv run python tools/check.py`) runs, in order and always all of them:

| Step | Tool | Settings |
|---|---|---|
| format | ruff format | line length 100 |
| lint + complexity | ruff check | `select = ["ALL"]` minus formatter conflicts; mccabe max 8; max 6 args, 10 branches, 4 returns, 40 statements, 3 nested blocks |
| types | mypy | `strict = true`, plus warn_unreachable and extra error codes |
| dead code | vulture | min confidence 60 |
| module boundaries | import-linter | contracts in `pyproject.toml`, from decision 0002 |
| file layout | tools/check_structure.py | rejects files outside allowed directories and stray scratch files |
| tests | pytest | strict markers and config |

The pre-commit hook and CI run the same command. A commit-msg hook enforces conventional
commits, and `no-commit-to-branch` blocks commits to main.

Chose ruff over flake8/black/isort (one fast tool), mypy over pyright (no Node dependency in a
Python repo), vulture for dead code, import-linter because it expresses layers directly.

Consequences: the bar is high from the first line, which is cheaper than tightening later.
Rule ignores are listed with reasons in `pyproject.toml`. Changing any threshold or adding an
ignore is a loosening and needs a PROGRESS.md entry and the owner's approval first.

Amendment (2026-10-25, owner-approved): vulture ignores methods decorated with `@override`.
Frameworks call these through the base class (e.g. FastMCP's `TokenVerifier.verify_token`), so
vulture can't see a caller; mypy's `explicit-override` check already guarantees each one really
overrides a base method, so nothing genuinely dead can hide behind it.
