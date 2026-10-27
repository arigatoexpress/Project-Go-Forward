"""Staff Matterport tours: strict input, auth, persistence, and public overlay.

Run: python -m pytest tests/test_staff_matterport_tour.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_api_v1 import create_client  # noqa: E402
from tools import tour_storage  # noqa: E402

VALID_ID = "JbALitnto3g"
OTHER_ID = "X4kxMpwHFCj"
CANONICAL = f"https://my.matterport.com/show/?m={VALID_ID}&play=1"
SHOW_URL = f"https://my.matterport.com/show/?m={VALID_ID}&play=1&next=https://evil.example"


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(tour_storage, "_LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(tour_storage, "_grouped_cache", None)
    monkeypatch.setattr(tour_storage, "_grouped_cache_at", 0.0)
    return tour_storage


def _headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


class _Blob:
    def __init__(self, store, name):
        self._store = store
        self.name = name

    def upload_from_string(self, data, content_type=None):
        self._store[self.name] = data

    def download_as_bytes(self):
        return self._store[self.name]

    def exists(self):
        return self.name in self._store

    def delete(self):
        del self._store[self.name]


class _Bucket:
    def __init__(self):
        self.store = {}

    def blob(self, name):
        return _Blob(self.store, name)

    def list_blobs(self, prefix=""):
        return [_Blob(self.store, name) for name in self.store if name.startswith(prefix)]


@pytest.mark.parametrize(
    "value",
    [
        VALID_ID,
        f"  {VALID_ID}  ",
        SHOW_URL,
        f"https://my.matterport.com/show?m={VALID_ID}",
        f"https://MY.MATTERPORT.COM/show/?m={VALID_ID}",
        f"https://my.matterport.com:443/show/?m={VALID_ID}&brand=0",
        "https://my.matterport.com/show/?m=%4AbALitnto3g",
    ],
)
def test_normalize_accepts_id_or_matterport_show_url(value):
    assert tour_storage.normalize_matterport_input(value) == VALID_ID


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "not-a-tour",
        "JbALitnto3",
        "JbALitnto3gX",
        "javascript:alert(1)",
        "<script>alert(1)</script>",
        f"https://evil.example/show/?m={VALID_ID}",
        f"https://my.matterport.com.evil.example/show/?m={VALID_ID}",
        f"http://my.matterport.com/show/?m={VALID_ID}",
        "https://" + "user" + ":" + "pass" + f"@my.matterport.com/show/?m={VALID_ID}",
        f"https://my.matterport.com/models/{VALID_ID}",
        "https://my.matterport.com/show/?m=<script>alert(1)</script>",
        f'{VALID_ID}"><img src=x onerror=alert(1)>',
        f"https://my.matterport.com/show/?m={VALID_ID}&m={OTHER_ID}",
        f"//my.matterport.com/show/?m={VALID_ID}",
        "https://my.matterport.com/show/?url=https://evil.example",
        f"https://my.matterport.com:444/show/?m={VALID_ID}",
        f"https://my.matterport.com/show/?m={VALID_ID}\nSet-Cookie:a=b",
        None,
        123,
        {"m": VALID_ID},
    ],
)
def test_normalize_rejects_junk_and_markup(value):
    with pytest.raises(tour_storage.TourValidationError):
        tour_storage.normalize_matterport_input(value)


def test_local_tour_persists_and_corrupt_files_are_ignored(monkeypatch, tmp_path):
    storage = _isolate(monkeypatch, tmp_path)
    stored = storage.set_tour("../43372", VALID_ID)
    assert stored.home_id == "43372"
    assert stored.matterport_url == CANONICAL
    assert json.loads((tmp_path / "43372.json").read_text()) == {"matterport_id": VALID_ID}
    assert not (tmp_path.parent / "43372.json").exists()

    storage._grouped_cache = None
    assert storage.get_tour("43372").matterport_id == VALID_ID
    assert storage.list_all() == {"43372": VALID_ID}

    (tmp_path / "bad.json").write_text(
        json.dumps({"matterport_id": "javascript:alert(1)"}),
        encoding="utf-8",
    )
    storage._grouped_cache = None
    assert "bad" not in storage.list_all()
    assert storage.clear_tour("43372") is True
    assert storage.get_tour("43372") is None
    assert storage.clear_tour("43372") is False


def test_cloud_run_refuses_a_local_only_write(monkeypatch, tmp_path):
    storage = _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("K_SERVICE", "project-go-forward")
    with pytest.raises(storage.TourStorageError):
        storage.set_tour("43372", VALID_ID)
    assert list(tmp_path.iterdir()) == []


def test_gcs_round_trip_does_not_keep_a_local_copy(monkeypatch, tmp_path):
    storage = _isolate(monkeypatch, tmp_path)
    bucket = _Bucket()
    monkeypatch.setattr(storage, "_get_bucket", lambda: bucket)
    stored = storage.set_tour("43372", VALID_ID)
    assert stored.backend == "gcs"
    assert storage.get_tour("43372").matterport_id == VALID_ID
    assert storage.list_all(use_cache=False) == {"43372": VALID_ID}
    assert storage.clear_tour("43372") is True
    assert storage.get_tour("43372") is None
    assert list(tmp_path.iterdir()) == []


def test_overlay_replaces_catalog_tour_and_leaves_other_homes_alone(monkeypatch, tmp_path):
    storage = _isolate(monkeypatch, tmp_path)
    _client, main, _db, _logger = create_client(monkeypatch)
    storage.set_tour("43372", VALID_ID)
    homes = [
        {
            "id": "43372",
            "matterport_id": OTHER_ID,
            "matterport_url": "https://evil.example/tour",
        },
        {
            "id": "99999",
            "matterport_id": OTHER_ID,
            "matterport_url": CANONICAL.replace(VALID_ID, OTHER_ID),
        },
        {"id": "  "},
        {"name": "no id"},
    ]
    main._overlay_staff_tours(homes)
    assert homes[0]["matterport_id"] == VALID_ID
    assert homes[0]["matterport_url"] == CANONICAL
    assert homes[0]["has_staff_tour"] is True
    assert "evil.example" not in homes[0]["matterport_url"]
    assert homes[1]["matterport_id"] == OTHER_ID
    assert homes[1].get("has_staff_tour") is not True
    assert homes[2] == {"id": "  "}
    assert homes[3] == {"name": "no id"}


def _legacy_homes():
    return {
        "success": True,
        "source": "legacy_site_live",
        "homes": [
            {
                "id": "43372",
                "model_name": "Starter Home",
                "real_photos": ["https://example.com/a.jpg"],
            },
            {
                "id": "99999",
                "model_name": "Catalog Tour",
                "matterport_id": OTHER_ID,
                "matterport_url": f"https://my.matterport.com/show/?m={OTHER_ID}&play=1",
            },
        ],
        "total_inventory": 2,
    }


def test_endpoint_saves_url_shows_on_feed_clears_and_persists(monkeypatch, tmp_path):
    storage = _isolate(monkeypatch, tmp_path)
    client, main, _db, _logger = create_client(monkeypatch)
    logged = []
    monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: logged.append(kwargs))
    monkeypatch.setattr(main, "load_legacy_inventory_context", lambda **kwargs: _legacy_homes())
    headers = _headers(main)
    assert storage.list_all() == {}

    saved = client.post(
        "/api/inventory/43372/tour",
        headers=headers,
        json={"matterport": SHOW_URL},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["matterport_id"] == VALID_ID
    assert body["matterport_url"] == CANONICAL
    assert "evil.example" not in saved.text
    assert "next=" not in saved.text
    assert logged[-1]["action"] == "inventory.tour_set"
    assert logged[-1]["details"] == {"matterport_id": VALID_ID}
    assert "evil.example" not in json.dumps(logged[-1]["details"])

    storage._grouped_cache = None
    stored = json.loads((tmp_path / "43372.json").read_text())
    assert stored == {"matterport_id": VALID_ID}

    again = client.get("/api/inventory/43372/tour", headers=headers)
    assert again.status_code == 200
    assert again.json()["matterport_id"] == VALID_ID

    public = client.get("/api/marketing/inventory-context")
    assert public.status_code == 200
    homes = {home["id"]: home for home in public.json()["homes"]}
    assert homes["43372"]["matterport_id"] == VALID_ID
    assert homes["43372"]["matterport_url"] == CANONICAL
    assert homes["43372"]["has_staff_tour"] is True
    assert homes["99999"]["matterport_id"] == OTHER_ID
    assert homes["99999"].get("has_staff_tour") is not True

    bare = client.post(
        "/api/inventory/43372/tour",
        headers=headers,
        json={"matterport": VALID_ID},
    )
    assert bare.status_code == 200
    assert bare.json()["matterport_url"] == CANONICAL

    cleared = client.delete("/api/inventory/43372/tour", headers=headers)
    assert cleared.status_code == 200
    assert cleared.json()["matterport_id"] is None
    assert logged[-1]["action"] == "inventory.tour_clear"
    assert client.get("/api/inventory/43372/tour", headers=headers).json()["matterport_id"] is None
    assert not (tmp_path / "43372.json").exists()

    after = {
        home["id"]: home for home in client.get("/api/marketing/inventory-context").json()["homes"]
    }
    assert after["43372"].get("matterport_id") in (None, "")
    assert after["43372"].get("has_staff_tour") is not True
    assert after["99999"]["matterport_id"] == OTHER_ID


@pytest.mark.parametrize(
    "payload",
    [
        {"matterport": "<script>alert(1)</script>"},
        {"matterport": "javascript:alert(1)"},
        {"matterport": "https://evil.example/show/?m=JbALitnto3g"},
        {"matterport": "not a tour"},
        {"matterport": ""},
        {"url": SHOW_URL},
        {},
    ],
)
def test_endpoint_rejects_junk_and_xss_without_saving(monkeypatch, tmp_path, payload):
    _isolate(monkeypatch, tmp_path)
    client, main, _db, _logger = create_client(monkeypatch)
    response = client.post(
        "/api/inventory/43372/tour",
        headers=_headers(main),
        json=payload,
    )
    assert response.status_code == 400
    assert response.json()["message"] == tour_storage.INVALID_TOUR_MESSAGE
    text = response.text
    assert "<script" not in text
    assert "javascript:" not in text
    assert "evil.example" not in text
    assert list(tmp_path.iterdir()) == []


def test_unauthenticated_and_non_staff_are_rejected(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    client, main, _db, _logger = create_client(monkeypatch, tho_api_key="tho-secret")
    target = "/api/inventory/43372/tour"
    body = {"matterport": VALID_ID}

    missing = client.post(target, json=body)
    assert missing.status_code == 401
    assert client.get(target).status_code == 401
    assert client.delete(target).status_code == 401

    outsider = main._create_admin_token("visitor@example.com")
    rejected = client.post(target, json=body, headers={"X-Admin-Token": outsider})
    assert rejected.status_code == 401

    partner = client.post(target, json=body, headers={"Authorization": "Bearer tho-secret"})
    assert partner.status_code == 401
    assert list(tmp_path.iterdir()) == []


def test_cookie_session_requires_csrf_and_matching_token_saves(monkeypatch, tmp_path):
    storage = _isolate(monkeypatch, tmp_path)
    client, main, _db, _logger = create_client(monkeypatch)
    token = main._create_admin_token()
    client.cookies.set("tho_admin_token", token)
    blocked = client.post("/api/inventory/43372/tour", json={"matterport": VALID_ID})
    assert blocked.status_code == 403
    assert list(tmp_path.iterdir()) == []

    client.cookies.set("tho_csrf_token", "csrf-value")
    saved = client.post(
        "/api/inventory/43372/tour",
        json={"matterport": SHOW_URL},
        headers={"X-CSRF-Token": "csrf-value"},
    )
    assert saved.status_code == 200
    assert saved.json()["matterport_id"] == VALID_ID
    assert storage.get_tour("43372").matterport_id == VALID_ID

    mismatch = client.delete(
        "/api/inventory/43372/tour",
        headers={"X-CSRF-Token": "other-value"},
    )
    assert mismatch.status_code == 403
    assert storage.get_tour("43372") is not None


def test_invalid_home_id_is_rejected(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    client, main, _db, _logger = create_client(monkeypatch)
    response = client.post(
        "/api/inventory/----/tour",
        headers=_headers(main),
        json={"matterport": VALID_ID},
    )
    assert response.status_code == 400
    assert response.json()["message"] == tour_storage.INVALID_HOME_MESSAGE
    assert list(tmp_path.iterdir()) == []
