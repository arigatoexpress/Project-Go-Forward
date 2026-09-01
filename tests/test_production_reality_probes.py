"""Probes that assert against production REALITY, not payload shape.

Every failure in the 2026-08-31 incident had the same shape: a check that could
not observe the thing it claimed to verify.

  * The hero watchdog reported "no photos" while the photos were fine.
  * `check_inventory_media_depth` counted STRINGS in a JSON array, so a URL
    returning 403 still counted as a photo, and a floorplan counted as a photo.
    It passed while the site's featured home was a floorplan drawing and 22
    hero images were dead.
  * 1,796 tests passed throughout.

These probes fetch the image and consult the classifier, so they can fail for
the reasons the site was actually broken.
"""

from scripts.production_smoke import (
    evaluate_floorplan_heroes,
    evaluate_hero_reachability,
)

FLOORPLAN = "https://cdn.example.com/manufacturer/1/floorplan/2/floor-plans.jpg"
PHOTO = "https://cdn.example.com/dealer/3522/inventory/9/Nassau-ext-1.jpg"
PHOTO_2 = "https://cdn.example.com/dealer/3522/inventory/9/Nassau-kitchen-2.jpg"


def _home(hid, image_url, **extra):
    home = {"id": hid, "model_name": f"Home {hid}", "image_url": image_url}
    home.update(extra)
    return home


class TestFloorplanHeroes:
    def test_passes_when_no_listing_leads_with_a_floorplan(self):
        homes = [_home("a", PHOTO), _home("b", PHOTO_2)]
        probe = evaluate_floorplan_heroes(homes, status=200, elapsed_ms=1)
        assert probe.ok is True
        assert "floorplan_heroes=0" in probe.evidence

    def test_fails_when_a_listing_leads_with_a_floorplan(self):
        # This is the state production was in: a floorplan as the home's picture.
        homes = [_home("a", PHOTO), _home("b", FLOORPLAN)]
        probe = evaluate_floorplan_heroes(homes, status=200, elapsed_ms=1)
        assert probe.ok is False
        assert "floorplan_heroes=1" in probe.evidence
        assert "b" in probe.evidence

    def test_a_home_with_no_photo_is_not_a_floorplan_hero(self):
        # Photo-less build-to-order homes render a branded placeholder by
        # design — that is not the failure this probe is looking for.
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
        # 22 hero URLs answered 403 in production while the smoke stayed green.
        homes = [_home("a", PHOTO), _home("b", PHOTO_2)]
        statuses = {PHOTO: 200, PHOTO_2: 403}
        probe = evaluate_hero_reachability(
            homes, status=200, elapsed_ms=1,
            fetch_status=lambda url: statuses[url], max_dead=0,
        )
        assert probe.ok is False
        assert "dead=1" in probe.evidence
        assert "403" in probe.evidence

    def test_tolerates_a_configured_number_of_dead_images(self):
        homes = [_home("a", PHOTO), _home("b", PHOTO_2)]
        statuses = {PHOTO: 200, PHOTO_2: 403}
        probe = evaluate_hero_reachability(
            homes, status=200, elapsed_ms=1,
            fetch_status=lambda url: statuses[url], max_dead=1,
        )
        assert probe.ok is True

    def test_reports_the_dead_url_so_staff_can_act(self):
        homes = [_home("a", PHOTO)]
        probe = evaluate_hero_reachability(
            homes, status=200, elapsed_ms=1, fetch_status=lambda url: 404, max_dead=0
        )
        assert "Nassau-ext-1.jpg" in probe.evidence


class TestDegradedClassifierFailsClosed:
    """A check that cannot see must not report green.

    `production_smoke` silently fell back to a filename-only classifier when
    `tools.photo_classifier` could not be imported — which is what happens when
    the script is run directly and the repo root is off sys.path. It reported
    `floorplan_heroes=0` while the live site served 33. The probe now reports
    the classifier mode and fails closed when degraded.
    """

    def test_evidence_names_the_classifier_mode(self):
        probe = evaluate_floorplan_heroes([_home("a", PHOTO)], status=200, elapsed_ms=1)
        assert "classifier=" in probe.evidence

    def test_degraded_classifier_fails_even_with_no_offenders(self, monkeypatch):
        import scripts.production_smoke as smoke

        monkeypatch.setattr(smoke, "CLASSIFIER_FULL", False)
        probe = smoke.evaluate_floorplan_heroes(
            [_home("a", PHOTO)], status=200, elapsed_ms=1
        )
        assert probe.ok is False
        assert "DEGRADED" in probe.evidence
