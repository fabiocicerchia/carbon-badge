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
make help    # Show this help
make setup   # Install the pre-commit hook
make install # Install the package
make dev     # Editable install with dev dependencies
make lint    # Run ruff
make test    # Run tests
make build   # Build sdist and wheel
```

## Tooling

Shared config — the GitHub workflows, `.pre-commit-config.yaml`,
`.editorconfig`, `.hadolint.yaml`, `SECURITY.md` — comes from
[repo-skeleton](https://github.com/fabiocicerchia/repo-skeleton). Edit it
there, not here; a local edit is drift and the next sync overwrites it.
`check-drift.sh` in that repo reports what has diverged.

- `make setup` installs the pre-commit hook, and that is the whole of it.
  Don't add a `.githooks/` directory: `core.hooksPath` replaces `.git/hooks/`
  wholesale, so setting it silently stops every pre-commit hook from running.
- Hooks are pinned by commit SHA with the tag in a trailing comment. A tag can
  be moved, a SHA cannot.
- CI runs this same `.pre-commit-config.yaml` through `pre-commit/action`, so
  what passes locally is what gates the pull request.

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
