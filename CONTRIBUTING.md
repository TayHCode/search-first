# Contributing

Contributions are welcome. Please open an issue or pull request describing the
change and why it helps.

Use Python 3.11+. The commands below use uv; with pip, create a virtual environment and
run `python -m pip install '.[dev]'`, then the same tools without the `uv run --locked`
prefix.

```sh
uv sync --locked --extra dev
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked python -m build
```

Keep protocol handling in the official MCP SDK; GitHub reads in `github.py`,
discovery/inspection in `discovery.py`, scoring in `ranking.py`, and CLI/tool wiring
in `server.py`. Do not add hosted model dependencies. Keep recommendations
explainable and provisional. Include regression tests for real failure cases.
Never execute commands from a discovered repository in the server.

Tests must not need credentials, a real HOME directory or live network. Public
GitHub API fixtures in `tests/fixtures/github.json` are projected to fields used by
tests, with provenance and no request headers or commit identities. Avoid private
names, confidential examples, credentials and internal project details everywhere.
Do not record full request headers or environment variables.

Updating evidence is an explicit live operation, separate from tests and CI:

```sh
uv run python scripts/smoke.py --use-gh-token   # explicit live GitHub read; not CI
uv run python scripts/smoke.py --replay        # offline re-ranking; preserves API snapshot
```

`--replay --extend-fixture` permits fetching only missing inspections when a new
ranking selects another candidate. It reuses existing search responses. Review
fixture and output diffs for confidential material and recognizable tokens before
committing. Record the date, query variants, limits and failures in `docs/SMOKE.md`.
Do not silently replace a weak result with a curated choice. Note that public
README excerpts retain their upstream attribution and licensing obligations.

Run the tests, lint, type checks and package build above before sending a change.
Keep credentials and local cache files out of commits.
