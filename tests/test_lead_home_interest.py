"""Staff new-lead email says which home the customer is interested in.

Staff request (Lee, sales, 2026-10-09): a lead from a home page should show
the home (model, stock #, link) at the top of the staff email so sales can
prepare before calling; leads with no home say "General inquiry".
Synthetic data only; the email provider is faked.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_api_v1 import create_client

INVENTORY = {
    "homes": [
        {
            "id": "28102",
            "home_id": "28102",
            "stock_id": "28102",
            "model_name": "TRU Single Section Delight",
            "manufacturer": "TRU Homes",
            "inventory_kind": "available_now",
            "listing_url": "/homes/28102-tru-single-section-delight",
        },
        {
            "id": "floorplan-223034",
            "home_id": "floorplan-223034",
            "stock_id": "floorplan-223034",
            "model_name": "Skyliner / 4732B",
            "manufacturer": "Skyline Homes",
            "inventory_kind": "orderable_floorplan",
            "detail_url": "https://www.texashomeoutlet.com/plan/223034/skyliner/4732b/",
        },
        {
            "id": "select-legacy-s-2468",
            "stock_id": "select-legacy-s-2468",
            "model_name": "Select Legacy S-2468-42A",
            "manufacturer": "Pre-Owned",
            "inventory_kind": "pre_owned",
        },
    ]
}


def _client(monkeypatch, inventory=INVENTORY):
    monkeypatch.setenv("NOTION_LEAD_SYNC", "off")
    monkeypatch.setenv("RESEND_FROM", "Synthetic Sender <noreply@example.com>")
    monkeypatch.setenv("RESEND_API_KEY", "synthetic-provider-key")
    client, main, *_ = create_client(monkeypatch)
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "resend",
        types.SimpleNamespace(
            api_key="",
            Emails=types.SimpleNamespace(
                send=lambda payload: calls.append(payload) or {"id": "synthetic-id"}
            ),
        ),
    )
    path = Path(__file__).resolve().parents[1] / "email_service.py"
    spec = importlib.util.spec_from_file_location("lead_home_email_adapter", path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    monkeypatch.setattr(adapter, "NOTIFICATION_EMAILS", ["staff@example.com"])
    monkeypatch.setattr(adapter, "_log_email_activity", lambda *a, **k: None)
    monkeypatch.setattr(adapter, "send_lead_welcome", lambda **k: {"success": True})
    monkeypatch.setattr(main, "notify_new_lead", adapter.notify_new_lead)
    monkeypatch.setattr(main, "send_lead_welcome", lambda **k: {"success": True})
    if isinstance(inventory, Exception):

        def boom():
            raise inventory

        monkeypatch.setattr(main, "_resolve_public_inventory_context", boom)
    else:
        monkeypatch.setattr(main, "_resolve_public_inventory_context", lambda: inventory)
    return client, main, calls


def _staff_email(calls):
    staff = [c for c in calls if c.get("to") == ["staff@example.com"]]
    assert len(staff) == 1
    return staff[0]


def _post(client, **extra):
    body = {"name": "Synthetic Buyer", "phone": "5125550123", **extra}
    resp = client.post("/api/contact", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
    return resp.json()


def test_home_page_lead_puts_home_at_top_of_staff_email(monkeypatch):
    client, main, calls = _client(monkeypatch)
    _post(
        client,
        source="inventory_tour",
        home_id="28102",
        home_model="TRU Single Section Delight",
        home_stock="28102",
        home_url="/homes/28102-tru-single-section-delight",
    )
    msg = _staff_email(calls)
    url = "https://www.texashomeoutlet.com/homes/28102-tru-single-section-delight"
    first_line = msg["text"].splitlines()[0]
    assert first_line == (
        f"Interested in: TRU Homes TRU Single Section Delight — Stock #28102 — {url}"
    )
    html = msg["html"]
    assert html.index("Interested in:") < html.index("Name:")
    assert f'href="{url}"' in html
    assert "Stock #28102" in msg["subject"]

    lead = main.lead_manager.leads[-1]
    assert lead.home_id == "28102"
    assert lead.home_stock == "28102"
    assert lead.home_label == "TRU Homes TRU Single Section Delight"
    assert lead.home_url == url


def test_lead_without_a_home_says_general_inquiry(monkeypatch):
    client, main, calls = _client(monkeypatch)
    _post(client, message="Do you deliver to Conroe?")
    msg = _staff_email(calls)
    assert msg["text"].splitlines()[0] == "General inquiry"
    assert "General inquiry" in msg["html"]
    assert "Interested in:" not in msg["html"]
    assert msg["subject"].endswith("— General inquiry")
    lead = main.lead_manager.leads[-1]
    assert lead.home_stock is None and lead.home_url is None


def test_build_to_order_plan_shows_plan_number_and_plan_link(monkeypatch):
    client, _, calls = _client(monkeypatch)
    _post(client, source="inventory_quote", home_id="floorplan-223034", home_model="Skyliner / 4732B")
    first = _staff_email(calls)["text"].splitlines()[0]
    assert first == (
        "Interested in: Skyline Homes Skyliner / 4732B — Plan #223034 (build-to-order) — "
        "https://www.texashomeoutlet.com/plan/223034/skyliner/4732b/"
    )


def test_home_without_public_page_shows_id_without_link(monkeypatch):
    client, _, calls = _client(monkeypatch)
    _post(client, home_id="select-legacy-s-2468", home_model="Select Legacy S-2468-42A")
    msg = _staff_email(calls)
    # "Pre-Owned" is a placeholder manufacturer, so it is not prefixed.
    assert msg["text"].splitlines()[0] == (
        "Interested in: Select Legacy S-2468-42A — Inventory ID: select-legacy-s-2468"
    )
    assert "View listing" not in msg["html"]


def test_home_no_longer_listed_falls_back_to_form_values(monkeypatch):
    client, _, calls = _client(monkeypatch, inventory={"homes": []})
    _post(
        client,
        home_id="43945",
        home_model="Sold Model 3/2",
        home_url="/homes/43945-sold-model-3-2",
    )
    lines = _staff_email(calls)["text"].splitlines()
    assert lines[0] == (
        "Interested in: Sold Model 3/2 — Stock #43945 — "
        "https://www.texashomeoutlet.com/homes/43945-sold-model-3-2"
    )
    assert "not found in current inventory" in lines[1]


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://evil.example/homes/1",
        "//evil.example/homes/1",
        "javascript:alert(1)",
        "/homes/../admin",
        "/staff",
    ],
)
def test_customer_supplied_link_never_points_off_site(monkeypatch, bad_url):
    client, _, calls = _client(monkeypatch, inventory={"homes": []})
    _post(client, home_id="43945", home_model="Model <b>X</b>", home_url=bad_url)
    msg = _staff_email(calls)
    assert "evil.example" not in msg["html"] and "evil.example" not in msg["text"]
    assert "javascript:" not in msg["html"]
    assert "View listing" not in msg["html"]
    # Customer-typed markup never reaches the staff email as HTML.
    assert "<b>X</b>" not in msg["html"]
    assert "Interested in:</strong> Model" in msg["html"]


def test_interest_html_escapes_text():
    import email_service

    html = email_service._lead_home_interest_html(
        {"label": "Model <b>X</b>", "stock": "Stock #1", "url": None, "note": None}
    )
    assert "<b>X</b>" not in html
    assert "Model &lt;b&gt;X&lt;/b&gt;" in html


def test_inventory_outage_never_blocks_the_lead(monkeypatch):
    client, main, calls = _client(monkeypatch, inventory=RuntimeError("synthetic outage"))
    body = _post(client, home_id="28102", home_model="TRU Single Section Delight")
    assert body["lead_id"] == main.lead_manager.leads[-1].lead_id
    first = _staff_email(calls)["text"].splitlines()[0]
    assert first == "Interested in: TRU Single Section Delight — Stock #28102"
