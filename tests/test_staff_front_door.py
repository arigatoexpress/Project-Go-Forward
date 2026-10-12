"""Old staff URLs land on one sign-in page. Public pages stay put."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DOOR = "https://www.texashomeoutlet.com/staff"


def test_old_staff_entries_redirect_and_public_pages_do_not(monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_api_v1 import create_client

    client, _main, _db, _logger = create_client(monkeypatch)

    def location(path, host, method="GET"):
        response = client.request(
            method,
            path,
            headers={"host": host},
            follow_redirects=False,
        )
        return response.status_code, response.headers.get("location")

    assert location("/staff", "texashomeoutlet.com") == (308, DOOR)
    assert location("/staff/", "texashomeoutlet.com") == (308, DOOR)
    assert location("/admin", "texashomeoutlet.com") == (308, DOOR)
    assert location("/login", "www.texashomeoutlet.com") == (308, DOOR)
    assert location("/admin/login", "tho.sapphirealpha.xyz") == (308, DOOR)
    assert location("/staff", "tho.sapphirealpha.xyz", "HEAD") == (308, DOOR)

    status, target = location("/staff", "www.texashomeoutlet.com")
    assert status == 200
    assert target is None

    status, target = location("/inventory", "texashomeoutlet.com")
    assert status != 308
    assert target != DOOR

    status, target = location("/contact", "tho.sapphirealpha.xyz")
    assert status != 308
    assert target != DOOR

    status, target = location("/staff", "candidate---project-go-forward-example.a.run.app")
    assert status == 200
    assert target is None

    assert location("/admin", "candidate---project-go-forward-example.a.run.app") == (
        308,
        "https://candidate---project-go-forward-example.a.run.app/staff",
    )

    status, target = location("/inventory", "candidate---project-go-forward-example.a.run.app")
    assert status == 308
    assert target == "https://www.texashomeoutlet.com/inventory"

    status, target = location("/admin", "testserver", "POST")
    assert status != 308
    assert target != DOOR
