# Contributing

Thanks for improving Vibe Content Agent.

## Local setup

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
PYTHONPATH=src uvicorn vibe_agent.api:app --reload --port 8088
```

## Checks

Run tests before opening a pull request:

```bash
PYTHONPATH=src pytest
```

## Pull requests

- Keep changes focused.
- Do not commit `.env`, SQLite databases, media generated for private posts, backups, logs, or API tokens.
- Add or update tests for behavior changes.
- Update docs when changing setup, publishing, providers, or admin workflows.

## Areas that need help

- safer source ingestion and deduplication;
- model comparison and rewrite evaluation;
- publishing integrations;
- public blog/RSS improvements;
- self-hosted deployment docs;
- security review of admin and token handling.
