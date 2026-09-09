from tests.test_api_v1 import create_client


def test_upload_storage_outage_returns_503_without_green_success(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)
    monkeypatch.setattr(main, "log_admin_action", lambda **_kwargs: None)

    from tools import image_storage

    monkeypatch.setattr(
        image_storage,
        "store_photo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            image_storage.PhotoStorageError("internal provider detail")
        ),
    )

    response = client.post(
        "/api/inventory/43372/photos",
        files={"files": ("front.png", b"valid-enough-for-mocked-storage", "image/png")},
        headers={"X-Admin-Token": "test-token"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "home_id": "43372",
        "uploaded": [],
        "errors": [
            {
                "name": "front.png",
                "error": "Durable photo storage is temporarily unavailable.",
            }
        ],
        "storage_unavailable": True,
        "unattempted": [],
        "unattempted_count": 0,
        "retryable": ["front.png"],
    }
    assert "internal provider detail" not in response.text


def test_partial_upload_uses_non_retryable_multistatus(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)
    monkeypatch.setattr(main, "log_admin_action", lambda **_kwargs: None)

    from tools import image_storage

    calls = []

    def store(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            return image_storage.StoredPhoto("43372", "front.png", 10, "image/png", "gcs")
        raise image_storage.PhotoStorageError("internal provider detail")

    monkeypatch.setattr(image_storage, "store_photo", store)

    response = client.post(
        "/api/inventory/43372/photos",
        files=[
            ("files", ("front.png", b"first", "image/png")),
            ("files", ("side.png", b"second", "image/png")),
            ("files", ("rear.png", b"third", "image/png")),
        ],
        headers={"X-Admin-Token": "test-token"},
    )

    assert response.status_code == 207
    body = response.json()
    assert body["success"] is True
    assert body["storage_unavailable"] is True
    assert body["uploaded"] == [
        {"filename": "front.png", "url": "/api/inventory/photos/43372/front.png", "size_bytes": 10}
    ]
    assert body["errors"][0]["name"] == "side.png"
    assert body["unattempted"] == ["rear.png"]
    assert body["unattempted_count"] == 1
    assert body["retryable"] == ["side.png", "rear.png"]
    assert len(calls) == 2
    assert "internal provider detail" not in response.text


def test_upload_stops_after_first_storage_outage(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)
    monkeypatch.setattr(main, "log_admin_action", lambda **_kwargs: None)

    from tools import image_storage

    calls = []

    def store(*_args, **_kwargs):
        calls.append(True)
        raise image_storage.PhotoStorageError("internal provider detail")

    monkeypatch.setattr(image_storage, "store_photo", store)

    response = client.post(
        "/api/inventory/43372/photos",
        files=[
            ("files", ("front.png", b"first", "image/png")),
            ("files", ("side.png", b"second", "image/png")),
        ],
        headers={"X-Admin-Token": "test-token"},
    )

    assert response.status_code == 503
    assert calls == [True]
    assert response.json()["unattempted"] == ["side.png"]
    assert response.json()["unattempted_count"] == 1
    assert response.json()["retryable"] == ["front.png", "side.png"]


def test_delete_storage_outage_returns_safe_503(monkeypatch):
    client, main, _db, _logger = create_client(monkeypatch)
    monkeypatch.setattr(main, "_verify_admin_token", lambda token: True)

    from tools import image_storage

    monkeypatch.setattr(
        image_storage,
        "delete_photo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            image_storage.PhotoStorageError("internal provider detail")
        ),
    )

    response = client.delete(
        "/api/inventory/43372/photos/front.png",
        headers={"X-Admin-Token": "test-token"},
    )

    assert response.status_code == 503
    assert response.json()["message"] == "Durable photo storage is temporarily unavailable."
    assert "internal provider detail" not in response.text
