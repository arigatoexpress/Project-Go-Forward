"""Probes that assert against production reality, not payload shape.

``check_inventory_media_depth`` counts strings in a JSON array, so a URL
returning 403 still counted as a photo, and a floorplan counted as a photo.
It passed while the site's featured home was a floorplan drawing and hero
images were dead.

The floorplan probe classifies ``image_url`` and runs in the default GET
smoke. Reachability fetches image hosts and stays behind
``--check-hero-reachability`` so the deploy workflow does not start failing
on heroes that already return HTTP 403.
"""

from contextlib import contextmanager
from urllib.error import HTTPError

import pytest

from scripts import production_smoke
from scripts.production_smoke import evaluate_floorplan_heroes, evaluate_hero_reachability

FLOORPLAN = "https://cdn.example.com/manufacturer/1/floorplan/2/floor-plans.jpg"
PHOTO = "https://cdn.example.com/dealer/3522/inventory/9/Nassau-ext-1.jpg"
PHOTO_2 = "https://cdn.example.com/dealer/3522/inventory/9/Nassau-kitchen-2.jpg"


def _home(hid, image_url, **extra):
    home = {"id": hid, "model_name": f"Home {hid}", "image_url": image_url}
    home.update(extra)
    return home


@pytest.mark.parametrize("get_status", [200, 206])
def test_get_only_staff_photo_is_reachable(monkeypatch, get_status):
    requests = []

    @contextmanager
    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.method == "HEAD":
            raise HTTPError(request.full_url, 405, "Method Not Allowed", None, None)
        assert request.method == "GET"
        assert request.get_header("Range") == "bytes=0-0"
        assert timeout == 1
        # No read method: the probe must not download the response body.
        yield type("Response", (), {"status": get_status})()

    monkeypatch.setattr(production_smoke, "urlopen", fake_urlopen)
    assert (
        production_smoke._head_status(
            "https://example.test/api/inventory/photos/a/photo.jpg", timeout=1
        )
        == 200
    )
    assert len(requests) == 2


def test_reachability_does_not_retry_a_denied_image(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        raise HTTPError(request.full_url, 403, "Forbidden", None, None)

    monkeypatch.setattr(production_smoke, "urlopen", fake_urlopen)
    assert production_smoke._head_status(PHOTO, timeout=1) == 403
    assert len(requests) == 1


class TestFloorplanHeroes:
    def test_passes_when_no_listing_leads_with_a_floorplan(self):
        homes = [_home("a", PHOTO), _home("b", PHOTO_2)]
        probe = evaluate_floorplan_heroes(homes, status=200, elapsed_ms=1)
        assert probe.ok is True
        assert "floorplan_heroes=0" in probe.evidence

    def test_fails_when_a_listing_leads_with_a_floorplan(self):
        homes = [_home("a", PHOTO), _home("b", FLOORPLAN)]
        probe = evaluate_floorplan_heroes(homes, status=200, elapsed_ms=1)
        assert probe.ok is False
        assert "floorplan_heroes=1" in probe.evidence
        assert "b" in probe.evidence

    def test_a_home_with_no_photo_is_not_a_floorplan_hero(self):
        probe = evaluate_floorplan_heroes([_home("a", "")], status=200, elapsed_ms=1)
        assert probe.ok is True


class TestHeroReachability:
    def test_passes_when_every_hero_loads(self):
        homes = [_home("a", PHOTO), _home("b", PHOTO_2)]
        probe = evaluate_hero_reachability(
            homes, status=200, elapsed_ms=1, fetch_status=lambda url: 200, max_dead=0
        )
        assert probe.ok is True
        assert "dead=0" in probe.evidence

    def test_fails_when_a_hero_is_dead(self):
        homes = [_home("a", PHOTO), _home("b", PHOTO_2)]
        statuses = {PHOTO: 200, PHOTO_2: 403}
        probe = evaluate_hero_reachability(
            homes,
            status=200,
            elapsed_ms=1,
            fetch_status=lambda url: statuses[url],
            max_dead=0,
        )
        assert probe.ok is False
        assert "dead=1" in probe.evidence
        assert "403" in probe.evidence

    def test_tolerates_a_configured_number_of_dead_images(self):
        homes = [_home("a", PHOTO), _home("b", PHOTO_2)]
        statuses = {PHOTO: 200, PHOTO_2: 403}
        probe = evaluate_hero_reachability(
            homes,
            status=200,
            elapsed_ms=1,
            fetch_status=lambda url: statuses[url],
            max_dead=1,
        )
        assert probe.ok is True

    def test_reports_the_dead_url_so_staff_can_act(self):
        homes = [_home("a", PHOTO)]
        probe = evaluate_hero_reachability(
            homes, status=200, elapsed_ms=1, fetch_status=lambda url: 404, max_dead=0
        )
        assert "Nassau-ext-1.jpg" in probe.evidence

    def test_signed_url_query_is_never_included_in_evidence(self):
        probe = evaluate_hero_reachability(
            [_home("a", PHOTO + "?signature=test-only-signature")],
            status=200,
            elapsed_ms=1,
            fetch_status=lambda url: 403,
        )
        assert "Nassau-ext-1.jpg" in probe.evidence
        assert "signature" not in probe.evidence


class TestDegradedClassifierFailsClosed:
    def test_evidence_names_the_classifier_mode(self):
        probe = evaluate_floorplan_heroes([_home("a", PHOTO)], status=200, elapsed_ms=1)
        assert "classifier=" in probe.evidence

    def test_degraded_classifier_fails_even_with_no_offenders(self, monkeypatch):
        monkeypatch.setattr(production_smoke, "CLASSIFIER_FULL", False)
        probe = production_smoke.evaluate_floorplan_heroes(
            [_home("a", PHOTO)], status=200, elapsed_ms=1
        )
        assert probe.ok is False
        assert "DEGRADED" in probe.evidence

    def test_missing_manifest_fails_even_when_import_succeeds(self, monkeypatch):
        monkeypatch.setattr(production_smoke, "content_manifest_available", lambda: False)
        probe = production_smoke.evaluate_floorplan_heroes(
            [_home("a", PHOTO)], status=200, elapsed_ms=1
        )
        assert not probe.ok
        assert "DEGRADED" in probe.evidence


def test_media_reality_resolves_staff_photo_urls_and_preserves_absolute_urls(monkeypatch):
    homes = [_home("staff", "/api/inventory/photos/43372/photo.jpg"), _home("vendor", PHOTO)]
    monkeypatch.setattr(
        production_smoke, "_json_probe", lambda *args, **kwargs: (200, {"homes": homes}, 1)
    )
    fetched = []

    def fetch(url, *, timeout):
        fetched.append(url)
        return 200

    monkeypatch.setattr(production_smoke, "_head_status", fetch)
    probes = production_smoke.check_inventory_media_reality(
        "https://example.test",
        timeout=1,
        hero_sample=0,
        max_dead_heroes=0,
        include_reachability=True,
    )
    assert all(probe.ok for probe in probes)
    assert fetched == ["https://example.test/api/inventory/photos/43372/photo.jpg", PHOTO]


def test_default_media_reality_does_not_fetch_images(monkeypatch):
    homes = [_home("a", PHOTO)]
    monkeypatch.setattr(
        production_smoke, "_json_probe", lambda *args, **kwargs: (200, {"homes": homes}, 1)
    )

    def fetch(url, *, timeout):
        raise AssertionError(f"default smoke fetched {url}")

    monkeypatch.setattr(production_smoke, "_head_status", fetch)
    probes = production_smoke.check_inventory_media_reality(
        "https://example.test",
        timeout=1,
        hero_sample=40,
        max_dead_heroes=0,
    )
    assert [probe.name for probe in probes] == ["inventory heroes are photos, not floorplans"]
    assert probes[0].ok is True


@pytest.mark.parametrize("sample,checked", [(1, 1), (0, 2)])
def test_reachability_sample_bound(sample, checked):
    fetched = []

    def fetch(url):
        fetched.append(url)
        return 200

    probe = evaluate_hero_reachability(
        [_home("a", PHOTO), _home("b", PHOTO_2)],
        status=200,
        elapsed_ms=1,
        fetch_status=fetch,
        sample=sample,
    )
    assert probe.ok
    assert len(fetched) == checked


@pytest.mark.parametrize("flag", ["--hero-sample", "--max-dead-heroes"])
def test_cli_rejects_negative_bounds(flag):
    with pytest.raises(SystemExit) as exc:
        production_smoke._parse_args([flag, "-1"])
    assert exc.value.code == 2
