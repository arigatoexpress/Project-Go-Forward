# Dev Setup

Use Python 3.11 for this repo. The Docker image and GitHub Actions deploy workflow both target Python 3.11, so local development should match that runtime.

All commands below run from the repo root unless noted otherwise.

## Python

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

If you use `pyenv`, `asdf`, or `mise`, the repo-level [`.python-version`](../.python-version) pins the interpreter to `3.11`.

## Frontend

```bash
cd frontend
npm ci
npm run build
```

## Pre-commit

```bash
pip install pre-commit
pre-commit install
pre-commit run --files <changed-files>
```

Use `pre-commit run --all-files` only when you intentionally want a repo-wide cleanup sweep.

## Test Commands

CI-critical checks:

```bash
python -m pytest tests/test_admin_auth.py tests/test_smoke.py -v --tb=short
```

Full suite:

```bash
python -m pytest tests/ -q
```
