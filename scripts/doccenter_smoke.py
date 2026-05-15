#!/usr/bin/env python3
"""Document Center production smoke checks for THO.

This smoke is deliberately narrower than ``production_smoke.py``: it only
answers whether the Document Center is usable right now. Read-only checks run
by default. Live PDF/customer writes require explicit flags and use synthetic
``Smoke Buyer`` data so generated artifacts are filtered out of the normal
Document Desk history.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener


DEFAULT_BASE_URL = "https://tho.sapphirealpha.xyz"
DEFAULT_TEMPLATE = "TMHA_SalesContract.pdf"
DEFAULT_PACKET = "standard_closing"
USER_AGENT = "tho-doccenter-smoke/1.0"


@dataclass
class Probe:
    name: str
    ok: bool
    status: int | None
    evidence: str
    elapsed_ms: int


class SmokeClient:
    def __init__(self, base_url: str, *, timeout: float) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.jar))

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        max_bytes: int = 25 * 1024 * 1024,
    ) -> tuple[int, bytes, str, int]:
        url = urljoin(self.base_url, path.lstrip("/"))
        data = None
        headers = {"User-Agent": USER_AGENT}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method.upper())
        started = time.perf_counter()
        try:
            with self.opener.open(request, timeout=self.timeout) as response:  # nosec B310
                body = response.read(max_bytes)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return response.status, body, response.headers.get("content-type", ""), elapsed_ms
        except HTTPError as exc:
            body = exc.read(max_bytes)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return exc.code, body, exc.headers.get("content-type", ""), elapsed_ms
        except URLError as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            raise RuntimeError(f"{path} network error: {exc}") from exc

    def json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any], int]:
        status, body, _content_type, elapsed_ms = self.request(method, path, payload=payload)
        try:
            parsed = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{path} did not return JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(f"{path} returned non-object JSON")
        return status, parsed, elapsed_ms


def _probe(name: str, status: int | None, elapsed_ms: int, ok: bool, evidence: str) -> Probe:
    return Probe(name=name, ok=ok, status=status, evidence=evidence, elapsed_ms=elapsed_ms)


def _iso_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _first(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]
    return None


def _clean(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _build_document_data(inventory_home: dict[str, Any] | None, stamp: str) -> dict[str, Any]:
    home = inventory_home or {}
    manufacturer = _clean(home.get("manufacturer"), "Clayton Homes")
    model = _clean(home.get("model_name") or home.get("model"), "Smoke Verification Home")
    price = home.get("sale_price")
    try:
        numeric_price = float(price)
    except (TypeError, ValueError):
        numeric_price = 0.0
    if numeric_price <= 0:
        numeric_price = 125000.0

    return {
        "salesrep": "Document Center Smoke",
        "buyer_first_name": "Document",
        "buyer_last_name": f"Smoke Buyer {stamp}",
        "buyer_name": f"Document Smoke Buyer {stamp}",
        "buyer_phone": "5125550199",
        "buyer_email": f"doc-smoke-{stamp}@example.com",
        "buyer_address": "123 Smoke Test Loop",
        "buyer_city": "Austin",
        "buyer_county": "Travis",
        "buyer_state": "TX",
        "buyer_zip": "78701",
        "buyer_city_state_zip": "Austin, TX 78701",
        "buyer_full_address": "123 Smoke Test Loop, Austin, TX 78701",
        "mailing_address": "123 Smoke Test Loop",
        "mailing_city": "Austin",
        "mailing_state": "TX",
        "mailing_zip": "78701",
        "mailing_city_state_zip": "Austin, TX 78701",
        "mailing_full_address": "123 Smoke Test Loop, Austin, TX 78701",
        "manufacturer": manufacturer,
        "model": model,
        "manufacturer_model": f"{manufacturer} {model}",
        "year": _clean(home.get("year"), "2026"),
        "is_new": bool(home.get("is_new", True)),
        "serial_number_1": _clean(home.get("serial_number"), f"SMOKE-{stamp}"),
        "serial_number_2": _clean(home.get("serial_number_2")),
        "label_number_1": _clean(home.get("label_number"), f"HUD-{stamp[-6:]}"),
        "label_number_2": _clean(home.get("label_number_2")),
        "no_of_sections": _clean(home.get("sections"), "1"),
        "width": _clean(home.get("width"), "14"),
        "length": _clean(home.get("length"), "60"),
        "sq_ft": _clean(home.get("sq_ft") or home.get("sqft"), "820"),
        "wind_zone": _clean(home.get("wind_zone"), "II"),
        "weight_sec_1": _clean(home.get("weight_sec_1"), "10000"),
        "weight_sec_2": _clean(home.get("weight_sec_2")),
        "manufacturer_address": _clean(home.get("manufacturer_address"), "500 Factory Road"),
        "manufacturer_city": _clean(home.get("manufacturer_city"), "Austin"),
        "manufacturer_state": _clean(home.get("manufacturer_state"), "TX"),
        "manufacturer_zip": _clean(home.get("manufacturer_zip"), "78701"),
        "manufacturer_city_state_zip": _clean(
            home.get("manufacturer_city_state_zip"), "Austin, TX 78701"
        ),
        "installer_type": "tho",
        "installer_name": "Texas Home Outlet",
        "installer_phone": "2542965300",
        "installer_address": "1101 Industrial Blvd",
        "installer_city": "Waco",
        "installer_state": "TX",
        "installer_zip": "76705",
        "installer_address_city_state_zip": "1101 Industrial Blvd, Waco, TX 76705",
        "installer_name_address": "Texas Home Outlet, 1101 Industrial Blvd, Waco, TX 76705",
        "sales_price": f"{numeric_price:.2f}",
        "down_payment": "5000",
        "loan_term": "240",
        "apr": "7.5",
        "finance_charge": "0",
        "max_financed": f"{numeric_price - 5000:.2f}",
        "monthly_payment": "950",
        "total_payments": "228000",
        "creditor_name": "Synthetic Verification Lender",
        "creditor_phone": "5125550144",
        "creditor_address": "456 Lender Ave",
        "creditor_city_state_zip": "Austin, TX 78701",
        "smoke_id": f"doc-smoke-{stamp}",
    }


def _download_probe(client: SmokeClient, name: str, download_url: str, min_bytes: int) -> Probe:
    status, body, content_type, elapsed_ms = client.request("GET", download_url)
    ok = status == 200 and body.startswith(b"%PDF-") and len(body) >= min_bytes
    evidence = f"bytes={len(body)}; content_type={content_type or 'unknown'}; pdf={body[:5]!r}"
    return _probe(name, status, elapsed_ms, ok, evidence)


def _document_result_downloads(result: dict[str, Any]) -> list[tuple[str, str]]:
    downloads: list[tuple[str, str]] = []
    merged = result.get("merged_document")
    if not isinstance(merged, dict):
        merged = result.get("merged")
    if isinstance(merged, dict) and merged.get("download_url"):
        downloads.append(("batch_merged_pdf", str(merged["download_url"])))
    for index, document in enumerate(result.get("documents") or [], start=1):
        if isinstance(document, dict) and document.get("download_url"):
            downloads.append((f"batch_document_{index}_pdf", str(document["download_url"])))
    if result.get("download_url"):
        downloads.append(("packet_pdf", str(result["download_url"])))
    return downloads


def run_smoke(
    *,
    base_url: str,
    pin: str | None,
    timeout: float,
    write_documents: bool,
    write_packet: bool,
    write_customer: bool,
    template: str,
    packet_name: str,
) -> tuple[bool, dict[str, Any]]:
    client = SmokeClient(base_url, timeout=timeout)
    probes: list[Probe] = []
    stamp = _iso_stamp()
    inventory_home: dict[str, Any] | None = None

    if not pin:
        raise RuntimeError("Admin PIN required via --pin or THO_ADMIN_PIN")

    status, payload, elapsed_ms = client.json("POST", "/api/admin/verify", payload={"pin": pin})
    probes.append(
        _probe(
            "admin_pin_auth",
            status,
            elapsed_ms,
            status == 200 and payload.get("success") is True,
            f"success={payload.get('success')}",
        )
    )

    status, payload, elapsed_ms = client.json("GET", "/api/admin/check")
    probes.append(
        _probe(
            "admin_session_check",
            status,
            elapsed_ms,
            status == 200 and payload.get("valid") is True,
            f"valid={payload.get('valid')}",
        )
    )

    status, readiness, elapsed_ms = client.json("GET", "/api/documents/readiness")
    probes.append(
        _probe(
            "document_readiness",
            status,
            elapsed_ms,
            status == 200
            and readiness.get("status") == "ready"
            and int(readiness.get("template_count") or 0) >= 60
            and int(readiness.get("packet_count") or 0) >= 1
            and readiness.get("output_dir") == "available",
            (
                f"status={readiness.get('status')}; templates={readiness.get('template_count')}; "
                f"packets={readiness.get('packet_count')}; output={readiness.get('output_dir')}; "
                f"gcs={readiness.get('gcs_document_count')}"
            ),
        )
    )

    status, templates_payload, elapsed_ms = client.json("GET", "/api/documents/templates")
    templates = templates_payload.get("templates") or []
    packets = templates_payload.get("packets") or []
    template_names = {item.get("template_name") for item in templates if isinstance(item, dict)}
    packet_names = {item.get("packet_name") for item in packets if isinstance(item, dict)}
    probes.append(
        _probe(
            "template_catalog",
            status,
            elapsed_ms,
            status == 200 and template in template_names and packet_name in packet_names,
            f"templates={len(templates)}; packets={len(packets)}; template={template in template_names}; packet={packet_name in packet_names}",
        )
    )

    status, inventory_payload, elapsed_ms = client.json(
        "GET", "/api/inventory?limit=25&status=AVAILABLE"
    )
    inventory = inventory_payload.get("inventory") or []
    first_home = _first(inventory)
    if isinstance(first_home, dict):
        inventory_home = first_home
    probes.append(
        _probe(
            "inventory_for_autofill",
            status,
            elapsed_ms,
            status == 200 and len(inventory) > 0 and bool(inventory_home),
            f"count={len(inventory)}; first_id={inventory_home.get('id') if inventory_home else None}",
        )
    )

    status, history_payload, elapsed_ms = client.json("GET", "/api/documents/history")
    documents = history_payload.get("documents") or []
    probes.append(
        _probe(
            "document_history",
            status,
            elapsed_ms,
            status == 200 and isinstance(documents, list),
            f"visible={history_payload.get('total')}; hidden_smoke={history_payload.get('hidden_test_document_count')}",
        )
    )

    data = _build_document_data(inventory_home, stamp)

    if write_documents:
        status, result, elapsed_ms = client.json(
            "POST",
            "/api/documents/generate-batch",
            payload={"templates": [template], "data": data, "merge": True},
        )
        batch_ok = (
            status == 200
            and result.get("success") is True
            and int(result.get("successful") or 0) >= 1
            and _document_result_downloads(result)
        )
        probes.append(
            _probe(
                "generate_single_document",
                status,
                elapsed_ms,
                bool(batch_ok),
                f"success={result.get('success')}; successful={result.get('successful')}; total={result.get('total')}",
            )
        )
        for name, download_url in _document_result_downloads(result):
            probes.append(_download_probe(client, name, download_url, min_bytes=10_000))

    if write_packet:
        status, result, elapsed_ms = client.json(
            "POST",
            "/api/documents/generate-packet",
            payload={"packet_name": packet_name, "data": data},
        )
        included = result.get("documents_included") or []
        probes.append(
            _probe(
                "generate_packet",
                status,
                elapsed_ms,
                status == 200
                and result.get("success") is True
                and int(result.get("page_count") or 0) > 0
                and len(included) > 0
                and bool(result.get("download_url")),
                (
                    f"success={result.get('success')}; pages={result.get('page_count')}; "
                    f"included={len(included)}; skipped={len(result.get('documents_skipped') or [])}; "
                    f"error={result.get('error') or ''}"
                ),
            )
        )
        for name, download_url in _document_result_downloads(result):
            probes.append(_download_probe(client, name, download_url, min_bytes=25_000))

    if write_customer:
        customer_payload = {
            "full_name": data["buyer_name"],
            "phone": data["buyer_phone"],
            "email": data["buyer_email"],
            "address": data["mailing_full_address"],
            "status": "LEAD",
            "notes": f"Synthetic Document Center smoke record {stamp}. Safe to delete.",
            "source": "doccenter_smoke",
            "ssn": "123-45-6789",
        }
        status, result, elapsed_ms = client.json("POST", "/api/customers", payload=customer_payload)
        probes.append(
            _probe(
                "customer_record_save",
                status,
                elapsed_ms,
                status == 200 and result.get("success") is True,
                f"success={result.get('success')}; id={result.get('id') or result.get('customer_id')}",
            )
        )
        query = urlencode({"q": data["buyer_name"], "limit": 5})
        status, result, elapsed_ms = client.json("GET", f"/api/customers/search?{query}")
        serialized = json.dumps(result)
        probes.append(
            _probe(
                "customer_record_search",
                status,
                elapsed_ms,
                status == 200 and data["buyer_name"] in serialized and "123-45-6789" not in serialized,
                f"status={status}; contains_name={data['buyer_name'] in serialized}; raw_ssn={'123-45-6789' in serialized}",
            )
        )

    ok = all(probe.ok for probe in probes)
    summary = {
        "ok": ok,
        "base_url": base_url.rstrip("/"),
        "stamp": stamp,
        "mode": {
            "write_documents": write_documents,
            "write_packet": write_packet,
            "write_customer": write_customer,
        },
        "probes": [asdict(probe) for probe in probes],
    }
    return ok, summary


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--pin", default=os.environ.get("THO_ADMIN_PIN"))
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--template", default=DEFAULT_TEMPLATE)
    parser.add_argument("--packet", default=DEFAULT_PACKET)
    parser.add_argument("--write-documents", action="store_true")
    parser.add_argument("--write-packet", action="store_true")
    parser.add_argument("--write-customer", action="store_true")
    parser.add_argument(
        "--write-all",
        action="store_true",
        help="Run single-doc, packet, and customer synthetic write probes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    write_documents = args.write_documents or args.write_all
    write_packet = args.write_packet or args.write_all
    write_customer = args.write_customer or args.write_all
    try:
        ok, summary = run_smoke(
            base_url=args.base_url,
            pin=args.pin,
            timeout=args.timeout,
            write_documents=write_documents,
            write_packet=write_packet,
            write_customer=write_customer,
            template=args.template,
            packet_name=args.packet,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
