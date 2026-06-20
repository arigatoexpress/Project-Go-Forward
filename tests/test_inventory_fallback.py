"""Regression guard: a transient Firestore/JSON outage must not be cached.

Before this, _load_inventory cached whatever it produced for 300s — including
the single-fake-home sample fallback — so a brief Firestore blip froze a
near-empty/fake catalog in front of customers for 5 minutes. Healthy results are
still cached; degraded (sample) results are not, so the next request recovers.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.inventory_tools as inv  # noqa: E402


def _patch(monkeypatch, firestore_homes, website_homes=()):
    monkeypatch.setattr(inv, "_load_inventory_from_firestore", lambda: list(firestore_homes))
    monkeypatch.setattr(inv, "_load_inventory_from_json", lambda: [])
    monkeypatch.setattr(inv, "_get_sample_inventory", lambda: [{"model_name": "The Nassau"}])
    monkeypatch.setattr(inv, "_load_website_homes", lambda: list(website_homes))
    monkeypatch.setattr(inv, "cache_get", lambda key: None)
    calls = []
    monkeypatch.setattr(
        inv, "cache_set", lambda key, value, ttl_seconds=None: calls.append((key, len(value)))
    )
    return calls


def test_degraded_sample_fallback_is_not_cached(monkeypatch):
    calls = _patch(monkeypatch, firestore_homes=[])  # firestore + json empty -> sample
    result = inv._load_inventory()
    assert any(h["model_name"] == "The Nassau" for h in result)  # degraded path taken
    assert calls == [], "transient sample fallback must NOT be cached"


def test_healthy_inventory_is_cached(monkeypatch):
    homes = [{"model_name": f"Home {i}"} for i in range(12)]
    calls = _patch(monkeypatch, firestore_homes=homes)
    result = inv._load_inventory()
    assert len(result) == 12
    assert len(calls) == 1, "healthy inventory should be cached"
