"""Tests for scripts/generate_api_reference.py and the committed API reference.

These tests guard two things:

1. The generator renders Markdown covering every path+method in app.openapi()
   (checked in-process against a rendered string, not the committed file).
2. The committed docs/API_REFERENCE.md exists, carries the generator marker,
   and still covers every current OpenAPI path. If routes are added or
   removed, regenerate with:

       python scripts/generate_api_reference.py
"""

from pathlib import Path

import pytest

from scripts import generate_api_reference as gen

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "API_REFERENCE.md"


@pytest.fixture(scope="module")
def app():
    gen._prepare_environment()
    return gen._load_app()


@pytest.fixture(scope="module")
def openapi_paths(app) -> dict:
    return app.openapi().get("paths", {})


def _operations(paths: dict) -> list[tuple[str, str]]:
    ops = []
    for path, item in paths.items():
        for method in gen._HTTP_METHODS:
            if method in item:
                ops.append((path, method.upper()))
    return ops


def test_render_covers_every_openapi_operation(app, openapi_paths):
    """Rendered Markdown mentions every path+method from app.openapi()."""
    markdown = gen.render_markdown(app)
    missing = [
        f"{method} {path}"
        for path, method in _operations(openapi_paths)
        if f"`{path}`" not in markdown
        or not any(line.startswith(f"| {method} | `{path}` |") for line in markdown.splitlines())
    ]
    assert not missing, "generator output is missing operations:\n" + "\n".join(missing)


def test_render_is_deterministic(app):
    assert gen.render_markdown(app) == gen.render_markdown(app)


def test_social_draft_compatibility_route_is_documented_as_response_only(app):
    markdown = gen.render_markdown(app)

    assert "| POST | `/api/marketing/schedule` | Prepare response-only social draft |" in markdown


def test_generator_never_embeds_secret_values(app):
    """Only env var NAMES may appear — never values from the environment."""
    import os

    markdown = gen.render_markdown(app)
    for name in ("ADMIN_PIN_HASH", "RESEND_API_KEY", "GITHUB_WEBHOOK_SECRET"):
        value = os.environ.get(name)
        if value:
            assert value not in markdown, f"value of {name} leaked into the API reference"


def test_committed_doc_exists_and_has_marker():
    assert DOC_PATH.exists(), (
        "docs/API_REFERENCE.md is missing — generate it with: "
        "python scripts/generate_api_reference.py"
    )
    text = DOC_PATH.read_text(encoding="utf-8")
    assert text.strip(), "docs/API_REFERENCE.md is empty"
    assert gen.GENERATED_MARKER in text, (
        "docs/API_REFERENCE.md lacks the generator marker; "
        "regenerate: python scripts/generate_api_reference.py"
    )


def test_committed_doc_covers_current_paths(openapi_paths):
    """Every current OpenAPI path must appear in the committed doc (drift = 0).

    Path-level (not byte-level) so cosmetic generator tweaks don't force a
    regen, but added/removed routes do.
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    missing = [path for path in sorted(openapi_paths) if f"`{path}`" not in text]
    assert not missing, (
        "docs/API_REFERENCE.md is stale; these paths are not documented:\n"
        + "\n".join(missing)
        + "\nregenerate: python scripts/generate_api_reference.py"
    )
