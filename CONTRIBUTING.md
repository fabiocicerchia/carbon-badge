# Contributing

Thanks for taking the time to contribute to carbon-badge!

## Getting started

You need Python 3.10+ and `make`.

1. Fork and clone the repo.
2. Install dev deps and git hooks: `make setup && make dev`.
3. Create a branch: `git checkout -b feat/short-description`.

```sh
make dev     # editable install with dev dependencies (pytest, ruff, build)
make lint    # ruff check .
make test    # pytest
```

## Making changes

- Keep changes focused; one logical change per PR, keeping the existing style.
- Add or update tests.
- Update `docs/` and `examples/` when behavior changes.
- Ensure CI (`ci`, `code-quality`, `security`) passes.

Don't edit `CHANGELOG.md` by hand — it's generated from commit messages by
release-please (see [Releases](#releases)).

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`,
`fix:`, `docs:`, `chore:`, etc. This keeps history readable and drives the
version bump: `fix:` → patch, `feat:` → minor, `feat!:` or a
`BREAKING CHANGE:` footer → major.

## Releases

Releases are automated by [release-please](.github/workflows/release.yml);
you don't tag or edit the changelog manually.

1. Merge `feat:`/`fix:` PRs into `main` as normal — **no tag is created**.
2. release-please keeps an open **release PR** ("chore: release X.Y.Z"),
   recalculating the next version (in `pyproject.toml`) and `CHANGELOG.md` on
   every merge.
3. When you're ready to ship, **merge the release PR** — that (and only that)
   creates the `vX.Y.Z` tag and GitHub Release, and the workflow attaches the
   sdist + wheel (and publishes to PyPI if `PUBLISH_TO_PYPI` is set).

## Pull requests

Fill out the PR template, link related issues, and request review. Be kind.

## License

By contributing you agree that your contributions are licensed under the
Apache License 2.0 (see `LICENSE`).
