.PHONY: setup db check fix infra-check

setup:  ## Install dependencies and git hooks
	uv sync
	uv run pre-commit install --hook-type pre-commit --hook-type commit-msg

db:  ## Start local Postgres (needed by the tests)
	cd db && docker compose up -d --wait

check:  ## The one check command (same as pre-commit and CI)
	uv run python tools/check.py

fix:  ## Auto-format and apply safe lint fixes, then check
	uv run python tools/check.py --fix

infra-check:  ## Validate infra/ with the official Terraform image (no local install needed)
	docker run --rm -v "$(PWD)/infra:/infra" -w /infra -e TF_DATA_DIR=/tmp/tf --entrypoint sh \
	  hashicorp/terraform:1.9 -c "terraform fmt -check -recursive && terraform init -backend=false -input=false >/dev/null && terraform validate"
