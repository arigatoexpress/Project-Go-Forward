"""Offline legal-route checks without initializing the AI or database clients."""

import asyncio
import html

import httpx
import pytest
from fastapi import FastAPI

import seo_routes


@pytest.fixture
def legal_client(monkeypatch, tmp_path):
    shell = tmp_path / "index.html"
    shell.write_text('<html><head></head><body><div id="root"></div></body></html>')
    monkeypatch.setattr(seo_routes, "_index_html_path", str(shell))
    monkeypatch.setattr(
        seo_routes, "_get_canonical_base", lambda: "https://www.texashomeoutlet.com"
    )
    monkeypatch.setattr(seo_routes, "_get_homes", lambda: [])
    monkeypatch.setattr(seo_routes, "_registry_cache", None)
    app = FastAPI()

    # Exercise HTTP responses without the synchronous TestClient worker portal.
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


@pytest.mark.parametrize(
    "host", ["www.texashomeoutlet.com", "texashomeoutlet.com", "tho.sapphirealpha.xyz"]
)
@pytest.mark.parametrize("page", ["privacy", "terms"])
def test_legal_route_returns_200_and_complete_copy(legal_client, host, page):
    response = legal_client.get(f"/{page}", headers={"Host": host})
    content = seo_routes.LEGAL_PAGES[page]
    assert response.status_code == 200
    assert f"<h1>{content['title']}</h1>" in response.text
    assert f'href="https://www.texashomeoutlet.com/{page}"' in response.text
    for heading, text in content["sections"]:
        assert html.escape(heading) in response.text
        assert html.escape(text) in response.text
        assert "\u2014" not in text
    for placeholder in (
        "[LEGAL BUSINESS NAME]",
        "[MAILING ADDRESS]",
        "[CONTACT EMAIL]",
        "[EFFECTIVE DATE]",
    ):
        assert placeholder in response.text


@pytest.mark.parametrize("path", ["/privacy", "/terms"])
def test_legal_route_canonicalization_sitemap_and_unknown_paths(legal_client, path):
    for variant in (path + "/", path.title()):
        response = legal_client.get(variant, follow_redirects=False)
        assert response.status_code == 301
        assert response.headers["location"] == path
    assert (
        legal_client.get("/sitemap.xml").text.count(f"https://www.texashomeoutlet.com{path}</loc>")
        == 1
    )
    assert legal_client.get(path + "/unknown").status_code == 404
