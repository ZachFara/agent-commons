.PHONY: setup db check fix

setup:  ## Install dependencies and git hooks
	uv sync
	uv run pre-commit install --hook-type pre-commit --hook-type commit-msg

db:  ## Start local Postgres (needed by the tests)
	cd db && docker compose up -d --wait

check:  ## The one check command (same as pre-commit and CI)
	uv run python tools/check.py

fix:  ## Auto-format and apply safe lint fixes, then check
	uv run python tools/check.py --fix
