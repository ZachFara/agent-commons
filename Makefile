.PHONY: setup db check fix infra-check search-eval eval-confirm eval-effects eval-reliability

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

search-eval:  ## Search precision@5 and recall@5 on the judged set (decision 0025). Needs `make db`.
	uv run python tools/search_eval.py

# Behavioral eval (decision 0023, docs/eval/README.md). Runs real Claude Code sessions on the
# signed-in subscription: ~156 sessions, about an hour. Needs `make db`. Rerun with RESUME=1
# after a usage limit stops it.
EVAL_CONDITION ?= confirm-$(shell date +%Y%m%d)
EVAL_MODELS ?= --model sonnet --model claude-opus-5-5
EVAL_CONFIRMATION = real-py314-itertools-copy real-py314-sqlite-params real-sqlmodel-utc \
  real-whenever-ignore-dst docs-inclusive-range docs-timezone-default multi-rounding-key \
  multi-shared-registry version-default-flip control-typo control-arithmetic \
  control-documented-flag known-default-flip

eval-confirm:  ## Shipped text vs the original on both models, scenarios no round was tuned on
	uv run python tools/agent_eval.py search --condition $(EVAL_CONDITION) --runs 3 \
	  --concurrency 4 $(EVAL_MODELS) --config original --config shipped \
	  $(foreach s,$(EVAL_CONFIRMATION),--scenario $(s)) $(if $(RESUME),--resume)

# Decision 0034: the multi-turn scenarios plus controls, for the shipped text, the wider
# propose trigger, and the per-turn nudge (a store setting). 2 models x 3 conditions x 4
# scenarios x RUNS sessions; ask the owner before running (it uses the subscription).
EVAL_0034_RUNS ?= 3
EVAL_0034 = --scenario multi-turn-rename-then-default --scenario multi-turn-retry-then-timezone \
  --scenario control-typo --scenario control-documented-flag --runs $(EVAL_0034_RUNS)
eval-reliability:  ## Decision 0034's conditions on both models (asks nothing; costs usage)
	for model in sonnet claude-opus-5-5; do \
	  uv run python tools/agent_eval.py run $(EVAL_0034) --model $$model --condition r-shipped-$$model && \
	  uv run python tools/agent_eval.py run $(EVAL_0034) --model $$model --variant previous --condition r-previous-$$model && \
	  COMMONS_TURN_NUDGE=on uv run python tools/agent_eval.py run $(EVAL_0034) --model $$model --condition r-nudge-$$model; \
	done

eval-effects:  ## Per-model results of an eval condition (EVAL_CONDITION=...)
	uv run python tools/agent_eval.py effects --condition $(EVAL_CONDITION)
