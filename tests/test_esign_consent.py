"""Tests for the ESIGN consumer-consent step (esign_consent.py) and its wiring
into docuseal_service.send_file_for_signature.

ESIGN (15 U.S.C. §7001(c)) requires the consumer be told, before consenting:
right to a paper copy, right to withdraw, scope, and hardware/software needs.
"""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import esign_consent

# ─── Consent PDF generation ──────────────────────────────────────


def test_build_consent_pdf_bytes_is_a_pdf():
    pdf = esign_consent.build_consent_pdf_bytes()
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 800  # non-trivial single page


def test_build_consent_document_shape():
    doc = esign_consent.build_consent_document()
    assert doc["name"] == esign_consent.CONSENT_DOCUMENT_NAME
    # file is valid base64 decoding back to a PDF
    decoded = base64.b64decode(doc["file"])
    assert decoded.startswith(b"%PDF")


def test_consent_covers_required_esign_elements():
    """All four ESIGN §7001(c) disclosures must be present in the consent copy."""
    blob = " ".join(h + " " + b for h, b in esign_consent.CONSENT_SECTIONS).lower()
    assert "paper copy" in blob  # right to paper
    assert "withdraw" in blob  # right to withdraw consent
    assert "hardware and software" in blob or "software you need" in blob  # system reqs
    assert "scope" in blob  # scope of consent


# ─── Wiring into the signing flow ────────────────────────────────


def _mock_httpx_capture():
    """Return (patch_target, captured) where captured['json'] gets the payload."""
    captured = {}
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"id": 1, "status": "pending"}

    async def fake_post(url, headers=None, json=None):
        captured["url"] = url
        captured["json"] = json
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, captured


def test_send_file_prepends_consent_document(tmp_path, monkeypatch):
    import docuseal_service

    monkeypatch.setattr(docuseal_service, "API_URL", "https://sign.example.com")
    monkeypatch.setattr(docuseal_service, "API_TOKEN", "tok")
    pdf = tmp_path / "packet.pdf"
    pdf.write_bytes(b"%PDF-1.7 packet")

    ctx, captured = _mock_httpx_capture()
    with patch("httpx.AsyncClient", return_value=ctx):
        out = asyncio.run(
            docuseal_service.send_file_for_signature(
                email="buyer@example.com",
                name="Jordan Brooks",
                file_path=str(pdf),
                display_name="Closing Packet",
            )
        )

    assert out["success"] is True
    docs = captured["json"]["documents"]
    # Consent page is FIRST, packet SECOND.
    assert len(docs) == 2
    assert docs[0]["name"] == esign_consent.CONSENT_DOCUMENT_NAME
    assert docs[1]["name"] == "Closing Packet"
    assert captured["json"]["metadata"]["esign_consent_included"] is True


def test_send_file_can_opt_out_of_consent(tmp_path, monkeypatch):
    import docuseal_service

    monkeypatch.setattr(docuseal_service, "API_URL", "https://sign.example.com")
    monkeypatch.setattr(docuseal_service, "API_TOKEN", "tok")
    pdf = tmp_path / "packet.pdf"
    pdf.write_bytes(b"%PDF-1.7 packet")

    ctx, captured = _mock_httpx_capture()
    with patch("httpx.AsyncClient", return_value=ctx):
        asyncio.run(
            docuseal_service.send_file_for_signature(
                email="buyer@example.com",
                name="Jordan Brooks",
                file_path=str(pdf),
                display_name="Closing Packet",
                include_esign_consent=False,
            )
        )

    docs = captured["json"]["documents"]
    assert len(docs) == 1
    assert docs[0]["name"] == "Closing Packet"
    assert captured["json"]["metadata"]["esign_consent_included"] is False
