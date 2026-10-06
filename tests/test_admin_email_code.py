"""Tests for the EMAIL one-time-code admin login fallback.

This is a security-sensitive auth path. The OTP login must:
  * mint the EXACT same admin session as /api/admin/verify (PIN), so
    /api/admin/check and require_admin honor it;
  * share ONE allowlist with passkeys (auth.routes._passkey_email_allowed);
  * never enumerate accounts (the /request endpoint always returns 200);
  * be single-use, time-limited, attempt-limited, and IP-lockout protected.

The pure store / code-generation logic is tested directly against
``auth.email_code``. The HTTP surface is exercised through the existing
``test_api_v1.load_app`` harness (which stubs main.py's heavy eager imports).

Run: python -m pytest tests/test_admin_email_code.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Force the in-memory OTP store + a known allowlist BEFORE main.py / auth modules
# are imported anywhere. These mirror what an operator would set for a local run.
import os  # noqa: E402

os.environ.setdefault("THO_EMAIL_CODE_ALLOW_MEMORY_STORE", "1")
# Force the in-memory backend so the test never reaches Firestore (conftest
# points FIRESTORE_EMULATOR_HOST at a non-running emulator, which would hang).
os.environ.setdefault("THO_EMAIL_CODE_STORE", "memory")
os.environ.setdefault("THO_STAFF_DIRECTORY_STORE", "memory")
os.environ.setdefault("THO_PASSKEY_ALLOWED_DOMAINS", "texashomeoutlet.com")

ALLOWED_EMAIL = "staff@texashomeoutlet.com"
DISALLOWED_EMAIL = "stranger@gmail.com"


# ---------------------------------------------------------------------------
# Pure store + code generation
# ---------------------------------------------------------------------------


class TestCodeGeneration:
    def test_generate_code_is_six_digits(self):
        from auth.email_code import CODE_LENGTH, generate_code

        for _ in range(200):
            code = generate_code()
            assert isinstance(code, str)
            assert len(code) == CODE_LENGTH == 6
            assert code.isdigit()

    def test_generate_code_zero_padded(self):
        """A small random value must still be a full 6-char zero-padded string."""
        from auth import email_code

        # Force secrets.randbelow to return a tiny number so we exercise padding.
        original = email_code.secrets.randbelow
        try:
            email_code.secrets.randbelow = lambda _n: 5
            assert email_code.generate_code() == "000005"
        finally:
            email_code.secrets.randbelow = original

    def test_hash_code_is_stable_and_hex(self):
        from auth.email_code import hash_code

        h1 = hash_code("123456")
        h2 = hash_code("123456")
        assert h1 == h2
        assert h1 != hash_code("654321")
        assert len(h1) == 64  # sha256 hex
        int(h1, 16)  # parses as hex


class TestInMemoryStore:
    def _store(self):
        from auth.email_code import InMemoryEmailLoginCodeStore

        return InMemoryEmailLoginCodeStore()

    def test_put_get_roundtrip(self):
        from auth.email_code import hash_code

        store = self._store()
        expires = time.time() + 600
        store.put(ALLOWED_EMAIL, hash_code("123456"), expires)
        rec = store.get(ALLOWED_EMAIL)
        assert rec is not None
        assert rec.code_hash == hash_code("123456")
        assert rec.attempts == 0
        assert abs(rec.expires_at - expires) < 1

    def test_get_missing_returns_none(self):
        store = self._store()
        assert store.get("nobody@texashomeoutlet.com") is None

    def test_get_expired_returns_none(self):
        from auth.email_code import hash_code

        store = self._store()
        store.put(ALLOWED_EMAIL, hash_code("123456"), time.time() - 1)
        assert store.get(ALLOWED_EMAIL) is None

    def test_increment_attempts(self):
        from auth.email_code import hash_code

        store = self._store()
        store.put(ALLOWED_EMAIL, hash_code("123456"), time.time() + 600)
        assert store.increment_attempts(ALLOWED_EMAIL) == 1
        assert store.increment_attempts(ALLOWED_EMAIL) == 2
        assert store.get(ALLOWED_EMAIL).attempts == 2

    def test_delete(self):
        from auth.email_code import hash_code

        store = self._store()
        store.put(ALLOWED_EMAIL, hash_code("123456"), time.time() + 600)
        store.delete(ALLOWED_EMAIL)
        assert store.get(ALLOWED_EMAIL) is None

    def test_put_overwrites_previous_record(self):
        from auth.email_code import hash_code

        store = self._store()
        store.put(ALLOWED_EMAIL, hash_code("111111"), time.time() + 600)
        store.increment_attempts(ALLOWED_EMAIL)
        # A fresh request overwrites the record and resets attempts.
        store.put(ALLOWED_EMAIL, hash_code("222222"), time.time() + 600)
        rec = store.get(ALLOWED_EMAIL)
        assert rec.code_hash == hash_code("222222")
        assert rec.attempts == 0

    def test_records_keyed_by_normalized_email(self):
        from auth.email_code import hash_code

        store = self._store()
        store.put("Staff@TexasHomeOutlet.com", hash_code("123456"), time.time() + 600)
        # Lookup with different casing / whitespace resolves the same record.
        assert store.get("  staff@texashomeoutlet.com ") is not None


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture
def email_client(monkeypatch):
    """Bring up the app via the shared harness with the OTP store forced to memory."""
    monkeypatch.setenv("THO_EMAIL_CODE_ALLOW_MEMORY_STORE", "1")
    monkeypatch.setenv("THO_EMAIL_CODE_STORE", "memory")
    monkeypatch.setenv("THO_STAFF_DIRECTORY_STORE", "memory")
    monkeypatch.setenv("THO_PASSKEY_ALLOWED_DOMAINS", "texashomeoutlet.com")
    monkeypatch.setenv("THO_PASSKEY_OWNER_EMAILS", "owner@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "test-not-a-real-key")

    sys.path.insert(0, str(Path(__file__).parent))
    from test_api_v1 import create_client  # noqa: E402

    # Neutralize the audit-log Firestore client. Under the test harness no
    # emulator is running, and conftest points FIRESTORE_EMULATOR_HOST at a
    # dead host — an unmocked .add() would hang on a gRPC connect retry. The
    # audit write is best-effort (swallowed on failure) in production, so
    # returning None here exercises the real "no DB" branch safely.
    import audit_log

    monkeypatch.setattr(audit_log, "_get_db", lambda: None)

    client, main, _db, _logger = create_client(monkeypatch)

    # Reset the lru_cache-backed default store + any in-memory rows between tests
    # so attempt counters and stored codes don't leak across cases.
    from auth import email_code

    email_code.default_code_store.cache_clear()
    from auth import staff_directory

    staff_directory.default_directory.cache_clear()
    return client, main, email_code


def _last_sent(captured):
    assert captured, "send_admin_login_code was never called"
    return captured[-1]


class TestRequestEndpoint:
    def test_allowed_email_stores_code_and_sends(self, email_client, monkeypatch):
        client, main, email_code = email_client
        captured: list[dict] = []

        def _fake_send(to, code, **kwargs):
            captured.append({"to": to, "code": code, "kwargs": kwargs})
            return {"success": True}

        # Patch the symbol main.py actually calls.
        monkeypatch.setattr(main, "send_admin_login_code", _fake_send, raising=False)

        res = client.post("/api/admin/email-code/request", json={"email": ALLOWED_EMAIL})
        assert res.status_code == 200
        assert res.json() == {"success": True}

        sent = _last_sent(captured)
        assert sent["to"] == ALLOWED_EMAIL
        assert isinstance(sent["code"], str) and sent["code"].isdigit() and len(sent["code"]) == 6
        link = sent["kwargs"]["link"]
        assert link.startswith("https://www.texashomeoutlet.com/staff#t=")
        assert sent["code"] not in link

        # Code and link token must be stored only as hashes.
        store = email_code.default_code_store()
        rec = store.get(ALLOWED_EMAIL)
        assert rec is not None
        assert rec.code_hash == email_code.hash_code(sent["code"])
        assert rec.code_hash != sent["code"]
        token = link.split("#t=", 1)[1].split("&", 1)[0]
        assert rec.link_hash == email_code.hash_code(token)
        assert token not in rec.link_hash

    def test_disallowed_email_is_generic_no_store_no_send(self, email_client, monkeypatch):
        client, main, email_code = email_client
        captured: list[dict] = []
        monkeypatch.setattr(
            main,
            "send_admin_login_code",
            lambda *a, **k: captured.append((a, k)) or {"success": True},
            raising=False,
        )

        res = client.post("/api/admin/email-code/request", json={"email": DISALLOWED_EMAIL})
        # Generic success — NO account enumeration.
        assert res.status_code == 200
        assert res.json() == {"success": True}
        # Nothing sent, nothing stored.
        assert captured == []
        assert email_code.default_code_store().get(DISALLOWED_EMAIL) is None

    def test_request_response_never_contains_code(self, email_client, monkeypatch):
        client, main, email_code = email_client
        monkeypatch.setattr(
            main, "send_admin_login_code", lambda *a, **k: {"success": True}, raising=False
        )
        res = client.post("/api/admin/email-code/request", json={"email": ALLOWED_EMAIL})
        body = res.json()
        assert "code" not in body
        assert set(body.keys()) == {"success"}


class TestVerifyEndpoint:
    def _request_code(self, client, main, email_code, monkeypatch, email=ALLOWED_EMAIL):
        holder: dict = {}
        monkeypatch.setattr(
            main,
            "send_admin_login_code",
            lambda to, code, **k: holder.update(code=code) or {"success": True},
            raising=False,
        )
        res = client.post("/api/admin/email-code/request", json={"email": email})
        assert res.status_code == 200
        return holder["code"]

    def test_happy_path_mints_session_and_authenticates(self, email_client, monkeypatch):
        client, main, email_code = email_client
        code = self._request_code(client, main, email_code, monkeypatch)

        res = client.post(
            "/api/admin/email-code/verify", json={"email": ALLOWED_EMAIL, "code": code}
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["success"] is True
        assert "csrf_token" in body and body["csrf_token"]

        # The exact PIN-parity cookies are set.
        assert "tho_admin_token" in res.cookies
        assert "tho_csrf_token" in res.cookies

        # And that session is honored by /api/admin/check (cookie auto-sent by TestClient).
        check = client.get("/api/admin/check")
        assert check.status_code == 200
        assert check.json() == {"valid": True}

    def test_single_use_second_verify_fails(self, email_client, monkeypatch):
        client, main, email_code = email_client
        code = self._request_code(client, main, email_code, monkeypatch)

        first = client.post(
            "/api/admin/email-code/verify", json={"email": ALLOWED_EMAIL, "code": code}
        )
        assert first.status_code == 200
        # Reusing the same code must fail — code is consumed on success.
        second = client.post(
            "/api/admin/email-code/verify", json={"email": ALLOWED_EMAIL, "code": code}
        )
        assert second.status_code == 401
        assert second.json()["success"] is False

    def test_wrong_code_401_and_increments_attempts(self, email_client, monkeypatch):
        client, main, email_code = email_client
        self._request_code(client, main, email_code, monkeypatch)

        res = client.post(
            "/api/admin/email-code/verify", json={"email": ALLOWED_EMAIL, "code": "000000"}
        )
        assert res.status_code == 401
        # Attempt counter on the stored record increments.
        rec = email_code.default_code_store().get(ALLOWED_EMAIL)
        assert rec is not None
        assert rec.attempts >= 1

    def test_too_many_attempts_deletes_code(self, email_client, monkeypatch):
        client, main, email_code = email_client
        code = self._request_code(client, main, email_code, monkeypatch)

        # Pre-seed the per-code attempt counter right at the cap so a single
        # HTTP wrong-code call pushes it OVER the cap and burns the code. (The
        # slowapi 5/min cap on /verify prevents driving all the attempts via
        # HTTP, so we set the counter directly — the endpoint logic under test
        # is the "attempts > MAX_CODE_ATTEMPTS -> delete" branch.)
        store = email_code.default_code_store()
        rec = store._records[email_code._normalize_email(ALLOWED_EMAIL)]
        rec.attempts = email_code.MAX_CODE_ATTEMPTS

        res = client.post(
            "/api/admin/email-code/verify", json={"email": ALLOWED_EMAIL, "code": "999999"}
        )
        assert res.status_code == 401
        # Code is deleted once attempts exceed the per-code cap.
        assert store.get(ALLOWED_EMAIL) is None
        # Even the correct code now fails — record is gone.
        res2 = client.post(
            "/api/admin/email-code/verify", json={"email": ALLOWED_EMAIL, "code": code}
        )
        assert res2.status_code == 401

    def test_expired_code_401(self, email_client, monkeypatch):
        client, main, email_code = email_client
        code = self._request_code(client, main, email_code, monkeypatch)

        # Expire the stored record in place.
        store = email_code.default_code_store()
        rec = store._records[email_code._normalize_email(ALLOWED_EMAIL)]
        rec.expires_at = time.time() - 1

        res = client.post(
            "/api/admin/email-code/verify", json={"email": ALLOWED_EMAIL, "code": code}
        )
        assert res.status_code == 401
        assert res.json()["success"] is False

    def test_no_code_requested_401(self, email_client):
        client, main, email_code = email_client
        res = client.post(
            "/api/admin/email-code/verify",
            json={"email": "neverrequested@texashomeoutlet.com", "code": "123456"},
        )
        assert res.status_code == 401
        assert res.json()["success"] is False

    def test_ip_lockout_returns_429(self, email_client, monkeypatch):
        client, main, email_code = email_client
        # Prime the shared pin-attempts fallback for the TestClient IP so the
        # OTP verify endpoint hits the same IP lockout as /api/admin/verify.
        main._pin_attempts_fallback["testclient"] = [
            time.time() for _ in range(main.PIN_MAX_ATTEMPTS)
        ]
        res = client.post(
            "/api/admin/email-code/verify", json={"email": ALLOWED_EMAIL, "code": "123456"}
        )
        assert res.status_code == 429
        assert "Retry-After" in res.headers

    def test_pin_hash_unset_returns_503_and_does_not_burn_code(self, email_client, monkeypatch):
        """Misconfig (ADMIN_PIN_HASH unset) must fail closed BEFORE consuming the code."""
        client, main, email_code = email_client
        code = self._request_code(client, main, email_code, monkeypatch)
        monkeypatch.setattr(main, "ADMIN_PIN_HASH", "", raising=False)
        res = client.post(
            "/api/admin/email-code/verify", json={"email": ALLOWED_EMAIL, "code": code}
        )
        assert res.status_code == 503
        # The user's valid code must NOT be consumed by a server misconfiguration.
        assert email_code.default_code_store().get(ALLOWED_EMAIL) is not None
        assert "tho_admin_token" not in res.cookies

    def test_disallowed_email_with_seeded_record_is_rejected(self, email_client):
        """Defense in depth: /verify re-asserts the allowlist even if a record exists."""
        client, main, email_code = email_client
        store = email_code.default_code_store()
        store.put(DISALLOWED_EMAIL, email_code.hash_code("123456"), time.time() + 600)
        res = client.post(
            "/api/admin/email-code/verify",
            json={"email": DISALLOWED_EMAIL, "code": "123456"},
        )
        assert res.status_code == 401
        assert "tho_admin_token" not in res.cookies


class TestEmailSignInDelivery:
    def test_unconfigured_sender_is_503_for_every_address(self, email_client, monkeypatch):
        client, main, email_code = email_client
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        captured: list = []
        monkeypatch.setattr(
            main,
            "send_admin_login_code",
            lambda *a, **k: captured.append((a, k)) or {"success": True},
            raising=False,
        )

        allowed = client.post("/api/admin/email-code/request", json={"email": ALLOWED_EMAIL})
        stranger = client.post("/api/admin/email-code/request", json={"email": DISALLOWED_EMAIL})

        assert allowed.status_code == 503
        assert stranger.status_code == 503
        assert allowed.json() == stranger.json()
        assert "not turned on" in allowed.json()["error"]
        assert captured == []
        assert email_code.default_code_store().get(ALLOWED_EMAIL) is None

    def test_failed_send_does_not_leave_a_usable_code(self, email_client, monkeypatch):
        client, main, email_code = email_client
        monkeypatch.setattr(
            main,
            "send_admin_login_code",
            lambda *a, **k: {"success": False, "error": "provider down"},
            raising=False,
        )
        res = client.post("/api/admin/email-code/request", json={"email": ALLOWED_EMAIL})
        assert res.status_code == 200
        assert res.json() == {"success": True}
        assert email_code.default_code_store().get(ALLOWED_EMAIL) is None

    def test_link_token_signs_in_once(self, email_client, monkeypatch):
        client, main, email_code = email_client
        holder: dict = {}

        def _capture(to, code, **kwargs):
            holder["link"] = kwargs["link"]
            return {"success": True}

        monkeypatch.setattr(main, "send_admin_login_code", _capture, raising=False)
        res = client.post("/api/admin/email-code/request", json={"email": ALLOWED_EMAIL})
        assert res.status_code == 200
        fragment = holder["link"].split("#", 1)[1]
        token = fragment.split("&", 1)[0].removeprefix("t=")

        first = client.post(
            "/api/admin/email-code/verify",
            json={"email": ALLOWED_EMAIL, "link_token": token},
        )
        assert first.status_code == 200, first.text
        assert first.json()["success"] is True
        check = client.get("/api/admin/check")
        assert check.json() == {"valid": True}

        second = client.post(
            "/api/admin/email-code/verify",
            json={"email": ALLOWED_EMAIL, "link_token": token},
        )
        assert second.status_code == 401

    def test_blocked_staff_email_cannot_request_or_keep_a_session(self, email_client, monkeypatch):
        client, main, email_code = email_client
        sends: list[str] = []
        monkeypatch.setattr(
            main,
            "send_admin_login_code",
            lambda to, code, **k: sends.append(code) or {"success": True},
            raising=False,
        )
        requested = client.post("/api/admin/email-code/request", json={"email": ALLOWED_EMAIL})
        assert requested.status_code == 200
        verified = client.post(
            "/api/admin/email-code/verify",
            json={"email": ALLOWED_EMAIL, "code": sends[-1]},
        )
        assert verified.status_code == 200
        assert client.get("/api/admin/check").json() == {"valid": True}

        from auth.staff_directory import set_override

        set_override(ALLOWED_EMAIL, "blocked")
        blocked = client.get("/api/admin/check")
        assert blocked.json() == {"valid": False}
        cleared = " ".join(blocked.headers.get_list("set-cookie")).lower()
        assert "tho_admin_token=" in cleared
        assert "max-age=0" in cleared

        again = client.post("/api/admin/email-code/request", json={"email": ALLOWED_EMAIL})
        assert again.status_code == 200
        assert again.json() == {"success": True}
        assert len(sends) == 1
        assert email_code.default_code_store().get(ALLOWED_EMAIL) is None


def _csrf(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("tho_csrf_token")}


class TestStaffDirectoryApi:
    def _sign_in(self, email_client, monkeypatch):
        client, main, email_code = email_client
        holder: dict = {}
        monkeypatch.setattr(
            main,
            "send_admin_login_code",
            lambda to, code, **k: holder.update(code=code) or {"success": True},
            raising=False,
        )
        client.post("/api/admin/email-code/request", json={"email": ALLOWED_EMAIL})
        verified = client.post(
            "/api/admin/email-code/verify",
            json={"email": ALLOWED_EMAIL, "code": holder["code"]},
        )
        assert verified.status_code == 200, verified.text
        return client, main

    def test_admin_can_add_and_remove_a_teammate(self, email_client, monkeypatch):
        client, main = self._sign_in(email_client, monkeypatch)
        extra = "helper@example.com"

        denied = client.post(
            "/api/admin/email-code/request",
            json={"email": extra},
        )
        # Unknown address still looks successful and does not reveal the list.
        assert denied.status_code == 200

        added = client.post(
            "/api/admin/staff",
            json={"email": extra, "action": "allow"},
            headers=_csrf(client),
        )
        assert added.status_code == 200, added.text
        listed = client.get("/api/admin/staff")
        assert extra in listed.json()["added"]
        sent: list = []
        monkeypatch.setattr(
            main,
            "send_admin_login_code",
            lambda *args, **kwargs: sent.append(args) or {"success": True},
            raising=False,
        )
        follow = client.post("/api/admin/email-code/request", json={"email": extra})
        assert follow.status_code == 200
        assert sent and sent[0][0] == extra

        cleared = client.post(
            "/api/admin/staff",
            json={"email": extra, "action": "reset"},
            headers=_csrf(client),
        )
        assert cleared.status_code == 200
        assert extra not in client.get("/api/admin/staff").json()["added"]

    def test_owner_cannot_be_blocked(self, email_client, monkeypatch):
        client, _main = self._sign_in(email_client, monkeypatch)
        res = client.post(
            "/api/admin/staff",
            json={"email": "owner@example.com", "action": "block"},
            headers=_csrf(client),
        )
        assert res.status_code == 400
        assert "Owner" in res.json()["error"]

    def test_staff_changes_require_admin(self, email_client):
        client, _main, _email_code = email_client
        res = client.post(
            "/api/admin/staff",
            json={"email": "helper@example.com", "action": "allow"},
        )
        assert res.status_code == 401

    def test_sign_in_options_hide_the_team_list(self, email_client, monkeypatch):
        client, _main, _email_code = email_client
        ready = client.get("/api/admin/sign-in/options")
        assert ready.status_code == 200
        body = ready.json()
        assert body["email_ready"] is True
        assert body["sign_in_path"] == "/staff"
        assert body["passkey_sign_in"] is False
        assert "added" not in body
        assert "owners" not in body

    def test_passkey_sign_in_stays_off_unless_the_owner_flag_is_on(self, email_client, monkeypatch):
        client, _main, _email_code = email_client
        monkeypatch.delenv("FF_STAFF_PASSKEY_SIGN_IN", raising=False)
        assert client.get("/api/admin/sign-in/options").json()["passkey_sign_in"] is False
        monkeypatch.setenv("FF_STAFF_PASSKEY_SIGN_IN", "1")
        assert client.get("/api/admin/sign-in/options").json()["passkey_sign_in"] is True


def _sign_in_with_code(client, main, monkeypatch):
    holder: dict = {}
    monkeypatch.setattr(
        main,
        "send_admin_login_code",
        lambda to, code, **k: holder.update(code=code) or {"success": True},
        raising=False,
    )
    requested = client.post("/api/admin/email-code/request", json={"email": ALLOWED_EMAIL})
    assert requested.status_code == 200
    verified = client.post(
        "/api/admin/email-code/verify",
        json={"email": ALLOWED_EMAIL, "code": holder["code"]},
    )
    assert verified.status_code == 200, verified.text
    return verified


class TestStaffSessionLifetime:
    def test_email_sign_in_cookie_lasts_30_days(self, email_client, monkeypatch):
        client, main, _email_code = email_client
        monkeypatch.setattr(main, "IS_LOCAL", False)
        verified = _sign_in_with_code(client, main, monkeypatch)
        header = " ".join(verified.headers.get_list("set-cookie"))
        assert "Max-Age=2592000" in header
        assert "HttpOnly" in header
        assert "Secure" in header
        assert "SameSite=strict" in header
        expires, email = main._decode_admin_token(verified.cookies["tho_admin_token"])
        assert email == ALLOWED_EMAIL
        remaining = expires - time.time()
        assert 30 * 24 * 60 * 60 - 120 < remaining <= 30 * 24 * 60 * 60

    def test_session_slides_forward_after_it_has_been_used(self, email_client, monkeypatch):
        client, main, _email_code = email_client
        _sign_in_with_code(client, main, monkeypatch)
        fresh = client.get("/api/admin/check")
        assert fresh.json() == {"valid": True}
        assert fresh.headers.get_list("set-cookie") == []

        now = time.time()
        monkeypatch.setattr(main.time, "time", lambda: now - 2 * 60 * 60)
        old = main._create_admin_token(ALLOWED_EMAIL)
        old_expires = main._decode_admin_token(old)[0]
        monkeypatch.setattr(main.time, "time", lambda: now)
        client.cookies.set("tho_admin_token", old)
        slid = client.get("/api/admin/check")
        assert slid.json() == {"valid": True}
        new_expires = main._decode_admin_token(slid.cookies["tho_admin_token"])[0]
        assert new_expires > old_expires
        assert new_expires > now + 29 * 24 * 60 * 60

    def test_sign_out_ends_the_session_on_this_device(self, email_client, monkeypatch):
        client, main, _email_code = email_client
        _sign_in_with_code(client, main, monkeypatch)
        signed_out = client.post("/api/admin/logout")
        assert signed_out.status_code == 200
        assert client.get("/api/admin/check").json() == {"valid": False}

    def test_pin_sign_in_uses_the_same_30_day_cookie(self, email_client, monkeypatch):
        client, main, _email_code = email_client
        import hashlib

        pin = "4832"
        monkeypatch.setattr(main, "ADMIN_PIN_HASH", hashlib.sha256(pin.encode()).hexdigest())
        monkeypatch.setattr(main, "IS_LOCAL", False)
        response = client.post("/api/admin/verify", json={"pin": pin})
        assert response.status_code == 200, response.text
        header = " ".join(response.headers.get_list("set-cookie"))
        assert "Max-Age=2592000" in header
        assert "HttpOnly" in header
        assert "Secure" in header
        assert "SameSite=strict" in header
        expires, email = main._decode_admin_token(response.cookies["tho_admin_token"])
        assert email is None
        assert expires - time.time() > 29 * 24 * 60 * 60

        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        missing = client.get("/api/admin/sign-in/options")
        assert missing.json()["email_ready"] is False
