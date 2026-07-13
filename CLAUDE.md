# CLAUDE.md

Guidance for Claude Code (and other AI agents) working in this repo.

## Project

carbon-badge is a single-module Python CLI (`carbon_badge.py`) that estimates a
repo's CI carbon footprint from the GitHub Actions API and emits Shields.io
endpoint JSON (or an SVG). Entry point: `carbon-badge` → `carbon_badge:main`.
Python 3.10+, packaged with setuptools (`pyproject.toml`).

## Commands

```sh
# setup: make setup   (git hooks + pre-commit)
# dev:   make dev      (editable install with dev deps)
# build: make build    (python -m build)
# test:  make test     (pytest -q)
# lint:  make lint      (ruff check .)
```

## Conventions

- Match existing style; don't reformat unrelated code.
- Conventional Commits for messages (see CONTRIBUTING.md).
- Update docs/ and examples/ with behavior changes. Don't hand-edit
  CHANGELOG.md — release-please generates it from commit messages.
- Never commit secrets; CI runs gitleaks. Keep `.env` out of git.

## Guardrails

- Don't add dependencies without a clear reason; prefer stdlib. The only
  runtime dependency is `requests`.
- Keep the carbon estimate assumptions documented and overridable via flags.
- Don't touch generated files (build/, *.egg-info/) or reports/ by hand.
- Ask before large refactors or destructive operations.
