from scripts import production_smoke


def test_default_base_url_targets_tho_subdomain():
    assert production_smoke.DEFAULT_BASE_URL == "https://tho.sapphirealpha.xyz"


def test_default_inventory_floor_matches_current_public_catalog_size():
    assert production_smoke.DEFAULT_MIN_HOMES == 10


def test_spa_route_probe_reports_root_marker(monkeypatch):
    def fake_read_url(base_url, path, *, timeout):
        return 200, b'<html><body><div id="root"></div></body></html>', "text/html", 12

    monkeypatch.setattr(production_smoke, "_read_url", fake_read_url)

    probes = production_smoke.check_spa_routes("https://example.test", timeout=1.0)

    assert len(probes) == len(production_smoke.PUBLIC_ROUTES)
    assert all(probe.ok for probe in probes)
    assert all("root=yes" in probe.evidence for probe in probes)


def test_media_depth_probe_requires_real_gallery_and_tours(monkeypatch):
    homes = [
        {
            "real_photos": [
                "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/1/ext.jpg",
                "kitchen.jpg",
                "bed.jpg",
            ],
            "gallery_images": ["one.jpg", "two.jpg", "three.jpg"],
            "matterport_url": "https://my.matterport.com/show/?m=test",
        }
        for _ in range(30)
    ]

    def fake_json_probe(base_url, path, *, timeout):
        return 200, {"homes": homes}, 10

    monkeypatch.setattr(production_smoke, "_json_probe", fake_json_probe)

    probe = production_smoke.check_inventory_media_depth("https://example.test", timeout=1.0)

    assert probe.ok
    assert "real_photo_rich=30" in probe.evidence
    assert "matterport=30" in probe.evidence


def test_media_depth_probe_scales_to_smaller_live_catalog(monkeypatch):
    homes = [
        {
            "real_photos": [
                "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/1/ext.jpg",
                "kitchen.jpg",
                "bed.jpg",
            ],
            "gallery_images": ["one.jpg", "two.jpg", "three.jpg"],
            "matterport_url": "https://my.matterport.com/show/?m=test" if idx < 7 else None,
        }
        for idx in range(19)
    ]

    def fake_json_probe(base_url, path, *, timeout):
        return 200, {"homes": homes}, 10

    monkeypatch.setattr(production_smoke, "_json_probe", fake_json_probe)

    probe = production_smoke.check_inventory_media_depth("https://example.test", timeout=1.0)

    assert probe.ok
    assert "real_photo_rich=19" in probe.evidence
    assert "matterport=7" in probe.evidence
    assert "required_rich=19" in probe.evidence
    assert "required_matterport=6" in probe.evidence


def test_media_depth_probe_does_not_count_floorplans_as_photos(monkeypatch):
    floorplan = "https://cdn.example.com/floor-plans.jpg"
    homes = [
        {
            "image_url": floorplan,
            "real_photos": [floorplan],
            "gallery_images": [floorplan],
            "floorplan_url": floorplan,
        }
        for _ in range(30)
    ]

    def fake_json_probe(base_url, path, *, timeout):
        return 200, {"homes": homes}, 10

    monkeypatch.setattr(production_smoke, "_json_probe", fake_json_probe)

    probe = production_smoke.check_inventory_media_depth("https://example.test", timeout=1.0)

    assert not probe.ok
    assert "real_photo_rich=0" in probe.evidence
    assert "gallery_rich=0" in probe.evidence


def test_safe_public_validation_uses_invalid_non_writing_payloads(monkeypatch):
    calls = []

    def fake_post_json(base_url, path, payload, *, timeout, admin_token):
        calls.append((path, payload, admin_token))
        if path == "/api/feedback":
            return (
                400,
                b'{"success":false,"message":"Description is required"}',
                "application/json",
                9,
            )
        return 200, b'{"success":false,"error":"required"}', "application/json", 8

    monkeypatch.setattr(production_smoke, "_post_json", fake_post_json)

    probes = production_smoke.check_safe_public_validation(
        "https://example.test",
        timeout=1.0,
    )

    assert all(probe.ok for probe in probes)
    assert all(payload == {} and admin_token is None for _, payload, admin_token in calls)


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
