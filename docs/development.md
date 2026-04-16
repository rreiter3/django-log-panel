# Development workflow

Use this workflow when working on a local checkout of `django-log-panel`.

## Setup

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/) and create a development environment:

```bash
uv venv --python=3.13
uv sync --group dev
```

## Tests

Run the test suite with:

```bash
uv run pytest
```

## Linting and typing

```bash
uv run ruff check
uv run ruff format
uv run ty check
```

## Project notes

- `pytest` uses `tests.settings` from `pyproject.toml`.
- Coverage is configured in `pyproject.toml` with `fail_under = 95`.
- Screenshots used in the docs live under `docs/images/`.

## See also

- [Backend setup](backends.md)
- [Configuration reference](configuration.md)
- [Advanced topics](advanced.md)
