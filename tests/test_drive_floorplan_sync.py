"""Unit tests for tools/drive_floorplan_sync.py."""

import tools.drive_floorplan_sync as sync


def _patch_children(monkeypatch, children_by_parent):
    def fake_list_children(_svc, parent_id):
        yield from children_by_parent.get(parent_id, [])

    monkeypatch.setattr(sync, "_list_children", fake_list_children)


def test_walk_skips_root_level_floorplan_file(monkeypatch):
    _patch_children(
        monkeypatch,
        {
            "root": [
                {
                    "id": "loose-pdf",
                    "name": "Loose Floorplan.pdf",
                    "mimeType": "application/pdf",
                    "size": "123",
                }
            ]
        },
    )

    assert list(sync.walk_tho_folder(object(), "root")) == []


def test_walk_descends_only_allowed_root_manufacturer_folders(monkeypatch):
    _patch_children(
        monkeypatch,
        {
            "root": [
                {
                    "id": "cavco-folder",
                    "name": "Cavco",
                    "mimeType": "application/vnd.google-apps.folder",
                },
                {
                    "id": "mark-folder",
                    "name": "Mark",
                    "mimeType": "application/vnd.google-apps.folder",
                },
            ],
            "cavco-folder": [
                {
                    "id": "plan-1",
                    "name": "../Plan A.pdf",
                    "mimeType": "application/pdf",
                    "size": "456",
                }
            ],
            "mark-folder": [
                {
                    "id": "customer-file",
                    "name": "Customer.pdf",
                    "mimeType": "application/pdf",
                    "size": "999",
                }
            ],
        },
    )

    files = list(sync.walk_tho_folder(object(), "root"))

    assert len(files) == 1
    assert files[0].manufacturer_key == "cavco"
    assert files[0].safe_local_name == "Plan_A.pdf"
