.PHONY: help setup install dev lint test build run format analyze

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

setup: ## Install the pre-commit hook
	pre-commit install

install: ## Install the package
	pip install .

dev: ## Editable install with dev dependencies
	pip install -e ".[dev]"

lint: ## Run the whole gate — every hook, every file
	pre-commit run --all-files

test: ## Run tests
	pytest -q

build: ## Build sdist and wheel
	python -m build

run: ## Run carbon-badge
	carbon-badge --help

format: ## Rewrite the sources to canonical form
	ruff format .

analyze: ## Type-check the package
	basedpyright
