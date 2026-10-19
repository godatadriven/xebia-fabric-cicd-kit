# The fabkit version for CI and local runs. The pre-commit rev in .pre-commit-config.yaml is bumped with it.
FABKIT_VERSION := 0.9.0
FABKIT := uvx --from xebia-fabkit==$(FABKIT_VERSION) fabkit

.PHONY: help setup validate deploy

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup: ## Install pre-commit and its git hooks
	uv tool install pre-commit
	"$$(uv tool dir --bin)/pre-commit" install

validate: ## Check the repository files, as the pull request check does
	$(FABKIT) validate

deploy: ## Deploy one environment: make deploy ENVIRONMENT=DEV (az login first)
	@test -n "$(ENVIRONMENT)" || { echo "Set ENVIRONMENT, e.g. make deploy ENVIRONMENT=DEV" >&2; exit 1; }
	$(FABKIT) deploy --environment "$(ENVIRONMENT)"
