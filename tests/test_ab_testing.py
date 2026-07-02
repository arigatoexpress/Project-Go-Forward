"""Tests for the A/B testing framework.

Coverage targets:
- Experiment / Variant validation
- Deterministic assignment and bucketing
- Feature-flag gating
- Impression / conversion tracking
- Metrics computation (conversion rates, uplift)
- Statistical significance (two-proportion z-test)
- Redis persistence (mocked via caching module)
- Admin helpers (reset, snapshot)
"""

from __future__ import annotations

import pytest

from tools.ab_testing import (
    ABTestEngine,
    Experiment,
    Variant,
    _phi,
    _two_proportion_z_test,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine() -> ABTestEngine:
    return ABTestEngine()


def _make_experiment(
    id: str = "test_exp",
    status: str = "running",
    variants: list[Variant] | None = None,
) -> Experiment:
    if variants is None:
        variants = [
            Variant(name="control", weight=1.0),
            Variant(name="treatment", weight=1.0),
        ]
    return Experiment(
        id=id,
        name="Test Experiment",
        variants=variants,
        status=status,
    )


# ---------------------------------------------------------------------------
# Data model validation
# ---------------------------------------------------------------------------


class TestVariantValidation:
    def test_valid_variant(self) -> None:
        v = Variant(name="control", weight=0.5, config={"color": "blue"})
        assert v.name == "control"
        assert v.weight == 0.5
        assert v.config == {"color": "blue"}

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            Variant(name="", weight=1.0)

    def test_negative_weight_raises(self) -> None:
        with pytest.raises(ValueError, match="weight must be >= 0"):
            Variant(name="a", weight=-1.0)

    def test_invalid_config_raises(self) -> None:
        with pytest.raises(ValueError, match="config must be a dict"):
            Variant(name="a", weight=1.0, config="bad")

    def test_default_config(self) -> None:
        v = Variant(name="a")
        assert v.config == {}

    def test_zero_weight_allowed(self) -> None:
        v = Variant(name="a", weight=0.0)
        assert v.weight == 0.0


class TestExperimentValidation:
    def test_valid_experiment(self) -> None:
        e = _make_experiment()
        assert e.id == "test_exp"
        assert e.control_variant.name == "control"

    def test_empty_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            Experiment(id="", name="X", variants=[Variant(name="a")])

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            Experiment(id="x", name="", variants=[Variant(name="a")])

    def test_no_variants_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one variant"):
            Experiment(id="x", name="X", variants=[])

    def test_duplicate_variant_names_raises(self) -> None:
        with pytest.raises(ValueError, match="Duplicate variant name"):
            Experiment(
                id="x",
                name="X",
                variants=[Variant(name="a"), Variant(name="a")],
            )

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid experiment status"):
            Experiment(
                id="x",
                name="X",
                variants=[Variant(name="a")],
                status="banana",
            )

    def test_all_statuses_accepted(self) -> None:
        for status in ("draft", "running", "paused", "ended"):
            e = Experiment(
                id="x",
                name="X",
                variants=[Variant(name="a")],
                status=status,
            )
            assert e.status == status

    def test_total_weight(self) -> None:
        e = _make_experiment(variants=[Variant(name="a", weight=1), Variant(name="b", weight=3)])
        assert e.total_weight == 4.0

    def test_total_weight_zero(self) -> None:
        e = Experiment(
            id="x",
            name="X",
            variants=[Variant(name="a", weight=0), Variant(name="b", weight=0)],
        )
        assert e.total_weight == 0.0

    def test_bucket_mapping_simple(self) -> None:
        e = Experiment(
            id="x",
            name="X",
            variants=[Variant(name="a", weight=1), Variant(name="b", weight=1)],
        )
        # 50/50 split: buckets 0-49 -> a, 50-99 -> b
        assert e.get_variant_by_bucket(0).name == "a"
        assert e.get_variant_by_bucket(49).name == "a"
        assert e.get_variant_by_bucket(50).name == "b"
        assert e.get_variant_by_bucket(99).name == "b"

    def test_bucket_mapping_uneven(self) -> None:
        e = Experiment(
            id="x",
            name="X",
            variants=[
                Variant(name="a", weight=1),
                Variant(name="b", weight=2),
                Variant(name="c", weight=1),
            ],
        )
        # Weights: 1/4, 2/4, 1/4 -> a: 0-24, b: 25-74, c: 75-99
        assert e.get_variant_by_bucket(0).name == "a"
        assert e.get_variant_by_bucket(24).name == "a"
        assert e.get_variant_by_bucket(25).name == "b"
        assert e.get_variant_by_bucket(74).name == "b"
        assert e.get_variant_by_bucket(75).name == "c"
        assert e.get_variant_by_bucket(99).name == "c"

    def test_bucket_mapping_zero_total_weight_falls_back_to_control(self) -> None:
        e = Experiment(
            id="x",
            name="X",
            variants=[Variant(name="a", weight=0), Variant(name="b", weight=0)],
        )
        assert e.get_variant_by_bucket(50).name == "a"  # control fallback

    def test_bucket_mapping_edge_last_bucket(self) -> None:
        e = Experiment(
            id="x",
            name="X",
            variants=[Variant(name="a", weight=1)],
        )
        assert e.get_variant_by_bucket(99).name == "a"


# ---------------------------------------------------------------------------
# Engine: registration
# ---------------------------------------------------------------------------


class TestEngineRegistration:
    def test_register_and_get(self) -> None:
        engine = _make_engine()
        exp = _make_experiment()
        engine.register_experiment(exp)
        assert engine.get_experiment("test_exp") == exp

    def test_list_experiments(self) -> None:
        engine = _make_engine()
        e1 = _make_experiment(id="e1")
        e2 = _make_experiment(id="e2")
        engine.register_experiment(e1)
        engine.register_experiment(e2)
        assert {e.id for e in engine.list_experiments()} == {"e1", "e2"}

    def test_overwrite(self) -> None:
        engine = _make_engine()
        e1 = _make_experiment(id="e1")
        e2 = Experiment(
            id="e1",
            name="Updated",
            variants=[Variant(name="v")],
        )
        engine.register_experiment(e1)
        engine.register_experiment(e2)
        assert engine.get_experiment("e1").name == "Updated"

    def test_get_missing_returns_none(self) -> None:
        engine = _make_engine()
        assert engine.get_experiment("missing") is None


# ---------------------------------------------------------------------------
# Engine: assignment
# ---------------------------------------------------------------------------


class TestAssignment:
    def test_assignment_deterministic(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        v1 = engine.get_variant("user-42", "test_exp")
        v2 = engine.get_variant("user-42", "test_exp")
        assert v1 is not None
        assert v1.name == v2.name

    def test_assignment_different_users_different_variants(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        # With a 50/50 split, it's statistically likely that two different
        # users get different variants, but we can't guarantee it.  Instead,
        # sample 100 users and assert both variants appear.
        variants = {engine.get_variant(f"user-{i}", "test_exp").name for i in range(200)}
        assert variants == {"control", "treatment"}

    def test_assignment_distribution_roughly_even(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        counts: dict[str, int] = {}
        for i in range(1000):
            v = engine.get_variant(f"user-{i}", "test_exp")
            counts[v.name] = counts.get(v.name, 0) + 1
        # 50/50 split should be within 10% of 500
        assert 400 < counts["control"] < 600
        assert 400 < counts["treatment"] < 600

    def test_assignment_uneven_weights(self) -> None:
        engine = _make_engine()
        engine.register_experiment(
            Experiment(
                id="uneven",
                name="Uneven",
                variants=[
                    Variant(name="a", weight=1),
                    Variant(name="b", weight=3),
                ],
            )
        )
        counts: dict[str, int] = {}
        for i in range(1000):
            v = engine.get_variant(f"user-{i}", "uneven")
            counts[v.name] = counts.get(v.name, 0) + 1
        # 25/75 split should be roughly 250/750
        assert 150 < counts["a"] < 350
        assert 650 < counts["b"] < 850

    def test_assignment_returns_none_when_not_registered(self) -> None:
        engine = _make_engine()
        assert engine.get_variant("user-1", "missing") is None

    def test_assignment_returns_none_when_not_running(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(status="paused"))
        assert engine.get_variant("user-1", "test_exp") is None

        engine.register_experiment(_make_experiment(status="draft"))
        assert engine.get_variant("user-1", "test_exp") is None

        engine.register_experiment(_make_experiment(status="ended"))
        assert engine.get_variant("user-1", "test_exp") is None

    def test_assignment_returns_variant_when_running(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(status="running"))
        v = engine.get_variant("user-1", "test_exp")
        assert v is not None
        assert v.name in {"control", "treatment"}

    def test_assignment_disabled_by_feature_flag(self, monkeypatch) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(id="hero_cta"))
        monkeypatch.setenv("FF_AB_HERO_CTA", "false")
        assert engine.get_variant("user-1", "hero_cta") is None

    def test_assignment_enabled_by_feature_flag_true(self, monkeypatch) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(id="hero_cta"))
        monkeypatch.setenv("FF_AB_HERO_CTA", "true")
        assert engine.get_variant("user-1", "hero_cta") is not None

    def test_assignment_defaults_to_enabled_when_no_flag(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(id="hero_cta"))
        assert engine.get_variant("user-1", "hero_cta") is not None

    def test_assignment_with_variant_config(self) -> None:
        engine = _make_engine()
        engine.register_experiment(
            Experiment(
                id="config_test",
                name="Config Test",
                variants=[
                    Variant(name="control", weight=1, config={"color": "blue"}),
                    Variant(name="treatment", weight=1, config={"color": "green"}),
                ],
            )
        )
        v = engine.get_variant("user-1", "config_test")
        assert v is not None
        assert v.config in [{"color": "blue"}, {"color": "green"}]

    def test_get_assignment(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        a = engine.get_assignment("user-1", "test_exp")
        assert a is not None
        assert a.user_id == "user-1"
        assert a.experiment_id == "test_exp"
        assert a.variant_name in {"control", "treatment"}
        assert a.assigned_at > 0

    def test_get_assignment_none_when_not_running(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(status="ended"))
        assert engine.get_assignment("user-1", "test_exp") is None


# ---------------------------------------------------------------------------
# Engine: tracking
# ---------------------------------------------------------------------------


class TestTracking:
    def test_track_impression(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        engine.track_impression("user-1", "test_exp")
        results = engine.get_results("test_exp")
        assert results["control"]["impressions"] + results["treatment"]["impressions"] == 1

    def test_track_impression_idempotent(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        engine.track_impression("user-1", "test_exp")
        engine.track_impression("user-1", "test_exp")
        engine.track_impression("user-1", "test_exp")
        results = engine.get_results("test_exp")
        variant = engine.get_variant("user-1", "test_exp")
        assert results[variant.name]["impressions"] == 1

    def test_track_impression_different_users(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        for i in range(100):
            engine.track_impression(f"user-{i}", "test_exp")
        results = engine.get_results("test_exp")
        total = results["control"]["impressions"] + results["treatment"]["impressions"]
        assert total == 100

    def test_track_conversion(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        engine.track_conversion("user-1", "test_exp")
        results = engine.get_results("test_exp")
        variant = engine.get_variant("user-1", "test_exp")
        assert results[variant.name]["conversions"] == 1.0

    def test_track_conversion_with_value(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        engine.track_conversion("user-1", "test_exp", value=2.5)
        results = engine.get_results("test_exp")
        variant = engine.get_variant("user-1", "test_exp")
        assert results[variant.name]["conversions"] == 2.5

    def test_track_conversion_multiple_users(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        for i in range(100):
            engine.track_impression(f"user-{i}", "test_exp")
            if i % 2 == 0:
                engine.track_conversion(f"user-{i}", "test_exp")
        results = engine.get_results("test_exp")
        total_conv = results["control"]["conversions"] + results["treatment"]["conversions"]
        assert total_conv == 50.0

    def test_track_noop_when_not_running(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(status="ended"))
        engine.track_impression("user-1", "test_exp")
        engine.track_conversion("user-1", "test_exp")
        results = engine.get_results("test_exp")
        # get_results returns the experiment structure even with zero events
        assert results["control"]["impressions"] == 0
        assert results["treatment"]["impressions"] == 0
        assert results["p_value"] is None
        assert results["significant"] is False

    def test_track_noop_when_experiment_missing(self) -> None:
        engine = _make_engine()
        engine.track_impression("user-1", "missing")
        engine.track_conversion("user-1", "missing")
        # Should not raise

    def test_conversion_without_impression(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        engine.track_conversion("user-1", "test_exp")
        results = engine.get_results("test_exp")
        variant = engine.get_variant("user-1", "test_exp")
        assert results[variant.name]["conversions"] == 1.0
        assert results[variant.name]["impressions"] == 0.0


# ---------------------------------------------------------------------------
# Engine: metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_conversion_rate(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        # 100 users in control, 10 convert
        for i in range(100):
            engine.track_impression(f"ctrl-{i}", "test_exp")
            if engine.get_variant(f"ctrl-{i}", "test_exp").name == "control" and i < 10:
                engine.track_conversion(f"ctrl-{i}", "test_exp")
        # Add more treatment users to ensure both variants have data
        for i in range(100, 200):
            engine.track_impression(f"trt-{i}", "test_exp")
            if engine.get_variant(f"trt-{i}", "test_exp").name == "treatment" and i < 120:
                engine.track_conversion(f"trt-{i}", "test_exp")
        results = engine.get_results("test_exp")
        assert results["control"]["conversion_rate"] > 0
        assert results["treatment"]["conversion_rate"] > 0

    def test_zero_conversion_rate(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        engine.track_impression("user-1", "test_exp")
        results = engine.get_results("test_exp")
        variant = engine.get_variant("user-1", "test_exp")
        assert results[variant.name]["conversion_rate"] == 0.0

    def test_zero_impressions(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        results = engine.get_results("test_exp")
        assert results["control"]["conversion_rate"] == 0.0
        assert results["treatment"]["conversion_rate"] == 0.0

    def test_relative_uplift(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        # Build explicit control/treatment user pools so we can hit exact rates
        control_users = []
        treatment_users = []
        for i in range(1000):
            v = engine.get_variant(f"u-{i}", "test_exp")
            if v.name == "control":
                control_users.append(f"u-{i}")
            else:
                treatment_users.append(f"u-{i}")

        # Control: 100 impressions, 10 conversions (10%)
        for u in control_users[:100]:
            engine.track_impression(u, "test_exp")
        for u in control_users[:10]:
            engine.track_conversion(u, "test_exp")

        # Treatment: 100 impressions, 20 conversions (20%)
        for u in treatment_users[:100]:
            engine.track_impression(u, "test_exp")
        for u in treatment_users[:20]:
            engine.track_conversion(u, "test_exp")

        results = engine.get_results("test_exp")
        assert results["relative_uplift"] == 1.0  # 20% / 10% - 1 = 1.0 = 100% uplift

    def test_no_uplift_when_no_better_variant(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        # Control: 10 conversions, Treatment: 5 conversions
        control_users = []
        treatment_users = []
        for i in range(1000):
            v = engine.get_variant(f"u-{i}", "test_exp")
            if v.name == "control":
                control_users.append(f"u-{i}")
            else:
                treatment_users.append(f"u-{i}")

        for u in control_users[:100]:
            engine.track_impression(u, "test_exp")
        for u in control_users[:10]:
            engine.track_conversion(u, "test_exp")

        for u in treatment_users[:100]:
            engine.track_impression(u, "test_exp")
        for u in treatment_users[:5]:
            engine.track_conversion(u, "test_exp")

        results = engine.get_results("test_exp")
        # Treatment is worse than control — negative uplift reported
        assert results["relative_uplift"] == -0.5  # (5% - 10%) / 10% = -0.5

    def test_uplift_when_control_zero(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        # Control: 0 conversions, Treatment: 10 conversions
        control_users = []
        treatment_users = []
        for i in range(1000):
            v = engine.get_variant(f"u-{i}", "test_exp")
            if v.name == "control":
                control_users.append(f"u-{i}")
            else:
                treatment_users.append(f"u-{i}")

        for u in control_users[:100]:
            engine.track_impression(u, "test_exp")

        for u in treatment_users[:100]:
            engine.track_impression(u, "test_exp")
        for u in treatment_users[:10]:
            engine.track_conversion(u, "test_exp")

        results = engine.get_results("test_exp")
        # control_rate is 0, so relative uplift is None (division by zero avoided)
        assert results["relative_uplift"] is None

    def test_single_variant_no_uplift(self) -> None:
        engine = _make_engine()
        engine.register_experiment(
            Experiment(
                id="single",
                name="Single",
                variants=[Variant(name="only")],
            )
        )
        engine.track_impression("user-1", "single")
        results = engine.get_results("single")
        assert results["relative_uplift"] is None
        assert results["p_value"] is None
        assert results["significant"] is False

    def test_results_missing_experiment(self) -> None:
        engine = _make_engine()
        assert engine.get_results("missing") == {}


# ---------------------------------------------------------------------------
# Statistical significance
# ---------------------------------------------------------------------------


class TestStatisticalSignificance:
    def test_large_difference_is_significant(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        control_users, treatment_users = [], []
        for i in range(1000):
            v = engine.get_variant(f"u-{i}", "test_exp")
            (control_users if v.name == "control" else treatment_users).append(f"u-{i}")
        # Control: 1000 impressions, 100 conversions (10%)
        for u in control_users[:1000]:
            engine.track_impression(u, "test_exp")
        for u in control_users[:100]:
            engine.track_conversion(u, "test_exp")
        # Treatment: 1000 impressions, 150 conversions (15%)
        for u in treatment_users[:1000]:
            engine.track_impression(u, "test_exp")
        for u in treatment_users[:150]:
            engine.track_conversion(u, "test_exp")
        results = engine.get_results("test_exp")
        assert results["p_value"] is not None
        assert results["p_value"] < 0.05
        assert results["significant"] is True

    def test_small_difference_not_significant(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        control_users, treatment_users = [], []
        for i in range(1000):
            v = engine.get_variant(f"u-{i}", "test_exp")
            (control_users if v.name == "control" else treatment_users).append(f"u-{i}")
        # Control: 1000 impressions, 100 conversions (10%)
        for u in control_users[:1000]:
            engine.track_impression(u, "test_exp")
        for u in control_users[:100]:
            engine.track_conversion(u, "test_exp")
        # Treatment: 1000 impressions, 110 conversions (11%)
        for u in treatment_users[:1000]:
            engine.track_impression(u, "test_exp")
        for u in treatment_users[:110]:
            engine.track_conversion(u, "test_exp")
        results = engine.get_results("test_exp")
        assert results["p_value"] is not None
        assert results["p_value"] > 0.05
        assert results["significant"] is False

    def test_below_min_conversions_not_significant(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        control_users, treatment_users = [], []
        for i in range(100):
            v = engine.get_variant(f"u-{i}", "test_exp")
            (control_users if v.name == "control" else treatment_users).append(f"u-{i}")
        # Control: 3 conversions, Treatment: 4 conversions (both < 5)
        for u in control_users[:100]:
            engine.track_impression(u, "test_exp")
        for u in control_users[:3]:
            engine.track_conversion(u, "test_exp")
        for u in treatment_users[:100]:
            engine.track_impression(u, "test_exp")
        for u in treatment_users[:4]:
            engine.track_conversion(u, "test_exp")
        results = engine.get_results("test_exp")
        # p_value should be None because < 5 conversions per variant
        assert results["p_value"] is None
        assert results["significant"] is False

    def test_zero_impressions_no_p_value(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        results = engine.get_results("test_exp")
        assert results["p_value"] is None
        assert results["significant"] is False


class TestTwoProportionZTest:
    def test_clear_winner(self) -> None:
        p = _two_proportion_z_test(100, 1000, 150, 1000)
        assert p is not None
        assert p < 0.01

    def test_no_difference(self) -> None:
        p = _two_proportion_z_test(100, 1000, 100, 1000)
        assert p is not None
        assert p > 0.1  # should be very high

    def test_below_min_conversions(self) -> None:
        p = _two_proportion_z_test(3, 100, 4, 100)
        assert p is None

    def test_zero_impressions(self) -> None:
        p = _two_proportion_z_test(0, 0, 0, 0)
        assert p is None

    def test_zero_control_impressions(self) -> None:
        p = _two_proportion_z_test(0, 0, 10, 100)
        assert p is None

    def test_zero_treatment_impressions(self) -> None:
        p = _two_proportion_z_test(10, 100, 0, 0)
        assert p is None

    def test_pool_edge_cases(self) -> None:
        # All convert: p_pool = 1.0, should return None
        p = _two_proportion_z_test(100, 100, 100, 100)
        assert p is None

        # None convert: p_pool = 0.0, should return None
        p = _two_proportion_z_test(0, 100, 0, 100)
        assert p is None

    def test_phi(self) -> None:
        # phi(0) = 0.5 (approximation tolerance)
        assert _phi(0) == pytest.approx(0.5)
        # phi(inf) -> 1.0
        assert _phi(10) > 0.999999
        # phi(-inf) -> 0.0
        assert _phi(-10) < 0.000001


# ---------------------------------------------------------------------------
# Engine: admin helpers
# ---------------------------------------------------------------------------


class TestAdminHelpers:
    def test_reset_experiment(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        for i in range(100):
            engine.track_impression(f"user-{i}", "test_exp")
            engine.track_conversion(f"user-{i}", "test_exp")
        engine.reset_experiment("test_exp")
        results = engine.get_results("test_exp")
        assert results["control"]["impressions"] == 0
        assert results["control"]["conversions"] == 0
        assert results["treatment"]["impressions"] == 0
        assert results["treatment"]["conversions"] == 0

    def test_reset_re_allows_impressions(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        engine.track_impression("user-1", "test_exp")
        engine.reset_experiment("test_exp")
        engine.track_impression("user-1", "test_exp")
        results = engine.get_results("test_exp")
        variant = engine.get_variant("user-1", "test_exp")
        assert results[variant.name]["impressions"] == 1

    def test_reset_only_affects_target_experiment(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(id="e1"))
        engine.register_experiment(_make_experiment(id="e2"))
        engine.track_impression("user-1", "e1")
        engine.track_impression("user-1", "e2")
        engine.reset_experiment("e1")
        results = engine.get_results("e2")
        assert results["control"]["impressions"] + results["treatment"]["impressions"] == 1

    def test_to_snapshot(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(id="e1"))
        engine.register_experiment(
            Experiment(
                id="e2",
                name="Second",
                variants=[Variant(name="only")],
                status="draft",
            )
        )
        engine.track_impression("user-1", "e1")
        snapshot = engine.to_snapshot()
        assert "e1" in snapshot
        assert "e2" in snapshot
        assert snapshot["e1"]["experiment"]["name"] == "Test Experiment"
        assert snapshot["e2"]["experiment"]["status"] == "draft"
        assert "results" in snapshot["e1"]

    def test_to_snapshot_empty(self) -> None:
        engine = _make_engine()
        assert engine.to_snapshot() == {}


# ---------------------------------------------------------------------------
# Integration with feature flags
# ---------------------------------------------------------------------------


class TestFeatureFlagIntegration:
    def test_experiment_disabled_by_env_var(self, monkeypatch) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(id="checkout_flow"))
        monkeypatch.setenv("FF_AB_CHECKOUT_FLOW", "false")
        assert engine.get_variant("user-1", "checkout_flow") is None

    def test_experiment_disabled_by_env_var_zero(self, monkeypatch) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(id="checkout_flow"))
        monkeypatch.setenv("FF_AB_CHECKOUT_FLOW", "0")
        assert engine.get_variant("user-1", "checkout_flow") is None

    def test_experiment_disabled_by_env_var_off(self, monkeypatch) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(id="checkout_flow"))
        monkeypatch.setenv("FF_AB_CHECKOUT_FLOW", "off")
        assert engine.get_variant("user-1", "checkout_flow") is None

    def test_experiment_enabled_by_env_var(self, monkeypatch) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(id="checkout_flow"))
        monkeypatch.setenv("FF_AB_CHECKOUT_FLOW", "true")
        assert engine.get_variant("user-1", "checkout_flow") is not None

    def test_kebab_case_id_mapped_to_underscore_flag(self, monkeypatch) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(id="hero-cta-v2"))
        monkeypatch.setenv("FF_AB_HERO_CTA_V2", "false")
        assert engine.get_variant("user-1", "hero-cta-v2") is None

    def test_percentage_flag_gates_assignment(self, monkeypatch) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(id="pct_test"))
        # 0% rollout means disabled for everyone
        monkeypatch.setenv("FF_AB_PCT_TEST_PERCENT", "0")
        assert engine.get_variant("user-1", "pct_test") is None

    def test_percentage_flag_100_percent(self, monkeypatch) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment(id="pct_test"))
        monkeypatch.setenv("FF_AB_PCT_TEST_PERCENT", "100")
        assert engine.get_variant("user-1", "pct_test") is not None


# ---------------------------------------------------------------------------
# Edge cases and stress tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_many_variants(self) -> None:
        engine = _make_engine()
        variants = [Variant(name=f"v{i}", weight=1) for i in range(10)]
        engine.register_experiment(Experiment(id="many", name="Many", variants=variants))
        counts = {v.name: 0 for v in variants}
        for i in range(1000):
            v = engine.get_variant(f"user-{i}", "many")
            counts[v.name] += 1
        for v in variants:
            assert counts[v.name] > 50  # should be roughly 100 each

    def test_zero_weight_variant_never_assigned(self) -> None:
        engine = _make_engine()
        engine.register_experiment(
            Experiment(
                id="zero",
                name="Zero",
                variants=[
                    Variant(name="a", weight=1),
                    Variant(name="b", weight=0),
                ],
            )
        )
        for i in range(100):
            v = engine.get_variant(f"user-{i}", "zero")
            assert v.name == "a"

    def test_all_variants_zero_weight_falls_to_control(self) -> None:
        engine = _make_engine()
        engine.register_experiment(
            Experiment(
                id="all_zero",
                name="All Zero",
                variants=[
                    Variant(name="a", weight=0),
                    Variant(name="b", weight=0),
                ],
            )
        )
        v = engine.get_variant("user-1", "all_zero")
        assert v.name == "a"

    def test_variant_config_preserved(self) -> None:
        engine = _make_engine()
        config = {"button_text": "Buy Now", "color": "#ff0000"}
        engine.register_experiment(
            Experiment(
                id="cfg",
                name="Config",
                variants=[Variant(name="t1", weight=1, config=config)],
            )
        )
        v = engine.get_variant("user-1", "cfg")
        assert v.config == config

    def test_results_with_float_values(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        # Track a conversion with a non-integer value (e.g., revenue)
        engine.track_conversion("user-1", "test_exp", value=99.99)
        results = engine.get_results("test_exp")
        variant = engine.get_variant("user-1", "test_exp")
        assert results[variant.name]["conversions"] == 99.99

    def test_concurrent_assignments_consistent(self) -> None:
        engine = _make_engine()
        engine.register_experiment(_make_experiment())
        # Simulate many concurrent users
        for i in range(5000):
            v1 = engine.get_variant(f"user-{i}", "test_exp")
            v2 = engine.get_variant(f"user-{i}", "test_exp")
            assert v1.name == v2.name

    def test_experiment_with_special_chars_in_id(self) -> None:
        engine = _make_engine()
        engine.register_experiment(
            Experiment(
                id="exp_123-abc",
                name="Special",
                variants=[Variant(name="a"), Variant(name="b")],
            )
        )
        v = engine.get_variant("user-1", "exp_123-abc")
        assert v is not None
        assert v.name in {"a", "b"}
