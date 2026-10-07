"""Offline legal-page checks without initializing the AI or database clients."""

import asyncio
import html
import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

import seo_routes
from config_loader import business_address, business_email, business_phone

ROOT = Path(__file__).resolve().parents[1]
OWNER_PLACEHOLDERS = ("[EFFECTIVE DATE]",)


def _all_text(page: str) -> str:
    content = seo_routes.LEGAL_PAGES[page]
    return " ".join(
        [content["title"], content["description"]] + [t for s in content["sections"] for t in s]
    )


def _frontend_legal_name() -> str:
    constants = (ROOT / "frontend/src/constants.js").read_text(encoding="utf-8")
    return re.search(r'BUSINESS_LEGAL_NAME = "([^"]+)"', constants).group(1)


@pytest.mark.parametrize("page", ["privacy", "terms"])
def test_legal_copy_uses_repo_business_facts(page):
    text = _all_text(page)
    assert _frontend_legal_name() in text
    assert business_address() in text
    assert business_email() in text
    assert business_phone() in text


@pytest.mark.parametrize("page", ["privacy", "terms"])
def test_legal_copy_keeps_only_owner_placeholders(page):
    text = _all_text(page)
    for placeholder in OWNER_PLACEHOLDERS:
        assert placeholder in text
    assert set(re.findall(r"\[[A-Z ]+\]", text)) == set(OWNER_PLACEHOLDERS)
    assert "\u2014" not in text


def test_privacy_policy_covers_how_the_site_handles_data():
    text = _all_text("privacy").lower()
    for topic in (
        "lead or contact form",
        "showroom visit",
        "ai",
        "docuseal",
        "work email",
        "allow analytics",
        "privacy choices",
        "do not type social security numbers",
    ):
        assert topic in text, topic


def test_terms_cover_listings_financing_signatures_and_staff():
    text = _all_text("terms").lower()
    for topic in (
        "is a binding offer",
        "not loan approvals",
        "electronic signature",
        "staff tools",
    ):
        assert topic in text, topic


@pytest.fixture
def legal_client(monkeypatch, tmp_path):
    shell = tmp_path / "index.html"
    shell.write_text('<html><head><title>x</title></head><body><div id="root"></div></body></html>')
    monkeypatch.setattr(seo_routes, "_index_html_path", str(shell))
    monkeypatch.setattr(
        seo_routes, "_get_canonical_base", lambda: "https://www.texashomeoutlet.com"
    )
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [])
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    app = FastAPI()

    @app.get("/sitemap.xml")
    async def sitemap():
        return seo_routes.sitemap_xml()

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        return seo_routes.render_spa_response(full_path) or seo_routes.render_not_found()

    class OfflineClient:
        def get(self, path, **kwargs):
            async def request():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    return await client.get(path, **kwargs)

            return asyncio.run(request())

    return OfflineClient()


@pytest.mark.parametrize("page", ["privacy", "terms"])
def test_legal_route_returns_200_with_complete_copy(legal_client, page):
    response = legal_client.get(f"/{page}")
    content = seo_routes.LEGAL_PAGES[page]
    assert response.status_code == 200
    assert f"<h1>{content['title']}</h1>" in response.text
    assert f'href="https://www.texashomeoutlet.com/{page}"' in response.text
    for heading, text in content["sections"]:
        assert f"<h2>{html.escape(heading)}</h2>" in response.text
        assert html.escape(text) in response.text


@pytest.mark.parametrize(
    "path,target",
    [
        ("/legal", "/privacy"),
        ("/LEGAL/", "/privacy"),
        ("/privacy-policy", "/privacy"),
        ("/terms-of-service", "/terms"),
    ],
)
def test_legal_aliases_redirect(legal_client, path, target):
    response = legal_client.get(path, follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == target


def test_legal_pages_are_in_sitemap_once(legal_client):
    sitemap = legal_client.get("/sitemap.xml").text
    for path in ("/privacy", "/terms"):
        assert sitemap.count(f"https://www.texashomeoutlet.com{path}</loc>") == 1
