"""A/B Testing Framework for Texas Home Outlet

Built on top of ``tools.feature_flags`` for experiment gating and
configuration.  Provides deterministic user assignment, impression/conversion
tracking, and statistical significance testing.

Design
------
- **Deterministic assignment** — SHA-256 hash of ``experiment_id + user_id``
  gives a stable 0-99 bucket; cumulative variant weights map the bucket to a
  variant.  The same user always sees the same variant for a given experiment.
- **Feature-flag integration** — Experiments are gated by
  ``FF_AB_<NAME>`` / ``FEATURE_FLAG_AB_<NAME>`` so operators can start,
  pause, or stop experiments without code changes.
- **Storage** — Events are written to Redis when available (via
  ``caching.cache_set`` / ``caching.cache_get``), with an in-memory fallback
  so tests run without external infrastructure.
- **Metrics** — Conversion rate per variant, two-proportion z-test for
  statistical significance vs. a control variant.
- **PII-safe** — No user identifiers are stored in long-term state; only
  aggregated counts per variant are persisted.

Usage
-----
    from tools.ab_testing import ABTestEngine, Experiment, Variant

    engine = ABTestEngine()
    engine.register_experiment(Experiment(
        id="hero_cta_v2",
        name="Hero CTA Copy Test",
        variants=[
            Variant(name="control", weight=0.5),
            Variant(name="treatment", weight=0.5,
                    config={"cta_text": "Get a Free Quote"}),
        ],
    ))

    variant = engine.get_variant("user-123", "hero_cta_v2")
    if variant:
        engine.track_impression("user-123", "hero_cta_v2")
        # ... user clicks CTA ...
        engine.track_conversion("user-123", "hero_cta_v2", value=1.0)

    results = engine.get_results("hero_cta_v2")
    # results["treatment"]["conversion_rate"] -> 0.12
    # results["significant"] -> True / False
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import time
from typing import Any

import caching
import tools.feature_flags as ff
from structured_logging import logger as struct_logger

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Variant:
    """A single variant within an A/B experiment.

    Parameters
    ----------
    name:
        Unique identifier within the experiment (e.g. ``"control"``).
    weight:
        Relative traffic share.  Weights are normalised to sum to 1.0 at
        assignment time.
    config:
        Optional dict of variant-specific configuration overrides (e.g. button
        text, pricing, algorithm parameters).  Must be JSON-serializable.
    """

    name: str
    weight: float = 1.0
    config: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValueError("Variant name must be a non-empty string")
        if self.weight < 0:
            raise ValueError(f"Variant weight must be >= 0, got {self.weight}")
        if not isinstance(self.config, dict):
            raise ValueError("Variant config must be a dict")


@dataclasses.dataclass(frozen=True)
class Experiment:
    """An A/B experiment definition.

    Parameters
    ----------
    id:
        Globally unique experiment identifier (kebab-case recommended).
    name:
        Human-readable display name.
    variants:
        List of variants.  Must contain at least one variant; the first variant
        is conventionally the **control**.
    status:
        ``draft`` | ``running`` | ``paused`` | ``ended``.  Only ``running``
        experiments assign users.
    description:
        Optional explanation for the admin dashboard.
    """

    id: str
    name: str
    variants: list[Variant]
    status: str = "running"
    description: str = ""

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError("Experiment id must be a non-empty string")
        if not self.name or not isinstance(self.name, str):
            raise ValueError("Experiment name must be a non-empty string")
        if self.status not in {"draft", "running", "paused", "ended"}:
            raise ValueError(
                f"Invalid experiment status: {self.status!r}"
            )
        if not self.variants:
            raise ValueError("Experiment must have at least one variant")
        seen = set()
        for v in self.variants:
            if v.name in seen:
                raise ValueError(f"Duplicate variant name: {v.name!r}")
            seen.add(v.name)

    @property
    def control_variant(self) -> Variant:
        """Return the first variant (conventionally control)."""
        return self.variants[0]

    @property
    def total_weight(self) -> float:
        """Sum of all variant weights."""
        return sum(v.weight for v in self.variants)

    def get_variant_by_bucket(self, bucket: int) -> Variant:
        """Map a 0-99 bucket to a variant using cumulative weights."""
        total = self.total_weight
        if total <= 0:
            return self.control_variant
        cumulative = 0.0
        for v in self.variants:
            cumulative += v.weight / total * 100.0
            if bucket < cumulative:
                return v
        return self.variants[-1]


@dataclasses.dataclass(frozen=True)
class Assignment:
    """Snapshot of a user-variant assignment."""

    user_id: str
    experiment_id: str
    variant_name: str
    assigned_at: float


@dataclasses.dataclass(frozen=True)
class Event:
    """A single tracked event in an experiment."""

    user_id: str
    experiment_id: str
    variant_name: str
    event_type: str
    timestamp: float
    value: float


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ABTestEngine:
    """In-memory experiment registry with impression/conversion tracking.

    Storage is **ephemeral** by default (in-memory).  When Redis is configured
    the engine persists aggregated counters per variant so that restarts do not
    lose data.  Individual user-level events are **not** persisted — only the
    counts that feed the metrics pipeline.
    """

    def __init__(self) -> None:
        self._experiments: dict[str, Experiment] = {}
        # In-memory event store: {experiment_id: {variant_name: {event_type: count}}}
        self._events: dict[str, dict[str, dict[str, float]]] = {}
        # Track which users have been assigned to avoid double-counting impressions
        self._assigned: set[str] = set()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_experiment(self, experiment: Experiment) -> None:
        """Register (or overwrite) an experiment definition."""
        self._experiments[experiment.id] = experiment
        struct_logger.info(
            "ab_experiment_registered",
            experiment_id=experiment.id,
            name=experiment.name,
            variant_count=len(experiment.variants),
            status=experiment.status,
        )

    def get_experiment(self, experiment_id: str) -> Experiment | None:
        """Return the experiment definition, or ``None`` if not registered."""
        return self._experiments.get(experiment_id)

    def list_experiments(self) -> list[Experiment]:
        """Return all registered experiments."""
        return list(self._experiments.values())

    # ------------------------------------------------------------------
    # Assignment
    # ------------------------------------------------------------------

    def get_variant(self, user_id: str, experiment_id: str) -> Variant | None:
        """Return the variant assigned to *user_id* for *experiment_id*, or
        ``None`` if the experiment is not running, disabled, or not found.
        """
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            return None

        # Feature-flag gate: FF_AB_<experiment_id> must be enabled (or absent).
        flag_name = f"AB_{experiment_id.upper().replace('-', '_')}"
        if not ff.is_enabled(flag_name, default=True):
            struct_logger.debug(
                "ab_experiment_disabled_by_flag",
                experiment_id=experiment_id,
                flag_name=flag_name,
            )
            return None

        if experiment.status != "running":
            return None

        # Percentage-based gate (0% means disabled for all users)
        pct = ff.get_percentage(flag_name, default=-1)
        if pct == 0:
            return None

        # Deterministic bucket: first 8 hex chars of SHA-256 -> 32-bit int -> 0-99
        h = hashlib.sha256(f"{experiment_id}:{user_id}".encode()).hexdigest()
        bucket = int(h[:8], 16) % 100
        return experiment.get_variant_by_bucket(bucket)

    def get_assignment(self, user_id: str, experiment_id: str) -> Assignment | None:
        """Return the full assignment record, or ``None`` if not applicable."""
        variant = self.get_variant(user_id, experiment_id)
        if variant is None:
            return None
        return Assignment(
            user_id=user_id,
            experiment_id=experiment_id,
            variant_name=variant.name,
            assigned_at=time.time(),
        )

    # ------------------------------------------------------------------
    # Tracking
    # ------------------------------------------------------------------

    def track_impression(self, user_id: str, experiment_id: str) -> None:
        """Record that *user_id* saw their assigned variant.  Idempotent per
        user per experiment within the engine lifetime.
        """
        experiment = self._experiments.get(experiment_id)
        if experiment is None or experiment.status != "running":
            return

        key = f"{experiment_id}:{user_id}:impression"
        if key in self._assigned:
            return
        self._assigned.add(key)

        variant = self.get_variant(user_id, experiment_id)
        if variant is None:
            return

        self._increment_event(experiment_id, variant.name, "impressions", 1.0)

    def track_conversion(
        self,
        user_id: str,
        experiment_id: str,
        value: float = 1.0,
    ) -> None:
        """Record a conversion event for *user_id* in *experiment_id*."""
        experiment = self._experiments.get(experiment_id)
        if experiment is None or experiment.status != "running":
            return

        variant = self.get_variant(user_id, experiment_id)
        if variant is None:
            return

        self._increment_event(experiment_id, variant.name, "conversions", value)

    def _increment_event(
        self,
        experiment_id: str,
        variant_name: str,
        event_type: str,
        value: float,
    ) -> None:
        """Bump the in-memory counter and optionally write to Redis."""
        # In-memory
        exp = self._events.setdefault(experiment_id, {})
        var = exp.setdefault(variant_name, {})
        var[event_type] = var.get(event_type, 0.0) + value

        # Try Redis (fire-and-forget; failure is acceptable)
        try:
            self._persist_to_redis(experiment_id, variant_name, event_type, value)
        except Exception as e:
            struct_logger.warning(
                "ab_redis_persist_failed",
                experiment_id=experiment_id,
                variant_name=variant_name,
                event_type=event_type,
                error=str(e),
            )

    def _persist_to_redis(
        self,
        experiment_id: str,
        variant_name: str,
        event_type: str,
        value: float,
    ) -> None:
        """Increment a Redis counter key if Redis is available."""
        redis_key = f"ab:{experiment_id}:{variant_name}:{event_type}"
        # Use cache_get / cache_set for simplicity (counters are aggregated
        # snapshots rather than individual events, so a small race window is
        # acceptable for A/B metrics).
        current = caching.cache_get(redis_key) or 0.0
        caching.cache_set(redis_key, current + value, ttl_seconds=86400 * 30)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_results(self, experiment_id: str) -> dict[str, Any]:
        """Return aggregated metrics for every variant of the experiment.

        Returns a dict shaped like::

            {
                "control": {
                    "impressions": 1000,
                    "conversions": 120,
                    "conversion_rate": 0.12,
                },
                "treatment": {
                    "impressions": 1000,
                    "conversions": 150,
                    "conversion_rate": 0.15,
                },
                "relative_uplift": 0.25,   # (treatment - control) / control
                "p_value": 0.02,
                "significant": True,
                "control_variant": "control",
            }
        """
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            return {}

        control = experiment.control_variant.name
        results: dict[str, Any] = {"control_variant": control}

        for v in experiment.variants:
            name = v.name
            impressions = self._get_count(experiment_id, name, "impressions")
            conversions = self._get_count(experiment_id, name, "conversions")
            rate = conversions / impressions if impressions > 0 else 0.0
            results[name] = {
                "impressions": impressions,
                "conversions": conversions,
                "conversion_rate": round(rate, 6),
            }

        # Relative uplift vs control
        control_data = results.get(control, {})
        control_rate = control_data.get("conversion_rate", 0.0)
        control_impressions = control_data.get("impressions", 0)
        control_conversions = control_data.get("conversions", 0)

        # Find the best non-control variant for uplift calc
        best_variant = None
        best_rate = 0.0
        for v in experiment.variants:
            if v.name == control:
                continue
            rate = results[v.name]["conversion_rate"]
            if rate > best_rate:
                best_rate = rate
                best_variant = v.name

        if best_variant and control_rate > 0:
            results["relative_uplift"] = round(
                (best_rate - control_rate) / control_rate, 6
            )
        else:
            results["relative_uplift"] = None

        # Statistical significance (two-proportion z-test, control vs best)
        if best_variant:
            best_data = results[best_variant]
            best_impressions = best_data["impressions"]
            best_conversions = best_data["conversions"]
            p_value = _two_proportion_z_test(
                control_conversions,
                control_impressions,
                best_conversions,
                best_impressions,
            )
            results["p_value"] = round(p_value, 6) if p_value is not None else None
            results["significant"] = (
                p_value is not None and p_value < 0.05
            )
        else:
            results["p_value"] = None
            results["significant"] = False

        return results

    def _get_count(self, experiment_id: str, variant_name: str, event_type: str) -> float:
        """Return total count for a variant from the in-memory store.

        Redis writes are fire-and-forget (best-effort backup) and are not
        merged back here to avoid double-counting in the same engine instance.
        """
        return (
            self._events.get(experiment_id, {})
            .get(variant_name, {})
            .get(event_type, 0.0)
        )

    # ------------------------------------------------------------------
    # Admin helpers
    # ------------------------------------------------------------------

    def reset_experiment(self, experiment_id: str) -> None:
        """Clear all in-memory counters for an experiment.  Does **not** clear
        Redis counters (operator must run ``redis-cli DEL ab:<experiment_id>:*``
        manually).
        """
        self._events.pop(experiment_id, None)
        # Clear assignment cache for this experiment so impressions are
        # re-counted after a reset.
        self._assigned = {
            k for k in self._assigned
            if not k.startswith(f"{experiment_id}:")
        }
        struct_logger.info(
            "ab_experiment_reset",
            experiment_id=experiment_id,
        )

    def to_snapshot(self) -> dict[str, Any]:
        """JSON-safe snapshot of all experiments and their current results."""
        return {
            exp.id: {
                "experiment": {
                    "id": exp.id,
                    "name": exp.name,
                    "description": exp.description,
                    "status": exp.status,
                    "variants": [
                        {
                            "name": v.name,
                            "weight": v.weight,
                            "config": v.config,
                        }
                        for v in exp.variants
                    ],
                },
                "results": self.get_results(exp.id),
            }
            for exp in self._experiments.values()
        }


# ---------------------------------------------------------------------------
# Statistical utilities
# ---------------------------------------------------------------------------


def _two_proportion_z_test(
    control_conversions: float,
    control_impressions: float,
    treatment_conversions: float,
    treatment_impressions: float,
) -> float | None:
    """Two-proportion z-test for comparing control vs. treatment.

    Returns the two-tailed p-value, or ``None`` if the sample is too small
    (less than 5 conversions per variant) or division by zero would occur.
    """
    if control_impressions <= 0 or treatment_impressions <= 0:
        return None
    if control_conversions < 5 or treatment_conversions < 5:
        return None

    p1 = control_conversions / control_impressions
    p2 = treatment_conversions / treatment_impressions
    n1 = control_impressions
    n2 = treatment_impressions

    p_pool = (control_conversions + treatment_conversions) / (n1 + n2)
    if p_pool <= 0 or p_pool >= 1:
        return None

    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return None

    z = (p2 - p1) / se
    # Two-tailed p-value from standard normal CDF
    p_value = 2 * (1 - _phi(abs(z)))
    return p_value


def _phi(x: float) -> float:
    """Cumulative distribution function for the standard normal distribution.

    Uses the approximation from Abramowitz & Stegun (1964), formula 7.1.26.
    """
    # Constants
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911

    sign = 1 if x >= 0 else -1
    x = abs(x) / math.sqrt(2.0)

    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)

    return 0.5 * (1.0 + sign * y)
