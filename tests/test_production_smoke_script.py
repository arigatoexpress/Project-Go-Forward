from scripts import production_smoke


def test_spa_route_probe_reports_root_marker(monkeypatch):
    def fake_read_url(base_url, path, *, timeout):
        return 200, b'<html><body><div id="root"></div></body></html>', "text/html", 12

    monkeypatch.setattr(production_smoke, "_read_url", fake_read_url)

    probes = production_smoke.check_spa_routes("https://example.test", timeout=1.0)

    assert len(probes) == len(production_smoke.PUBLIC_ROUTES)
    assert all(probe.ok for probe in probes)
    assert all("root=yes" in probe.evidence for probe in probes)
