"""Email dependency must be VERIFIED, not merely present.

`/healthz/detailed` reported `"email": "configured"` whenever RESEND_API_KEY was
a non-empty string. On 2026-08-31 the key was present AND rejected by Resend
("API key is invalid"), so health stayed green while every transactional email
was dead — including the admin email sign-in code, the documented fallback for
staff who cannot use the shared PIN. Staff were asking for access at exactly
that moment.

"Set" is not "works". These tests pin the difference.
"""

import email_service


class TestEmailLiveness:
    def test_missing_key_reports_not_configured(self):
        status = email_service.check_email_liveness(api_key="", verify=lambda key: True)
        assert status["state"] == "not_configured"
        assert status["ok"] is False

    def test_present_but_rejected_key_is_not_healthy(self):
        # The exact production state: a key that exists and does not work.
        status = email_service.check_email_liveness(
            api_key="re_dead", verify=lambda key: False
        )
        assert status["state"] == "invalid_key"
        assert status["ok"] is False

    def test_working_key_is_healthy(self):
        status = email_service.check_email_liveness(
            api_key="re_live", verify=lambda key: True
        )
        assert status["state"] == "ok"
        assert status["ok"] is True

    def test_verifier_error_is_reported_as_unknown_not_healthy(self):
        # A network blip must not be reported as healthy, and must not be
        # reported as a bad key either — those need different responses.
        def boom(key):
            raise OSError("connection reset")

        status = email_service.check_email_liveness(api_key="re_live", verify=boom)
        assert status["state"] == "unreachable"
        assert status["ok"] is False

    def test_never_leaks_the_key(self):
        status = email_service.check_email_liveness(
            api_key="re_super_secret_value", verify=lambda key: False
        )
        assert "re_super_secret_value" not in repr(status)
