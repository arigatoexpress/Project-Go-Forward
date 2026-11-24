"""Document Center pre-check beacon -> WARNING log line with a stable signature.

Guard for the 2026-11-23 outage: the browser pre-check blocked every Full New
Home closing and the server never saw a request. The beacon makes those blocks
visible to Cloud Logging / tho-logwatch without logging customer data.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import document_precheck_telemetry as telemetry

_PII = ["Jane Q Buyer", "123-45-6789", "jane@example.com", "281-555-0100", "100 Main St"]


def _emit(body, authenticated=False):
    buf = io.StringIO()
    n = telemetry.emit_precheck_blocked(body, authenticated=authenticated, write=buf.write)
    lines = [json.loads(line) for line in buf.getvalue().splitlines()]
    assert n == len(lines)
    return lines, buf.getvalue()


def test_homestead_outage_shape_logs_warning_with_stable_signature():
    lines, _ = _emit(
        {
            "stage": "required",
            "templates": ["Internal_Homestead.pdf"],
            "packet": "full_closing_new",
            "missing_fields": ["manufacturer_model_home_id"],
        },
        authenticated=True,
    )
    assert len(lines) == 1
    line = lines[0]
    assert line["severity"] == "WARNING"
    assert line["message"] == "Document precheck blocked"
    assert line["event"] == "documents.precheck_blocked"
    assert line["template"] == "Internal_Homestead.pdf"
    assert line["packet"] == "full_closing_new"
    assert line["authenticated"] is True and line["test"] is False
    assert line["signature"] == (
        "documents.precheck_blocked Internal_Homestead.pdf required [manufacturer_model_home_id]"
    )


def test_signature_is_order_independent_and_one_line_per_template():
    a, _ = _emit(
        {
            "stage": "required",
            "templates": ["TMHA_SalesContract.pdf", "Internal_Homestead.pdf"],
            "missing_fields": ["model", "manufacturer"],
        }
    )
    b, _ = _emit(
        {
            "stage": "required",
            "templates": ["Internal_Homestead.pdf", "TMHA_SalesContract.pdf"],
            "missing_fields": ["manufacturer", "model"],
        }
    )
    assert [x["signature"] for x in a] == [x["signature"] for x in b]
    assert {x["template"] for x in a} == {"Internal_Homestead.pdf", "TMHA_SalesContract.pdf"}


def test_values_and_unknown_keys_never_reach_the_log():
    _, raw = _emit(
        {
            "stage": "required",
            "templates": ["Internal_Homestead.pdf", *_PII],
            "packet": "Jane Q Buyer",
            "missing_fields": ["buyer_phone", *_PII, "not_a_real_field_key"],
            "buyer_name": "Jane Q Buyer",
            "form": {"buyer_ssn": "123-45-6789"},
        }
    )
    for value in _PII:
        assert value not in raw
    assert "not_a_real_field_key" not in raw
    line = json.loads(raw.splitlines()[0])
    assert line["missing_fields"] == ["buyer_phone"]
    assert line["packet"] == "(custom)"
    assert line["unknown_field_count"] == len(_PII) + 1
    assert line["unknown_template_count"] == len(_PII)
    assert set(line) == {
        "severity",
        "message",
        "timestamp",
        "event",
        "lane",
        "service",
        "signature",
        "template",
        "packet",
        "stage",
        "missing_fields",
        "missing_field_count",
        "selected_template_count",
        "unknown_template_count",
        "unknown_field_count",
        "authenticated",
        "test",
    }


def test_test_flag_and_test_stage_are_marked():
    lines, _ = _emit(
        {"stage": "test", "templates": ["Internal_Homestead.pdf"], "missing_fields": []}
    )
    assert lines[0]["test"] is True
    lines, _ = _emit({"stage": "required", "test": True, "templates": ["Internal_Homestead.pdf"]})
    assert lines[0]["test"] is True


def test_junk_bodies_write_nothing_and_never_raise():
    for body in (
        None,
        [],
        "x",
        {},
        {"templates": "Internal_Homestead.pdf"},
        {"templates": ["nope.pdf"]},
        {"missing_fields": [1, 2]},
        {"templates": [None], "missing_fields": [{"a": 1}]},
    ):
        lines, _ = _emit(body)
        assert lines == []


def test_endpoint_always_ok_and_logs(monkeypatch, capsys):
    from test_api_v1 import create_client

    client, *_ = create_client(monkeypatch)
    for body in ({}, [1], {"templates": ["nope.pdf"]}):
        r = client.post("/api/documents/precheck-blocked", json=body)
        assert r.status_code == 200 and r.json() == {"ok": True}
    capsys.readouterr()
    r = client.post(
        "/api/documents/precheck-blocked",
        json={
            "stage": "test",
            "test": True,
            "templates": ["Internal_Homestead.pdf"],
            "packet": "full_closing_new",
            "missing_fields": ["manufacturer_model_home_id"],
        },
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    out = [json.loads(x) for x in capsys.readouterr().out.splitlines() if "precheck_blocked" in x]
    assert len(out) == 1
    assert out[0]["authenticated"] is False  # no staff session on this request
    assert out[0]["test"] is True


def test_endpoint_ignores_oversized_bodies(monkeypatch, capsys):
    from test_api_v1 import create_client

    client, *_ = create_client(monkeypatch)
    capsys.readouterr()
    big = {
        "templates": ["Internal_Homestead.pdf"],
        "missing_fields": ["buyer_phone"],
        "pad": "x" * 20000,
    }
    r = client.post("/api/documents/precheck-blocked", json=big)
    assert r.status_code == 200
    assert "precheck_blocked" not in capsys.readouterr().out
