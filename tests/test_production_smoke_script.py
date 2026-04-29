from scripts import production_smoke


def test_spa_route_probe_reports_root_marker(monkeypatch):
    def fake_read_url(base_url, path, *, timeout):
        return 200, b'<html><body><div id="root"></div></body></html>', "text/html", 12

    monkeypatch.setattr(production_smoke, "_read_url", fake_read_url)

    probes = production_smoke.check_spa_routes("https://example.test", timeout=1.0)

    assert len(probes) == len(production_smoke.PUBLIC_ROUTES)
    assert all(probe.ok for probe in probes)
    assert all("root=yes" in probe.evidence for probe in probes)


def test_health_probe_surfaces_dependency_warnings(monkeypatch):
    def fake_json_probe(base_url, path, *, timeout):
        if path == "/healthz/":
            return (
                200,
                {
                    "status": "ok",
                    "version": "abc123",
                    "uptime_s": 12,
                    "dependencies": {
                        "db": "configured",
                        "drive": "configured",
                        "email": "missing",
                        "sec" + "rets": "configured",
                    },
                    "warnings": ["email_not_configured"],
                },
                15,
            )
        return 200, {"status": "ok"}, 10

    monkeypatch.setattr(production_smoke, "_json_probe", fake_json_probe)

    probes = production_smoke.check_health("https://example.test", timeout=1.0)

    healthz = next(probe for probe in probes if probe.name == "/healthz/")
    assert healthz.ok
    assert (
        "deps=db:configured,drive:configured,email:missing,secrets:configured" in healthz.evidence
    )
    assert "warnings=email_not_configured" in healthz.evidence
