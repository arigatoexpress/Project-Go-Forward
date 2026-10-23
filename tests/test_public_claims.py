"""Public pages state only recorded business facts.

Placeholder strings and unverified promises stay out of crawlable HTML and
JSON-LD. The same phone, address, and retailer license that already appear in
config and the footer stay.
"""

import html
import re
from pathlib import Path

import pytest

import seo_routes
from config_loader import business_legal_name, business_license, business_phone
from tests.test_seo_routes import _jsonld_blocks, seo_client


@pytest.fixture(autouse=True)
def isolated_seo_shell(monkeypatch, tmp_path):
    shell = tmp_path / "index.html"
    source = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
    shell.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(seo_routes, "_index_html_path", str(shell))
    monkeypatch.setattr(seo_routes, "_shell_cache", None)


PUBLIC_PATHS = [
    "/",
    "/inventory",
    "/homes/44490-pre-owned-big-blue",
    "/financing",
    "/contact",
    "/about",
    "/privacy",
    "/terms",
    "/warranty",
    "/delivery",
    "/faq",
    "/manufactured-homes-in-humble-tx",
]

# Phrases the owner has not put on record. "guaranteed rates" in the terms
# disclaimer is a denial, so the ban is "guaranteed approval", not "guaranteed".
FORBIDDEN = [
    r"TODO\(owner\)",
    r"\[THO:",
    r"\[\[OWNER",
    r"21st Mortgage",
    r"Vanderbilt",
    r"\bTriad\b",
    r"free delivery",
    r"guaranteed approval",
    r"30-day",
    r"1-year",
    r"1 year from",
    r"30 days from",
    r"family-focused",
    r"meets or exceeds",
    r"within weeks",
    r"delivers manufactured",
    r"Inventory changes daily",
    r"281-657-7366",
    r"\(281\) 555-0199",
    r"Local Home Services",
    r"award-winning",
    r"5-star",
    r"\bFHA\b",
    r"\bUSDA\b",
    r"\bChattel\b",
    r"dream home",
    r"\bTBD\b",
    r"Apply for financing",
    r"modular home retailer",
    r"budget-friendly",
    r"lender partners",
]


def _visible(page: str) -> str:
    without_scripts = re.sub(
        r"<script\b[^>]*>.*?</script>", " ", page, flags=re.IGNORECASE | re.DOTALL
    )
    without_styles = re.sub(
        r"<style\b[^>]*>.*?</style>", " ", without_scripts, flags=re.IGNORECASE | re.DOTALL
    )
    text = re.sub(r"<[^>]+>", " ", without_styles)
    return html.unescape(re.sub(r"\s+", " ", text))


def _json_text(blocks) -> str:
    parts = []

    def walk(node):
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for block in blocks:
        walk(block)
    return "\n".join(parts)


def _assert_clean(label: str, text: str):
    for pattern in FORBIDDEN:
        assert not re.search(pattern, text, flags=re.IGNORECASE), (
            f"{label} still contains {pattern}"
        )


def test_recorded_license_matches_the_public_footer():
    assert business_license() == "35248"
    assert business_phone() == "(281) 324-3020"
    constants = Path("frontend/src/constants.js").read_text(encoding="utf-8")
    assert 'BUSINESS_LEGAL_NAME = "Prosperity Acquisitions, Inc. dba Texas Home Outlet"' in constants
    assert business_legal_name() == "Prosperity Acquisitions, Inc. dba Texas Home Outlet"


def test_public_html_and_jsonld_omit_unverified_claims(monkeypatch):
    client, _ = seo_client(monkeypatch)
    for path in PUBLIC_PATHS:
        page = client.get(path).text
        assert client.get(path).status_code == 200
        visible = _visible(page)
        structured = _json_text(_jsonld_blocks(page))
        _assert_clean(f"{path} visible copy", visible)
        _assert_clean(f"{path} JSON-LD", structured)
        assert "(281) 324-3020" in visible or "(281) 324-3020" in structured


def test_about_page_states_the_recorded_license(monkeypatch):
    client, _ = seo_client(monkeypatch)
    visible = _visible(client.get("/about").text)
    assert "License #35248" in visible
    assert "The legal name is Prosperity Acquisitions, Inc. dba Texas Home Outlet." in visible
    assert "10685 FM 1960 East" in visible
    assert "Ask our team about financing options." in visible
    assert "Delivery available, ask for details." in visible
    assert "Warranty coverage varies by manufacturer, ask a manager." in visible


def test_legal_pages_use_the_recorded_identity(monkeypatch):
    client, _ = seo_client(monkeypatch)
    for path in ("/privacy", "/terms"):
        visible = _visible(client.get(path).text)
        assert "manufactured and mobile home retailer" in visible
        assert "Prosperity Acquisitions, Inc. dba Texas Home Outlet" in visible
        assert "(281) 324-3020" in visible
        assert "10685 FM 1960 East, Huffman, TX 77336" in visible
        assert "\u2014" not in visible


def test_financing_warranty_and_delivery_meta_matches_visible_copy(monkeypatch):
    client, _ = seo_client(monkeypatch)
    expectations = {
        "/financing": "Ask our team about financing options.",
        "/warranty": "Warranty coverage varies by manufacturer, ask a manager.",
        "/delivery": "Delivery available, ask for details.",
        "/faq": "Ask our team about financing options.",
    }
    for path, phrase in expectations.items():
        page = client.get(path).text
        visible = _visible(page)
        description = re.search(r'<meta name="description" content="([^"]*)"', page).group(1)
        assert phrase in visible
        assert html.unescape(description) in visible
        assert "\u2014" not in visible
        if path == "/faq":
            assert phrase in _json_text(_jsonld_blocks(page))
        else:
            assert phrase in html.unescape(description)


def test_faq_jsonld_answers_are_the_visible_answers(monkeypatch):
    client, _ = seo_client(monkeypatch)
    page = client.get("/faq").text
    visible = _visible(page)
    faq = next(block for block in _jsonld_blocks(page) if block.get("@type") == "FAQPage")
    for question in faq["mainEntity"]:
        answer = question["acceptedAnswer"]["text"]
        assert answer in visible
        assert question["name"] in visible


def test_city_jsonld_description_matches_the_visible_sentence(monkeypatch):
    client, _ = seo_client(monkeypatch)
    path = "/manufactured-homes-in-humble-tx"
    page = client.get(path).text
    sentence = seo_routes._city_shopper_sentence("Humble")
    visible = _visible(page)
    assert sentence in visible
    assert "delivers manufactured" not in visible.lower()
    webpage = next(
        block for block in _jsonld_blocks(page) if block.get("@type") == "WebPage"
    )
    assert webpage["description"] == sentence
    service = next(block for block in _jsonld_blocks(page) if block.get("@type") == "Service")
    assert service["name"] == "Manufactured and mobile homes in Humble, TX"
    business = next(
        block for block in _jsonld_blocks(page) if block.get("@type") == "LocalBusiness"
    )
    assert business["knowsAbout"] == ["Manufactured homes", "Mobile homes"]
    assert business["telephone"] == "(281) 324-3020"


def test_home_detail_keeps_call_for_price(monkeypatch):
    client, _ = seo_client(monkeypatch)
    visible = _visible(client.get("/homes/44490-pre-owned-big-blue").text)
    assert "Call for Price" in visible
    assert "Ask about financing" in visible
    assert "Apply for financing" not in visible


def test_default_work_order_has_no_placeholder_contractor():
    from tools import document_tools

    result = document_tools.generate_work_order_pdf(
        customer_name="Jane Doe",
        customer_address="1 Main St",
        customer_phone="281-555-0100",
        work_description="Check the roof",
    )
    html_content = result["html_content"]
    assert "(281) 555-0199" not in html_content
    assert "Local Home Services" not in html_content
    assert "dispatch@localhomeservices.com" not in html_content
    assert "Family Owned" not in html_content
    assert "Veteran Owned" not in html_content
    assert "Since 2010" not in html_content
    assert result["next_steps"] == "Warranty coverage varies by manufacturer, ask a manager."


def test_customer_emails_do_not_state_unconfirmed_warranty_terms():
    from tools import document_tools

    for email_type in ("appointment_confirmation", "service_update", "welcome", "follow_up"):
        result = document_tools.generate_customer_email("Jane Doe", email_type)
        blob = f"{result['subject']}\n{result['body']}"
        assert "TBD" not in blob
        assert "dream home" not in blob.lower()
        assert "1 year" not in blob.lower()
        assert "30 day" not in blob.lower()
        assert "24-48" not in blob
        assert "(281) 324-3020" in blob


def test_customer_email_templates_omit_price_and_slogan_claims(monkeypatch):
    import email_service
    from tools.lead_nurture import render_nurture_email

    captured = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(email_service, "send_email", fake_send)
    email_service.send_lead_welcome("shopper@example.com", "Jane Doe")
    welcome = captured["html"]
    assert "Dream Home" not in welcome
    assert "under $50" not in welcome
    assert "over 40 homes" not in welcome
    assert "24/7" not in welcome
    assert "Call for Price" in welcome
    assert "(281) 324-3020" in welcome

    email_service.send_appointment_confirmation(
        "shopper@example.com", "Jane Doe", "2026-11-02", "10:00 AM"
    )
    visit = captured["html"]
    assert "Ben or Mark" not in visit
    assert "ask for our team" in visit

    _subject, nurture = render_nurture_email({"name": "Jane Doe", "days_inactive": 20})
    assert "under $50" not in nurture
    assert "Dream Home" not in nurture
    assert "Delivery available, ask for details." in nurture


def test_warranty_tool_does_not_confirm_coverage():
    from tools.service_tools import WARRANTY_CUSTOMER_MESSAGE, check_warranty_status

    result = check_warranty_status("2024-01-15", "Structural")
    assert result["success"] is True
    assert result["covered"] is None
    assert result["message"] == WARRANTY_CUSTOMER_MESSAGE
    assert "1 year" not in result["message"].lower()
    assert "30 day" not in result["message"].lower()
