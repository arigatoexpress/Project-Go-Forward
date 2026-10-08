"""DocuSeal document_url SSRF guards (L3).

Run: python -m pytest tests/test_docuseal_document_url.py -v
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.docuseal_document_url import is_safe_docuseal_document_url  # noqa: E402


def _public_resolver(_host, _port):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 0))]


def _private_resolver(_host, _port):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 0))]


def test_allowed_https_host_on_api_url():
    assert (
        is_safe_docuseal_document_url(
            "https://sign.example.com/docs/a.pdf",
            api_url="https://sign.example.com",
            resolver=_public_resolver,
        )
        is True
    )


def test_allowed_https_host_on_extra_list():
    assert (
        is_safe_docuseal_document_url(
            "https://cdn.example.net/signed.pdf",
            api_url="https://sign.example.com",
            extra_hosts="cdn.example.net",
            resolver=_public_resolver,
        )
        is True
    )


def test_foreign_host_rejected():
    assert (
        is_safe_docuseal_document_url(
            "https://evil.example/steal",
            api_url="https://sign.example.com",
            resolver=_public_resolver,
        )
        is False
    )


def test_http_scheme_rejected():
    assert (
        is_safe_docuseal_document_url(
            "http://sign.example.com/docs/a.pdf",
            api_url="https://sign.example.com",
            resolver=_public_resolver,
        )
        is False
    )


def test_private_resolved_ip_rejected():
    assert (
        is_safe_docuseal_document_url(
            "https://sign.example.com/docs/a.pdf",
            api_url="https://sign.example.com",
            resolver=_private_resolver,
        )
        is False
    )


def test_literal_loopback_ip_rejected_even_if_listed():
    assert (
        is_safe_docuseal_document_url(
            "https://127.0.0.1/docs/a.pdf",
            extra_hosts="127.0.0.1",
        )
        is False
    )
