"""Tests for tools/field_crypto.py — SSN envelope encryption.

Covers the two safety guarantees the feature must hold: it is a NO-OP until a
KMS key is configured, and decryption is backward-compatible with legacy
plaintext so a migration can run incrementally.
"""

from types import SimpleNamespace

import pytest

from tools import field_crypto


class _FakeKMS:
    """Reversible stand-in for the Cloud KMS client (XOR — test only)."""

    @staticmethod
    def _xor(b: bytes) -> bytes:
        return bytes(x ^ 0x5A for x in b)

    def encrypt(self, request):
        return SimpleNamespace(ciphertext=self._xor(request["plaintext"]))

    def decrypt(self, request):
        return SimpleNamespace(plaintext=self._xor(request["ciphertext"]))


@pytest.fixture
def kms_on(monkeypatch):
    monkeypatch.setenv("SSN_KMS_KEY", "projects/p/locations/l/keyRings/r/cryptoKeys/k")
    monkeypatch.setattr(field_crypto, "_client", lambda: _FakeKMS())


@pytest.fixture
def kms_off(monkeypatch):
    monkeypatch.delenv("SSN_KMS_KEY", raising=False)


# ─── Inert (no key configured) ───────────────────────────────────────────────

def test_inert_encrypt_is_passthrough(kms_off):
    assert field_crypto.encrypt_field("123-45-6789") == "123-45-6789"


def test_inert_decrypt_is_passthrough(kms_off):
    assert field_crypto.decrypt_field("123-45-6789") == "123-45-6789"


def test_inert_ssn_fields_unchanged(kms_off):
    data = {"buyer_ssn": "111-22-3333", "co_buyer_ssn": "444-55-6666", "buyer_name": "Jo"}
    assert field_crypto.encrypt_ssn_fields(dict(data)) == data


# ─── Active (key configured, KMS mocked) ─────────────────────────────────────

def test_encrypt_adds_prefix_and_changes_value(kms_on):
    out = field_crypto.encrypt_field("123-45-6789")
    assert out.startswith("kms1:")
    assert "123-45-6789" not in out


def test_roundtrip(kms_on):
    ct = field_crypto.encrypt_field("123-45-6789")
    assert field_crypto.decrypt_field(ct) == "123-45-6789"


def test_encrypt_is_idempotent(kms_on):
    once = field_crypto.encrypt_field("123-45-6789")
    twice = field_crypto.encrypt_field(once)
    assert once == twice  # already-encrypted value not re-encrypted


def test_decrypt_legacy_plaintext_passthrough(kms_on):
    # A value without the kms1: prefix is treated as legacy plaintext.
    assert field_crypto.decrypt_field("123-45-6789") == "123-45-6789"


def test_ssn_fields_roundtrip_and_leave_others(kms_on):
    data = {"buyer_ssn": "111-22-3333", "co_buyer_ssn": "444-55-6666", "buyer_name": "Jo"}
    enc = field_crypto.encrypt_ssn_fields(dict(data))
    assert enc["buyer_ssn"].startswith("kms1:")
    assert enc["co_buyer_ssn"].startswith("kms1:")
    assert enc["buyer_name"] == "Jo"
    dec = field_crypto.decrypt_ssn_fields(enc)
    assert dec["buyer_ssn"] == "111-22-3333"
    assert dec["co_buyer_ssn"] == "444-55-6666"


@pytest.mark.parametrize("empty", [None, "", 0])
def test_empty_values_untouched(kms_on, empty):
    assert field_crypto.encrypt_field(empty) == empty
    assert field_crypto.decrypt_field(empty) == empty


def test_mixed_plaintext_and_ciphertext_decrypt(kms_on):
    # During migration a deal may have one encrypted + one legacy plaintext SSN.
    data = {"buyer_ssn": field_crypto.encrypt_field("111-22-3333"), "co_buyer_ssn": "444-55-6666"}
    dec = field_crypto.decrypt_ssn_fields(dict(data))
    assert dec["buyer_ssn"] == "111-22-3333"
    assert dec["co_buyer_ssn"] == "444-55-6666"
