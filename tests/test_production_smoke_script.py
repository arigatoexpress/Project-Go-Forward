from scripts import production_smoke


class _TimeoutContext:
    def __enter__(self):
        raise TimeoutError("timed out")

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_default_base_url_targets_public_domain():
    assert production_smoke.DEFAULT_BASE_URL == "https://www.texashomeoutlet.com"


def test_default_inventory_floor_matches_current_public_catalog_size():
    assert production_smoke.DEFAULT_MIN_HOMES == 10


def test_canonical_authority_probe_accepts_public_domain(monkeypatch):
    def fake_read_url(base_url, path, *, timeout):
        if path == "/":
            body = b'<link rel="canonical" href="https://www.texashomeoutlet.com/">'
            return 200, body, "text/html", 8
        if path == "/robots.txt":
            body = b"User-agent: *\nSitemap: https://www.texashomeoutlet.com/sitemap.xml\n"
            return 200, body, "text/plain", 4
        body = b"<urlset><url><loc>https://www.texashomeoutlet.com/</loc></url></urlset>"
        return 200, body, "application/xml", 5

    monkeypatch.setattr(production_smoke, "_read_url", fake_read_url)

    probe = production_smoke.check_canonical_authority(
        "https://www.texashomeoutlet.com", timeout=1.0
    )

    assert probe.ok
    assert "homepage=yes" in probe.evidence
    assert "robots=yes" in probe.evidence
    assert "sitemap=yes" in probe.evidence


def test_canonical_authority_probe_rejects_staging_domain(monkeypatch):
    def fake_read_url(base_url, path, *, timeout):
        if path == "/":
            body = b'<link rel="canonical" href="https://tho.sapphirealpha.xyz/">'
            return 200, body, "text/html", 8
        if path == "/robots.txt":
            body = b"User-agent: *\nSitemap: https://tho.sapphirealpha.xyz/sitemap.xml\n"
            return 200, body, "text/plain", 4
        body = b"<urlset><url><loc>https://tho.sapphirealpha.xyz/</loc></url></urlset>"
        return 200, body, "application/xml", 5

    monkeypatch.setattr(production_smoke, "_read_url", fake_read_url)

    probe = production_smoke.check_canonical_authority(
        "https://www.texashomeoutlet.com", timeout=1.0
    )

    assert not probe.ok
    assert "homepage=no" in probe.evidence
    assert "robots=no" in probe.evidence
    assert "sitemap=no" in probe.evidence


def test_candidate_canonical_probe_uses_machine_paths_and_expected_origin(monkeypatch):
    seen_paths = []

    def fake_read_url(base_url, path, *, timeout):
        seen_paths.append(path)
        if path == "/robots.txt":
            body = b"User-agent: *\nSitemap: https://www.texashomeoutlet.com/sitemap.xml\n"
            return 200, body, "text/plain", 4
        body = b"<urlset><url><loc>https://www.texashomeoutlet.com/</loc></url></urlset>"
        return 200, body, "application/xml", 5

    monkeypatch.setattr(production_smoke, "_read_url", fake_read_url)

    probe = production_smoke.check_canonical_authority(
        "https://candidate---service.example.run.app",
        timeout=1.0,
        canonical_origin="https://www.texashomeoutlet.com",
    )

    assert probe.ok
    assert seen_paths == ["/robots.txt", "/sitemap.xml"]
    assert "homepage=machine-path-proxy" in probe.evidence


def test_spa_route_probe_reports_root_marker(monkeypatch):
    def fake_read_url(base_url, path, *, timeout):
        return 200, b'<html><body><div id="root"></div></body></html>', "text/html", 12

    monkeypatch.setattr(production_smoke, "_read_url", fake_read_url)

    probes = production_smoke.check_spa_routes("https://example.test", timeout=1.0)

    assert len(probes) == len(production_smoke.PUBLIC_ROUTES)
    assert all(probe.ok for probe in probes)
    assert all("root=yes" in probe.evidence for probe in probes)


def test_read_probe_reports_timeout_with_path_context(monkeypatch):
    def fake_urlopen(request, timeout):
        return _TimeoutContext()

    monkeypatch.setattr(production_smoke, "urlopen", fake_urlopen)

    try:
        production_smoke._read_url("https://example.test", "/slow", timeout=2.0)
    except RuntimeError as exc:
        assert str(exc) == "/slow timed out after 2s"
    else:
        raise AssertionError("expected timeout to be wrapped")


def test_post_probe_reports_timeout_with_path_context(monkeypatch):
    def fake_urlopen(request, timeout):
        return _TimeoutContext()

    monkeypatch.setattr(production_smoke, "urlopen", fake_urlopen)

    try:
        production_smoke._post_json(
            "https://example.test",
            "/api/slow",
            {},
            timeout=3.0,
            admin_token=None,
        )
    except RuntimeError as exc:
        assert str(exc) == "/api/slow timed out after 3s"
    else:
        raise AssertionError("expected timeout to be wrapped")


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
    assert "media_target_homes=19" in probe.evidence
    assert "required_rich=19" in probe.evidence
    assert "required_matterport=6" in probe.evidence


def test_media_depth_probe_targets_current_inventory_not_orderable_floorplans(monkeypatch):
    current_homes = [
        {
            "inventory_kind": "available_now",
            "real_photos": [
                "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/1/ext.jpg",
                "kitchen.jpg",
                "bed.jpg",
            ],
            "gallery_images": ["one.jpg", "two.jpg", "three.jpg"],
        }
        for _ in range(19)
    ]
    orderable_floorplans = [
        {
            "inventory_kind": "orderable_floorplan",
            "real_photos": [],
            "gallery_images": [],
            "matterport_url": "https://my.matterport.com/show/?m=floorplan",
        }
        for _ in range(260)
    ]

    def fake_json_probe(base_url, path, *, timeout):
        return 200, {"homes": [*current_homes, *orderable_floorplans]}, 10

    monkeypatch.setattr(production_smoke, "_json_probe", fake_json_probe)

    probe = production_smoke.check_inventory_media_depth("https://example.test", timeout=1.0)

    assert probe.ok
    assert "real_photo_rich=19" in probe.evidence
    assert "media_target_homes=19" in probe.evidence
    assert "required_rich=19" in probe.evidence
    assert "matterport=260" in probe.evidence


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


# ───────────────── /run agent-reply correctness probe (gate #4a) ─────────────


def test_run_reply_probe_passes_on_non_error_agent_reply(monkeypatch):
    """A real, non-error /run reply makes the probe PASS.

    The whole point: a re-introduced prompt-strip makes /run return an error
    envelope, which must FAIL this probe (the inverse case below). A liveness
    probe alone never catches that, because the process stays up.
    """

    def fake_post_json(base_url, path, payload, *, timeout, admin_token):
        assert path == "/run"
        assert admin_token is None  # public endpoint, no secret needed
        return 200, b'{"text": "Hi! We have several homes available."}', "application/json", 30

    monkeypatch.setattr(production_smoke, "_post_json", fake_post_json)

    probe = production_smoke.check_run_reply("https://example.test", timeout=5.0)
    assert probe.ok
    assert probe.status == 200


def test_run_reply_probe_fails_on_error_envelope(monkeypatch):
    """If /run returns the {"error": ...} envelope (the #223 prompt-strip
    symptom), the probe must FAIL — not silently pass."""

    def fake_post_json(base_url, path, payload, *, timeout, admin_token):
        return (
            200,
            b'{"error": "AI service temporarily unavailable. Please try again later."}',
            "application/json",
            20,
        )

    monkeypatch.setattr(production_smoke, "_post_json", fake_post_json)

    probe = production_smoke.check_run_reply("https://example.test", timeout=5.0)
    assert not probe.ok
    assert "error" in probe.evidence.lower()


def test_run_reply_probe_fails_on_empty_text(monkeypatch):
    """A 200 with an empty/missing text field is not a real reply -> FAIL."""

    def fake_post_json(base_url, path, payload, *, timeout, admin_token):
        return 200, b'{"text": ""}', "application/json", 10

    monkeypatch.setattr(production_smoke, "_post_json", fake_post_json)

    probe = production_smoke.check_run_reply("https://example.test", timeout=5.0)
    assert not probe.ok


# ────────────── admin-auth liveness probe (wrong PIN -> 401) (gate #4b) ───────


def test_admin_verify_probe_passes_on_wellformed_401(monkeypatch):
    """POSTing an obviously-wrong PIN must return a structured 401.

    Verifies the auth endpoint + its rate-limit/lockout path are alive WITHOUT
    needing the real secret. A correct, well-formed rejection is the success
    condition.
    """
    calls = []

    def fake_post_json(base_url, path, payload, *, timeout, admin_token):
        calls.append((path, payload))
        return 401, b'{"success": false, "error": "Incorrect PIN."}', "application/json", 12

    monkeypatch.setattr(production_smoke, "_post_json", fake_post_json)

    probe = production_smoke.check_admin_auth_liveness("https://example.test", timeout=5.0)
    assert probe.ok
    assert probe.status == 401
    # The PIN we send must be obviously-wrong, never a real secret.
    path, payload = calls[0]
    assert path == "/api/admin/verify"
    assert payload.get("pin") and payload["pin"] != ""


def test_admin_verify_probe_accepts_429_lockout(monkeypatch):
    """A 429 lockout is also a healthy auth path (rate-limit alive)."""

    def fake_post_json(base_url, path, payload, *, timeout, admin_token):
        return (
            429,
            b'{"success": false, "error": "Too many failed attempts. Please wait 5 minutes."}',
            "application/json",
            12,
        )

    monkeypatch.setattr(production_smoke, "_post_json", fake_post_json)

    probe = production_smoke.check_admin_auth_liveness("https://example.test", timeout=5.0)
    assert probe.ok
    assert probe.status == 429


def test_admin_verify_probe_fails_on_unexpected_200(monkeypatch):
    """A 200 to a wrong PIN means auth is broken/open -> FAIL."""

    def fake_post_json(base_url, path, payload, *, timeout, admin_token):
        return 200, b'{"success": true, "csrf_token": "x"}', "application/json", 12

    monkeypatch.setattr(production_smoke, "_post_json", fake_post_json)

    probe = production_smoke.check_admin_auth_liveness("https://example.test", timeout=5.0)
    assert not probe.ok


def test_run_and_admin_probes_are_opt_in_in_run_smoke(monkeypatch):
    """The new live-write/agent probes must be opt-in so the default run stays
    read-only and cheap. They only fire when explicitly enabled."""
    seen_paths = []

    def fake_post_json(base_url, path, payload, *, timeout, admin_token):
        seen_paths.append(path)
        return 200, b'{"text": "ok"}', "application/json", 10

    def fake_read_url(base_url, path, *, timeout):
        return 200, b'<html><body><div id="root"></div></body></html>', "text/html", 5

    def fake_json_probe(base_url, path, *, timeout):
        if path.startswith("/healthz") or path == "/health":
            return 200, {"status": "ok", "version": "x", "uptime_s": 1}, 5
        if "inventory-context" in path:
            return 200, {"success": True, "homes": []}, 5
        if "slots" in path:
            return 200, {"date": "x", "available_slots": ["9:00"]}, 5
        if "passkey/status" in path:
            return (
                200,
                {"enabled": True, "persistent": True, "rp_ids": ["sapphirealpha.xyz"]},
                5,
            )
        return 200, {}, 5

    monkeypatch.setattr(production_smoke, "_post_json", fake_post_json)
    monkeypatch.setattr(production_smoke, "_read_url", fake_read_url)
    monkeypatch.setattr(production_smoke, "_json_probe", fake_json_probe)

    production_smoke.run_smoke("https://example.test", timeout=1.0, min_homes=0)
    assert "/run" not in seen_paths
    assert "/api/admin/verify" not in seen_paths

    seen_paths.clear()
    production_smoke.run_smoke(
        "https://example.test",
        timeout=1.0,
        min_homes=0,
        check_run_reply=True,
        check_admin_auth=True,
    )
    assert "/run" in seen_paths
    assert "/api/admin/verify" in seen_paths


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
