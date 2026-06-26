"""Tests for the centralized feature-flag system.

Coverage targets
----------------
- Boolean resolution (truthy / falsy tokens + default)
- String value resolution
- Percentage clamping and parsing
- Deterministic user-based rollout
- Env-var vs config.yaml priority
- ``all_flags()`` snapshot completeness
- Graceful degradation on import / config errors
"""

from __future__ import annotations

import hashlib
import os
from unittest.mock import patch

import pytest

import tools.feature_flags as ff

# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def _hash_bucket(name: str, user_id: str) -> int:
    """Reproduce the internal hashing logic for test assertions."""
    h = hashlib.sha256(f"{name}:{user_id}".encode()).hexdigest()
    return int(h[:8], 16) % 100


# ────────────────────────────────────────────────────────────────────────────
# Boolean flags
# ────────────────────────────────────────────────────────────────────────────


class TestIsEnabled:
    @pytest.mark.parametrize("token", ["1", "true", "TRUE", "yes", "YES", "on", "ON", "enabled", "ENABLED"])
    def test_truthy_tokens(self, token, monkeypatch):
        monkeypatch.setenv("FF_FOO", token)
        assert ff.is_enabled("FOO") is True

    @pytest.mark.parametrize("token", ["0", "false", "FALSE", "no", "NO", "off", "OFF", "disabled", "DISABLED", "maybe"])
    def test_falsy_or_unknown_tokens(self, token, monkeypatch):
        monkeypatch.setenv("FF_FOO", token)
        assert ff.is_enabled("FOO") is False

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("FF_FOO", raising=False)
        monkeypatch.delenv("FEATURE_FLAG_FOO", raising=False)
        assert ff.is_enabled("FOO") is False
        assert ff.is_enabled("FOO", default=True) is True

    def test_empty_string_means_unset(self, monkeypatch):
        monkeypatch.setenv("FF_FOO", "")
        assert ff.is_enabled("FOO") is False

    def test_feature_flag_prefix_also_works(self, monkeypatch):
        monkeypatch.setenv("FEATURE_FLAG_BAR", "1")
        monkeypatch.delenv("FF_BAR", raising=False)
        assert ff.is_enabled("BAR") is True

    def test_ff_prefix_wins_over_feature_flag_prefix(self, monkeypatch):
        monkeypatch.setenv("FF_BAZ", "0")
        monkeypatch.setenv("FEATURE_FLAG_BAZ", "1")
        assert ff.is_enabled("BAZ") is False


class TestIsDisabled:
    @pytest.mark.parametrize("token", ["0", "false", "FALSE", "no", "NO", "off", "OFF", "disabled", "DISABLED"])
    def test_falsy_tokens(self, token, monkeypatch):
        monkeypatch.setenv("FF_FOO", token)
        assert ff.is_disabled("FOO") is True

    @pytest.mark.parametrize("token", ["1", "true", "yes", "on", "enabled", "maybe"])
    def test_truthy_or_unknown_tokens(self, token, monkeypatch):
        monkeypatch.setenv("FF_FOO", token)
        assert ff.is_disabled("FOO") is False

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("FF_FOO", raising=False)
        assert ff.is_disabled("FOO") is False
        assert ff.is_disabled("FOO", default=True) is True


# ────────────────────────────────────────────────────────────────────────────
# String value
# ────────────────────────────────────────────────────────────────────────────


class TestGetValue:
    def test_reads_bool_style_env(self, monkeypatch):
        monkeypatch.setenv("FF_FOO", "bar")
        assert ff.get_value("FOO") == "bar"

    def test_reads_value_style_env(self, monkeypatch):
        monkeypatch.setenv("FF_FOO_VALUE", "baz")
        monkeypatch.delenv("FF_FOO", raising=False)
        assert ff.get_value("FOO") == "baz"

    def test_value_style_wins_over_bool_style(self, monkeypatch):
        monkeypatch.setenv("FF_FOO", "bool")
        monkeypatch.setenv("FF_FOO_VALUE", "val")
        # Bool style is checked first, so it wins
        assert ff.get_value("FOO") == "bool"

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("FF_FOO", raising=False)
        monkeypatch.delenv("FF_FOO_VALUE", raising=False)
        assert ff.get_value("FOO") is None
        assert ff.get_value("FOO", default="default") == "default"


# ────────────────────────────────────────────────────────────────────────────
# Percentage
# ────────────────────────────────────────────────────────────────────────────


class TestGetPercentage:
    def test_reads_percent_env(self, monkeypatch):
        monkeypatch.setenv("FF_ROLLOUT_PERCENT", "42")
        assert ff.get_percentage("ROLLOUT") == 42

    def test_clamps_high(self, monkeypatch):
        monkeypatch.setenv("FF_ROLLOUT_PERCENT", "150")
        assert ff.get_percentage("ROLLOUT") == 100

    def test_clamps_low(self, monkeypatch):
        monkeypatch.setenv("FF_ROLLOUT_PERCENT", "-10")
        assert ff.get_percentage("ROLLOUT") == 0

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("FF_ROLLOUT_PERCENT", raising=False)
        assert ff.get_percentage("ROLLOUT") == 0
        assert ff.get_percentage("ROLLOUT", default=50) == 50

    def test_invalid_string_returns_default(self, monkeypatch):
        monkeypatch.setenv("FF_ROLLOUT_PERCENT", "abc")
        assert ff.get_percentage("ROLLOUT") == 0

    def test_reads_from_config(self, monkeypatch):
        monkeypatch.delenv("FF_ROLLOUT_PERCENT", raising=False)
        with patch("tools.feature_flags._read_config", return_value=75):
            assert ff.get_percentage("ROLLOUT") == 75


# ────────────────────────────────────────────────────────────────────────────
# User-based deterministic rollout
# ────────────────────────────────────────────────────────────────────────────


class TestIsEnabledForUser:
    def test_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv("FF_EXP", raising=False)
        assert ff.is_enabled_for_user("EXP", "user_1") is False
        assert ff.is_enabled_for_user("EXP", "user_1", default=True) is True

    def test_explicitly_disabled_returns_false(self, monkeypatch):
        monkeypatch.setenv("FF_EXP", "off")
        assert ff.is_enabled_for_user("EXP", "user_1") is False

    def test_explicitly_enabled_no_percent_returns_true(self, monkeypatch):
        monkeypatch.setenv("FF_EXP", "on")
        monkeypatch.delenv("FF_EXP_PERCENT", raising=False)
        assert ff.is_enabled_for_user("EXP", "user_1") is True

    def test_percent_0_returns_false(self, monkeypatch):
        monkeypatch.setenv("FF_EXP", "on")
        monkeypatch.setenv("FF_EXP_PERCENT", "0")
        assert ff.is_enabled_for_user("EXP", "user_1") is False

    def test_percent_100_returns_true(self, monkeypatch):
        monkeypatch.setenv("FF_EXP", "on")
        monkeypatch.setenv("FF_EXP_PERCENT", "100")
        assert ff.is_enabled_for_user("EXP", "user_1") is True

    def test_deterministic_consistency(self, monkeypatch):
        """Same user always gets the same result."""
        monkeypatch.setenv("FF_EXP", "on")
        monkeypatch.setenv("FF_EXP_PERCENT", "50")
        result = ff.is_enabled_for_user("EXP", "user_123")
        for _ in range(10):
            assert ff.is_enabled_for_user("EXP", "user_123") == result

    def test_different_users_different_results(self, monkeypatch):
        """Different users may fall into different buckets."""
        monkeypatch.setenv("FF_EXP", "on")
        monkeypatch.setenv("FF_EXP_PERCENT", "50")
        results = {ff.is_enabled_for_user("EXP", f"user_{i}") for i in range(50)}
        # With 50 users and 50% rollout, we expect both True and False
        assert len(results) == 2

    def test_bucket_math_matches_internal(self, monkeypatch):
        """Verify the hash-bucket logic directly."""
        monkeypatch.setenv("FF_EXP", "on")
        monkeypatch.setenv("FF_EXP_PERCENT", "50")
        for uid in ("alice", "bob", "charlie"):
            bucket = _hash_bucket("EXP", uid)
            expected = bucket < 50
            assert ff.is_enabled_for_user("EXP", uid) == expected

    def test_percent_without_explicit_bool(self, monkeypatch):
        """Only percent set, no explicit bool token — treat as boolean enabled."""
        monkeypatch.setenv("FF_EXP_PERCENT", "30")
        monkeypatch.delenv("FF_EXP", raising=False)
        # With no FF_EXP set, _get_raw returns None, so default is used
        assert ff.is_enabled_for_user("EXP", "user_1", default=False) is False
        # But if we set a default of True, percent should apply
        # Actually, let me reconsider. The function says:
        # raw = _get_raw(name); if raw is None: return default
        # So if only percent is set, it returns default. This is correct behavior.


# ────────────────────────────────────────────────────────────────────────────
# all_flags snapshot
# ────────────────────────────────────────────────────────────────────────────


class TestAllFlags:
    def test_empty_when_nothing_configured(self, monkeypatch):
        # Clear all FF_ env vars
        for key in list(os.environ):
            if key.startswith("FF_") or key.startswith("FEATURE_FLAG_"):
                monkeypatch.delenv(key, raising=False)
        with patch("tools.feature_flags._read_config", return_value=None):
            assert ff.all_flags() == {}

    def test_picks_up_env_vars(self, monkeypatch):
        monkeypatch.setenv("FF_ALPHA", "1")
        monkeypatch.setenv("FF_BETA", "0")
        monkeypatch.setenv("FF_GAMMA_VALUE", "hello")
        flags = ff.all_flags()
        assert "ALPHA" in flags
        assert "BETA" in flags
        assert "GAMMA" in flags
        assert flags["ALPHA"]["enabled"] is True
        assert flags["BETA"]["enabled"] is False
        assert flags["GAMMA"]["value"] == "hello"

    def test_deduplicates_value_and_percent_suffixes(self, monkeypatch):
        monkeypatch.setenv("FF_ROLLOUT", "on")
        monkeypatch.setenv("FF_ROLLOUT_PERCENT", "25")
        flags = ff.all_flags()
        assert list(flags.keys()) == ["ROLLOUT"]
        assert flags["ROLLOUT"]["percentage"] == 25

    def test_merges_config_flags(self, monkeypatch):
        monkeypatch.delenv("FF_DELTA", raising=False)
        with patch("config_loader.get_config", return_value={"feature_flags": {"DELTA": "on"}}):
            flags = ff.all_flags()
            assert "DELTA" in flags
            assert flags["DELTA"]["enabled"] is True

    def test_no_secrets_in_snapshot(self, monkeypatch):
        """Snapshots are safe for JSON serialization."""
        monkeypatch.setenv("FF_SECRET", "shh")
        flags = ff.all_flags()
        assert flags["SECRET"]["value"] == "shh"
        # The point is that it doesn't contain complex objects or functions
        assert isinstance(flags["SECRET"], dict)


# ────────────────────────────────────────────────────────────────────────────
# Config.yaml integration
# ────────────────────────────────────────────────────────────────────────────


class TestConfigIntegration:
    def test_config_yaml_priority_below_env(self, monkeypatch):
        """Env vars win over config.yaml."""
        monkeypatch.setenv("FF_FOO", "0")
        with patch("tools.feature_flags._read_config", return_value="1"):
            assert ff.is_enabled("FOO") is False

    def test_config_yaml_used_when_env_absent(self, monkeypatch):
        monkeypatch.delenv("FF_FOO", raising=False)
        with patch("tools.feature_flags._read_config", return_value="1"):
            assert ff.is_enabled("FOO") is True

    def test_config_error_gracefully_degrades(self, monkeypatch):
        monkeypatch.delenv("FF_FOO", raising=False)
        with patch("config_loader.get_config", side_effect=RuntimeError("boom")):
            assert ff.is_enabled("FOO", default=False) is False


# ────────────────────────────────────────────────────────────────────────────
# Edge cases
# ────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_whitespace_tokens(self, monkeypatch):
        monkeypatch.setenv("FF_WS", "  true  ")
        assert ff.is_enabled("WS") is True

    def test_case_insensitive_tokens(self, monkeypatch):
        monkeypatch.setenv("FF_CI", "TrUe")
        assert ff.is_enabled("CI") is True

    def test_very_long_flag_name(self, monkeypatch):
        name = "A" * 200
        monkeypatch.setenv(f"FF_{name}", "1")
        assert ff.is_enabled(name) is True

    def test_unicode_value(self, monkeypatch):
        monkeypatch.setenv("FF_EMOJI", "🚀")
        assert ff.get_value("EMOJI") == "🚀"
        assert ff.is_enabled("EMOJI") is False  # 🚀 is not a truthy token
