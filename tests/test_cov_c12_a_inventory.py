"""Coverage mop-up for six inventory modules (swarm c12, agent A).

Targets (measured baseline, all tests touching these modules):
  - tools/image_url_backfill.py  : 54 missing (L28-29, 79-82, 124-125, 173-190,
                                   201-221, 252-254, 283-286, 292-313, 317)
  - tools/inventory_master_catalog.py: 46 missing (L54, 187-188, 196, 215,
                                   225-230, 238-239, 335-343, 362-366, 376-409)
  - tools/floorplan_image_scan.py: 45 missing (L93, 110-115, 124-134, 170-177,
                                   181-211)
  - tools/inventory_sync.py      : 39 missing (L41-43, 257, 261-267, 341, 395,
                                   418-421, 427, 451-453, 524, 557, 640,
                                   644-645, 697-699, 735-753, 757)
  - tools/image_storage.py       : 39 missing (L165, 168, 185, 210, 215,
                                   267-283, 335-336, 393-394, 396-397, 399,
                                   405-406, 426, 456, 505-516, 582, 588-596, 611)
  - tools/inventory_seed.py      : 33 missing (L45-46, 48, 55, 58-59, 61, 76,
                                   131-136, 171-194)

Dead code documented with proof (NOT forced):
  - image_storage.py L397 (``return []`` in ``_read_order``): reachable only
    after the GCS exception handler, but that handler already raises when
    durable and the condition is False when not — control can never pass
    ``if _requires_durable_storage(): return []`` with the condition True.
  - inventory_sync.py L523-524 (``_get_plan_id`` without-"the" direct lookup):
    ``model_lower.replace("the ", "")`` strips EVERY occurrence of "the ", so
    the result can never equal a KNOWN_MODEL_PLAN_IDS key — every key contains
    "the ". Dead by construction (proof test below).
"""

from __future__ import annotations

import importlib
import importlib.util
import io
import json
import os
import sys
import types
from pathlib import Path

import pytest

# tools.inventory_master_catalog hard-requires pandas at import time
# (SystemExit without it), so the whole file is skipped in dependency-minimal
# environments rather than failing at collection.
pd = pytest.importorskip("pandas")

import tools.floorplan_image_scan as floorplan_image_scan  # noqa: E402
import tools.image_storage as image_storage  # noqa: E402
import tools.image_url_backfill as image_url_backfill  # noqa: E402
import tools.inventory_master_catalog as inventory_master_catalog  # noqa: E402
import tools.inventory_seed as inventory_seed  # noqa: E402
import tools.inventory_sync as inventory_sync  # noqa: E402
from database.models import InventoryStatus  # noqa: E402
from tools.inventory_master_catalog import CatalogRow  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02"  # pragma: allowlist secret - deterministic 1x1 test image
    "0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082"  # pragma: allowlist secret - deterministic 1x1 test image
)


def _jpeg_bytes(size=(64, 64), mode="RGB") -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new(mode, size, "blue").save(buffer, format="JPEG")
    return buffer.getvalue()


def _gif_bytes(frames: int = 2, size=(20, 20)) -> bytes:
    from PIL import Image

    pil_frames = [
        Image.effect_noise(size, 100 + index).convert("P") for index in range(frames)
    ]
    buffer = io.BytesIO()
    pil_frames[0].save(
        buffer, format="GIF", save_all=True, append_images=pil_frames[1:], loop=0, duration=100
    )
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# tools/image_url_backfill.py
# ---------------------------------------------------------------------------


class TestResolveCandidateUrlCatalogEntryWithoutImages:
    """L79-82: catalog entry exists but lists no images -> build from its IDs."""

    def test_catalog_entry_without_images_builds_url_from_ids(self, monkeypatch):
        monkeypatch.setattr(
            image_url_backfill,
            "get_assets_for_home",
            lambda model: {"images": [], "manufacturer_id": "3335", "plan_id": "225053"},
        )
        url = image_url_backfill.resolve_candidate_url(
            {"id": "x", "model_name": "Whatever", "is_new": True}
        )
        assert url == image_url_backfill.build_new_home_url("3335", "225053")

    def test_catalog_entry_without_images_and_no_ids_falls_through(self, monkeypatch):
        monkeypatch.setattr(
            image_url_backfill, "get_assets_for_home", lambda model: {"images": []}
        )
        url = image_url_backfill.resolve_candidate_url(
            {"id": "12345", "model_name": "Whatever", "is_new": False}
        )
        assert url == image_url_backfill.build_preowned_url("12345")


class TestProbeUrlGenericException:
    """L124-125: anything that is not Timeout/ConnectionError becomes error:<exc>."""

    def test_unexpected_exception_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            image_url_backfill.requests, "head", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom"))
        )
        result = image_url_backfill.probe_url("https://example.com/x.jpg")
        assert "error:boom" in str(result["head_status"])
        assert result["content_length"] is None


class _FakeFsDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _FakeFsCollection:
    def __init__(self, docs, raise_stream=False):
        self._docs = docs
        self._raise = raise_stream

    def stream(self):
        if self._raise:
            raise RuntimeError("stream blew up")
        yield from self._docs


class _FakeFsInner:
    def __init__(self, docs, raise_stream=False):
        self._collection = _FakeFsCollection(docs, raise_stream)

    def collection(self, name):
        assert name == "inventory"
        return self._collection


class _FakeFsDb:
    def __init__(self, docs=(), raise_stream=False):
        self.db = _FakeFsInner(docs, raise_stream)
        self.updated: list[tuple] = []

    def update_inventory(self, inventory_id, data):
        self.updated.append((inventory_id, data))


def _patch_firestore_client(monkeypatch, db):
    monkeypatch.setitem(
        sys.modules, "database.firestore_client", types.SimpleNamespace(get_database=lambda: db)
    )


class TestLoadInventoryMissingImage:
    """L173-190: Firestore read paths (success + both exception branches)."""

    def test_streams_only_docs_without_image_url(self, monkeypatch):
        db = _FakeFsDb(
            [
                _FakeFsDoc("a", {"model_name": "M1", "image_url": None}),
                _FakeFsDoc("b", {"model_name": "M2", "image_url": "https://x/y.jpg"}),
                _FakeFsDoc("c", {"model_name": "M3"}),
            ]
        )
        _patch_firestore_client(monkeypatch, db)

        rows = image_url_backfill.load_inventory_missing_image()

        assert {r["id"] for r in rows} == {"a", "c"}

    def test_connect_error_returns_empty(self, monkeypatch):
        def _boom():
            raise RuntimeError("no creds")

        monkeypatch.setitem(
            sys.modules, "database.firestore_client", types.SimpleNamespace(get_database=_boom)
        )
        assert image_url_backfill.load_inventory_missing_image() == []

    def test_stream_error_returns_partial_empty(self, monkeypatch, caplog):
        db = _FakeFsDb([], raise_stream=True)
        _patch_firestore_client(monkeypatch, db)

        with caplog.at_level("ERROR"):
            rows = image_url_backfill.load_inventory_missing_image()

        assert rows == []
        assert "Error streaming inventory" in caplog.text


class TestApplyVerified:
    """L201-221: --apply --yes write path, per-record failure handling."""

    def _records(self):
        return [
            {
                "inventory_id": "ok-1",
                "candidate_url": "https://cdn.example.com/1.jpg",
                "head_status": 200,
            },
            {"inventory_id": "not-200", "candidate_url": "https://cdn.example.com/2.jpg", "head_status": 404},
            {"inventory_id": "no-url", "candidate_url": None, "head_status": "skipped_no_ids"},
        ]

    def test_writes_only_verified_records(self, monkeypatch):
        db = _FakeFsDb()
        _patch_firestore_client(monkeypatch, db)

        image_url_backfill._apply_verified(self._records())

        assert db.updated == [("ok-1", {"image_url": "https://cdn.example.com/1.jpg"})]

    def test_per_record_failure_is_logged_and_skipped(self, monkeypatch, caplog):
        db = _FakeFsDb()
        db.update_inventory = lambda _id, _data: (_ for _ in ()).throw(RuntimeError("write denied"))
        _patch_firestore_client(monkeypatch, db)

        with caplog.at_level("ERROR"):
            image_url_backfill._apply_verified(self._records())

        assert "[APPLY] Failed to update ok-1" in caplog.text

    def test_connect_error_aborts_apply(self, monkeypatch, caplog):
        def _boom():
            raise RuntimeError("no creds")

        monkeypatch.setitem(
            sys.modules, "database.firestore_client", types.SimpleNamespace(get_database=_boom)
        )
        with caplog.at_level("ERROR"):
            image_url_backfill._apply_verified(self._records())
        assert "Cannot connect to Firestore for apply" in caplog.text


class TestRunOrchestrationGaps:
    """L252-254 (worker exception) and L283-286 (--apply without --yes)."""

    def test_worker_exception_is_logged_and_others_complete(self, monkeypatch, caplog):
        real_process = image_url_backfill.process_row

        def _flaky(row):
            if row["id"] == "bad":
                raise RuntimeError("worker exploded")
            return real_process(row)

        monkeypatch.setattr(
            image_url_backfill, "load_inventory_missing_image", lambda: [{"id": "bad"}, {"id": "worse"}]
        )
        monkeypatch.setattr(image_url_backfill, "process_row", _flaky)

        class Args:
            out = None
            apply = False
            yes = False

        with caplog.at_level("ERROR"):
            results = image_url_backfill.run(Args())

        # The "bad" row raised in the worker and was logged; "worse" completed
        # as a skipped_no_ids record.
        assert [r["inventory_id"] for r in results] == ["worse"]
        assert results[0]["head_status"] == "skipped_no_ids"
        assert "Unhandled error for bad" in caplog.text

    def test_apply_without_yes_warns_and_writes_nothing(self, monkeypatch, caplog):
        monkeypatch.setattr(
            image_url_backfill,
            "load_inventory_missing_image",
            lambda: [{"id": "1", "model_name": "m", "manufacturer": "x", "is_new": True}],
        )
        monkeypatch.setattr(
            image_url_backfill,
            "probe_url",
            lambda url: {"head_status": 200, "content_length": "1", "content_type": "image/jpeg"},
        )
        monkeypatch.setattr(
            image_url_backfill, "_apply_verified", lambda results: pytest.fail("must not be called")
        )

        class Args:
            out = None
            apply = True
            yes = False

        with caplog.at_level("WARNING"):
            image_url_backfill.run(Args())

        assert "--apply requires --yes" in caplog.text


class TestAssetScraperImportFallback:
    """L28-29: absolute ``tools.asset_scraper`` import fails -> relative fallback.

    In-process exec under patched sys.modules: the absolute name maps to a
    PEP 562 stub raising ImportError from __getattr__ (dunders raise
    AttributeError), while the relative import resolves through a synthetic
    package whose ``asset_scraper`` is the real module.
    """

    def test_relative_fallback_imports_real_module(self):
        real_scraper = sys.modules["tools.asset_scraper"]

        stub = types.ModuleType("tools.asset_scraper")

        def _raising(name):
            if name.startswith("__"):
                raise AttributeError(name)
            raise ImportError(f"cannot import name {name!r} from 'tools.asset_scraper'")

        stub.__getattr__ = _raising

        alt_pkg = types.ModuleType("altpkg")
        alt_pkg.__path__ = []
        sys.modules["altpkg"] = alt_pkg
        sys.modules["altpkg.asset_scraper"] = real_scraper
        sys.modules["tools.asset_scraper"] = stub
        try:
            spec = importlib.util.spec_from_file_location(
                "altpkg.image_url_backfill", image_url_backfill.__file__
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.modules["tools.asset_scraper"] = real_scraper
            sys.modules.pop("altpkg.image_url_backfill", None)
            sys.modules.pop("altpkg.asset_scraper", None)
            sys.modules.pop("altpkg", None)

        assert mod.CDN_BASE == real_scraper.CDN_BASE
        assert mod.get_assets_for_home is real_scraper.get_assets_for_home


class TestMainEntrypoint:
    """L292-313 + L317: argparse plumbing and ``main()`` under runpy."""

    def test_main_dry_run_end_to_end(self, monkeypatch, tmp_path):
        # One row with no derivable candidate URL: exercises resolve + probe
        # skip + JSONL write + the --apply --yes confirmation path, all with
        # zero network access (no model name, no IDs -> skipped_no_ids).
        _patch_firestore_client(
            monkeypatch, _FakeFsDb([_FakeFsDoc("doc-1", {"model_name": "Mystery"})])
        )
        out_file = tmp_path / "results.jsonl"
        monkeypatch.setattr(
            sys, "argv", ["image_url_backfill.py", "--out", str(out_file), "--apply", "--yes"]
        )

        import runpy

        runpy.run_path(str(Path(image_url_backfill.__file__).resolve()), run_name="__main__")

        assert out_file.exists()
        records = [json.loads(line) for line in out_file.read_text().splitlines()]
        assert records[0]["inventory_id"] == "doc-1"
        assert records[0]["candidate_url"] is None
        assert records[0]["head_status"] == "skipped_no_ids"


# ---------------------------------------------------------------------------
# tools/inventory_master_catalog.py
# ---------------------------------------------------------------------------


def _catalog_row(serial="CAV-001", mfr="cavco", model="Mountain Delight", msrp=45900.0):
    return CatalogRow(
        serial_number=serial,
        manufacturer_key=mfr,
        model_name=model,
        width=16,
        length=76,
        msrp=msrp,
        bedrooms=3,
        bathrooms=2,
        sqft=1216,
        status=InventoryStatus.AVAILABLE,
        notes=None,
    )


class TestSysPathBootstrap:
    """L53-54: module inserts repo root into sys.path when invoked as a script."""

    def test_inserts_repo_root_when_missing(self, monkeypatch):
        repo = str(REPO_ROOT)
        monkeypatch.setattr(
            sys,
            "path",
            [p for p in sys.path if os.path.abspath(p or os.curdir) != repo],
        )
        try:
            importlib.reload(inventory_master_catalog)
            assert repo in sys.path
        finally:
            importlib.reload(inventory_master_catalog)


class TestScalarHelpers:
    """L187-188, 196, 215, 225-230, 238-239: normalization edge branches."""

    def test_is_missing_swallows_value_error_from_array_truthiness(self):
        # pd.isna on a list returns an array; bool(array) raises ValueError,
        # which _is_missing must convert to "not missing".
        assert inventory_master_catalog._is_missing([1, 2]) is False
        assert inventory_master_catalog._is_missing(None) is True

    def test_clean_text_integral_branch(self):
        assert inventory_master_catalog._clean_text(7) == "7"
        assert inventory_master_catalog._clean_text(True) == "True"

    def test_resolve_manufacturer_empty_raises(self):
        with pytest.raises(ValueError, match="manufacturer is empty"):
            inventory_master_catalog._resolve_manufacturer(None)

    def test_parse_status_known_and_unknown_values(self):
        from database.models import InventoryStatus

        assert inventory_master_catalog._parse_status("sold") is InventoryStatus.SOLD
        assert inventory_master_catalog._parse_status("PENDING") is InventoryStatus.PENDING
        assert inventory_master_catalog._parse_status("??weird??") is InventoryStatus.AVAILABLE

    def test_to_int_bad_value_returns_none(self):
        assert inventory_master_catalog._to_int("not-a-number") is None
        assert inventory_master_catalog._to_int("3.7") == 3


class TestWriteToFirestoreDryRunPreview:
    """L335-343: dry-run prints at most 10 rows + overflow notice."""

    def test_dry_run_previews_first_ten_and_counts_rest(self, capsys):
        rows = [_catalog_row(serial=f"S{i:03d}") for i in range(12)]

        counts = inventory_master_catalog.write_to_firestore(rows, dry_run=True)

        out = capsys.readouterr().out
        assert out.count("DRY") == 10
        assert "... and 2 more rows" in out
        assert counts == {"created": 0, "updated": 0, "failed": 0, "would_write": 12}

    def test_dry_run_ten_or_fewer_has_no_overflow_notice(self, capsys):
        counts = inventory_master_catalog.write_to_firestore([_catalog_row()], dry_run=True)
        out = capsys.readouterr().out
        assert "... and" not in out
        assert counts["would_write"] == 1


class _FakeThoDb:
    def __init__(self, existing=None, raise_on=None):
        self._existing = existing
        self._raise_on = raise_on
        self.created: list[dict] = []
        self.updated: list[tuple] = []

    def get_inventory_by_serial(self, serial_number):
        if self._raise_on == "get_inventory_by_serial":
            raise RuntimeError("lookup failed")
        return self._existing

    def create_inventory(self, data):
        if self._raise_on == "create_inventory":
            raise RuntimeError("create failed")
        self.created.append(data)

    def update_inventory(self, inventory_id, data):
        self.updated.append((inventory_id, data))


class TestWriteToFirestoreApply:
    """L362-366: create-new path and per-row failure counter."""

    def test_creates_new_inventory(self, monkeypatch):
        db = _FakeThoDb(existing=None)
        monkeypatch.setitem(
            sys.modules, "database.firestore_client", types.SimpleNamespace(THODatabase=lambda: db)
        )

        counts = inventory_master_catalog.write_to_firestore([_catalog_row()], dry_run=False)

        assert counts == {"created": 1, "updated": 0, "failed": 0}
        assert db.created[0]["serial_number"] == "CAV-001"

    def test_row_failure_counts_failed(self, monkeypatch):
        db = _FakeThoDb(existing=None, raise_on="create_inventory")
        monkeypatch.setitem(
            sys.modules, "database.firestore_client", types.SimpleNamespace(THODatabase=lambda: db)
        )

        counts = inventory_master_catalog.write_to_firestore([_catalog_row()], dry_run=False)

        assert counts == {"created": 0, "updated": 0, "failed": 1}


def _write_catalog_xlsx(path: Path, rows: list[dict]) -> Path:
    pd.DataFrame(rows).to_excel(path, index=False)
    return path


_GOOD_ROWS = [
    {
        "Manufacturer": "Cavco",
        "Model": "Mountain Delight",
        "Serial #": "CAV-001",
        "Width": 16,
        "Length": 76,
        "MSRP": "$45,900",
    }
]


class TestMainCli:
    """L376-409: argparse validate + ingest plumbing."""

    def test_requires_dry_run_or_apply(self, tmp_path):
        xlsx = _write_catalog_xlsx(tmp_path / "m.xlsx", _GOOD_ROWS)
        with pytest.raises(SystemExit):
            inventory_master_catalog.main(["--xlsx", str(xlsx)])

    def test_missing_file_errors(self, tmp_path):
        with pytest.raises(SystemExit):
            inventory_master_catalog.main(["--xlsx", str(tmp_path / "nope.xlsx"), "--dry-run"])

    def test_dry_run_end_to_end(self, tmp_path, capsys):
        xlsx = _write_catalog_xlsx(tmp_path / "m.xlsx", _GOOD_ROWS)

        rc = inventory_master_catalog.main(["--xlsx", str(xlsx), "--dry-run", "-v"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "Parsed 1 rows" in out
        assert "cavco" in out
        assert "would_write" in out

    def test_returns_one_when_rows_fail(self, monkeypatch, tmp_path):
        xlsx = _write_catalog_xlsx(tmp_path / "m.xlsx", _GOOD_ROWS)
        monkeypatch.setattr(
            inventory_master_catalog,
            "write_to_firestore",
            lambda rows, dry_run: {"created": 0, "updated": 0, "failed": 1},
        )

        rc = inventory_master_catalog.main(["--xlsx", str(xlsx), "--apply"])

        assert rc == 1


# ---------------------------------------------------------------------------
# tools/floorplan_image_scan.py
# ---------------------------------------------------------------------------


class _ZeroPixelImage:
    size = (0, 0)

    def convert(self, *_args, **_kwargs):
        return self

    def thumbnail(self, *_args, **_kwargs):
        return None

    def getdata(self):
        return []


class TestAnalyzeEmptyPixels:
    """L93: a decoded image with zero pixels is treated as unreadable."""

    def test_zero_pixel_image_returns_none(self, monkeypatch):
        import PIL.Image as pil_image

        monkeypatch.setattr(pil_image, "open", lambda *a, **k: _ZeroPixelImage())
        monkeypatch.setattr(pil_image, "new", lambda *a, **k: _ZeroPixelImage())
        monkeypatch.setattr(pil_image, "alpha_composite", lambda a, b: _ZeroPixelImage())

        assert floorplan_image_scan.analyze_image_bytes(b"anything") is None


class TestFetch:
    """L110-115: _fetch success and failure paths."""

    def _fake_response(self, payload: bytes):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return payload

        return _Resp()

    def test_fetch_success(self, monkeypatch):
        monkeypatch.setattr(
            floorplan_image_scan.urllib.request,
            "urlopen",
            lambda req, timeout=None: self._fake_response(b"\x89PNG payload"),
        )
        assert floorplan_image_scan._fetch("https://cdn.example.com/a.png") == b"\x89PNG payload"

    def test_fetch_failure_returns_none(self, monkeypatch, caplog):
        def _boom(req, timeout=None):
            raise OSError("network down")

        monkeypatch.setattr(floorplan_image_scan.urllib.request, "urlopen", _boom)

        with caplog.at_level("DEBUG"):
            assert floorplan_image_scan._fetch("https://cdn.example.com/dead.png") is None
        assert "fetch failed" in caplog.text


class TestCollectPhotoUrls:
    """L124-134: dedup, order preservation, junk tolerance."""

    def test_dedupes_and_preserves_order(self):
        homes = [
            {
                "image_url": " https://cdn.example.com/a.jpg ",
                "hero_image": "https://cdn.example.com/a.jpg",
                "real_photos": ["https://cdn.example.com/b.jpg", "https://cdn.example.com/a.jpg"],
                "gallery_images": ["https://cdn.example.com/c.jpg"],
                "photos": None,
            },
            {"image_url": ["https://cdn.example.com/d.jpg", "https://cdn.example.com/b.jpg"]},
            "not-a-dict",
            {"image_url": 42, "hero_image": ""},
        ]

        urls = floorplan_image_scan.collect_photo_urls(homes)

        assert urls == [
            "https://cdn.example.com/a.jpg",
            "https://cdn.example.com/b.jpg",
            "https://cdn.example.com/c.jpg",
            "https://cdn.example.com/d.jpg",
        ]


class _JsonResponse:
    def __init__(self, payload: dict | list):
        self._raw = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self._raw

    def __exit__(self, *args):
        return False


class TestLoadHomes:
    """L170-177: http vs file source."""

    def test_http_source_appends_inventory_path(self, monkeypatch):
        captured = {}

        def _fake_urlopen(url, timeout=None):
            captured["url"] = url
            return _JsonResponse({"homes": [{"id": "1"}, "junk", {"id": "2"}]})

        monkeypatch.setattr(floorplan_image_scan.urllib.request, "urlopen", _fake_urlopen)

        homes = floorplan_image_scan._load_homes("https://texashomeoutlet.com/")

        assert captured["url"] == "https://texashomeoutlet.com/api/marketing/inventory-context"
        assert homes == [{"id": "1"}, {"id": "2"}]

    def test_file_source_with_dict_payload(self, tmp_path):
        src = tmp_path / "inventory.json"
        src.write_text(json.dumps({"homes": [{"id": "a"}]}))

        assert floorplan_image_scan._load_homes(str(src)) == [{"id": "a"}]

    def test_file_source_with_list_payload_and_none_homes(self, tmp_path):
        src = tmp_path / "inventory.json"
        src.write_text(json.dumps([{"id": "a"}, {"id": "b"}]))

        assert floorplan_image_scan._load_homes(str(src)) == [{"id": "a"}, {"id": "b"}]


class TestFloorplanMainCli:
    """L181-211: argparse + orchestration."""

    def _patch_io(self, monkeypatch, homes):
        monkeypatch.setattr(floorplan_image_scan, "_load_homes", lambda source: homes)
        monkeypatch.setattr(
            floorplan_image_scan,
            "build_manifest",
            lambda urls, out_path=None, max_workers=12: {
                "generated_at": "now",
                "scanned": len(urls),
                "unreadable": 0,
                "urls": urls[:1],
            },
        )

    def test_dry_run_reports_without_writing(self, monkeypatch, tmp_path, capsys):
        self._patch_io(monkeypatch, [{"image_url": "https://cdn.example.com/a.jpg"}])
        out = tmp_path / "manifest.json"

        rc = floorplan_image_scan.main(
            ["--source", "whatever", "--out", str(out), "--dry-run"]
        )

        assert rc == 0
        captured = capsys.readouterr().out
        assert "candidate photo URLs: 1" in captured
        assert "Dry run — nothing written" in captured
        assert not out.exists()

    def test_write_mode_reports_manifest_path(self, monkeypatch, tmp_path, capsys):
        self._patch_io(monkeypatch, [{"image_url": "https://cdn.example.com/a.jpg"}])
        out = tmp_path / "manifest.json"

        rc = floorplan_image_scan.main(["--source", "whatever", "--out", str(out)])

        assert rc == 0
        captured = capsys.readouterr().out
        assert f"Wrote {out}" in captured

    def test_limit_slices_urls_before_scan(self, monkeypatch, capsys):
        seen = {}

        def _fake_build(urls, out_path=None, max_workers=12):
            seen["urls"] = urls
            return {"generated_at": "now", "scanned": len(urls), "unreadable": 0, "urls": []}

        monkeypatch.setattr(
            floorplan_image_scan,
            "_load_homes",
            lambda source: [{"image_url": f"https://cdn.example.com/{i}.jpg"} for i in range(5)],
        )
        monkeypatch.setattr(floorplan_image_scan, "build_manifest", _fake_build)

        floorplan_image_scan.main(["--source", "x", "--dry-run", "--limit", "2"])

        assert seen["urls"] == ["https://cdn.example.com/0.jpg", "https://cdn.example.com/1.jpg"]


# ---------------------------------------------------------------------------
# tools/inventory_sync.py
# ---------------------------------------------------------------------------


class TestGcpImportFallback:
    """L41-43: google.cloud.firestore missing -> scrape-only mode."""

    def test_gcp_unavailable_falls_back_to_scrape_only(self, capsys):
        stub = types.ModuleType("google.cloud")

        def _raising(name):
            if name.startswith("__"):
                raise AttributeError(name)
            raise ImportError(f"No module named {name!r}")

        stub.__getattr__ = _raising
        real_cloud = sys.modules.get("google.cloud")
        sys.modules["google.cloud"] = stub
        try:
            importlib.reload(inventory_sync)
        finally:
            if real_cloud is not None:
                sys.modules["google.cloud"] = real_cloud
            importlib.reload(inventory_sync)

        assert inventory_sync.GCP_AVAILABLE is True  # restored by second reload
        assert "google-cloud-firestore not installed" in capsys.readouterr().out


class TestInitFirestore:
    """L257, 261-267: _init_firestore success and failure."""

    def test_success_connects_and_logs_project(self, monkeypatch, caplog):
        seen = {}

        class _Client:
            def __init__(self, project=None):
                seen["project"] = project

        monkeypatch.delenv("FIRESTORE_PROJECT_ID", raising=False)
        monkeypatch.setattr(inventory_sync.firestore, "Client", _Client)

        with caplog.at_level("INFO"):
            sync = inventory_sync.InventorySync(dry_run=False)

        assert seen["project"] == "tho-ai-agent"
        assert sync.db is not None
        assert "Connected to Firestore project: tho-ai-agent" in caplog.text

    def test_failure_logs_and_leaves_db_none(self, monkeypatch, caplog):
        class _Client:
            def __init__(self, project=None):
                raise RuntimeError("no ADC")

        monkeypatch.setattr(inventory_sync.firestore, "Client", _Client)

        with caplog.at_level("ERROR"):
            sync = inventory_sync.InventorySync(dry_run=False)

        assert sync.db is None
        assert "Failed to initialize Firestore" in caplog.text


class TestExtractCardGaps:
    """L341, 395, 418-421, 427, 451-453: card extraction edge branches."""

    def _sync(self):
        return inventory_sync.InventorySync(dry_run=True)

    def _card(self, html):
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "html.parser").select_one(".fp-card-container")

    def test_card_without_title_returns_none(self):
        card = self._card('<div class="fp-card-container"><div class="fp-card-image"></div></div>')
        assert self._sync()._extract_card_data(card) is None

    def test_relative_image_url_gets_origin_prefix(self):
        card = self._card(
            """
            <div class="fp-card-container">
              <div class="fp-card-image" data-bg="/dealer/3522/inventory/28102/card.jpg"></div>
              <h4 class="home-name"><a href="/inventory-detail/28102/x/">Model A</a></h4>
            </div>
            """
        )
        home = self._sync()._extract_card_data(card)
        assert home["image_url"] == "https://www.texashomeoutlet.com/dealer/3522/inventory/28102/card.jpg"

    def test_price_and_year_extracted(self):
        card = self._card(
            """
            <div class="fp-card-container">
              <h4 class="home-name"><a href="/inventory-detail/28102/x/">2023 Fleetwood Breeze</a></h4>
              <div class="fp-card-price">Sale Price: $89,900</div>
            </div>
            """
        )
        home = self._sync()._extract_card_data(card)
        assert home["price"] == 89900.0
        assert home["year"] == 2023

    def test_unexpected_error_returns_none(self, caplog):
        card = self._card('<h4 class="home-name"><a>No href attr</a></h4>')
        with caplog.at_level("ERROR"):
            assert self._sync()._extract_card_data(card) is None
        assert "Error extracting card" in caplog.text


class TestPlanIdAndTransformGaps:
    """L557 (CDN photo build) and the L523-524 dead branch."""

    def test_without_the_direct_lookup_is_unreachable_by_construction(self):
        # Proof, not a coverage hack: _get_plan_id does
        # ``model_lower.replace("the ", "")`` which strips EVERY occurrence of
        # "the ". The result therefore never contains "the " and can never
        # equal a KNOWN_MODEL_PLAN_IDS key — every key starts with "the ".
        # The ``if model_without_the in KNOWN_MODEL_PLAN_IDS`` branch (L523-524)
        # is dead code for any input.
        from tools.inventory_sync import KNOWN_MODEL_PLAN_IDS

        for key in KNOWN_MODEL_PLAN_IDS:
            assert "the " in key  # premise: every key contains the article
            assert key.replace("the ", "").strip() not in KNOWN_MODEL_PLAN_IDS

    def test_transform_builds_cdn_photos_for_known_plan(self):
        sync = inventory_sync.InventorySync(dry_run=True)
        item = sync.transform_to_inventory_item(
            {
                "id": "77",
                "model_name": "The Big Steve",
                "manufacturer": "New Vision Manufacturing",
                "specs": {"beds": 3, "baths": 2.0, "sq_ft": 1216, "width": 32, "length": 56},
                "price": 125000.0,
                "year": 2026,
                "is_new": True,
                "image_url": "https://cdn.example.com/hero.jpg",
                "floorplan_url": None,
                "matterport_id": None,
                "tags": [],
                "detail_url": "https://www.texashomeoutlet.com/inventory-detail/77/x/",
            }
        )
        assert item.plan_id == "225053"
        assert len(item.photos) == 21
        assert item.photos[0].endswith("/manufacturer/3335/floorplan/225053/1.jpg")


class TestSyncGaps:
    """L640 (dry-run overflow), L644-645 (no db), L697-699 (transform error)."""

    def _item(self, sync, item_id="99"):
        return sync.transform_to_inventory_item(
            {
                "id": item_id,
                "model_name": "The Delight",
                "manufacturer": "TRU",
                "specs": {"beds": 2, "baths": 2.0, "sq_ft": 820, "width": 14, "length": 60},
                "price": 89900.0,
                "year": 2026,
                "is_new": True,
                "image_url": None,
                "floorplan_url": None,
                "matterport_id": None,
                "tags": [],
                "detail_url": "",
            }
        )

    def test_dry_run_logs_overflow(self, caplog):
        sync = inventory_sync.InventorySync(dry_run=True)
        with caplog.at_level("INFO"):
            sync.sync_to_firestore([self._item(sync, str(i)) for i in range(5)])
        assert "... and 2 more" in caplog.text

    def test_not_dry_run_without_db_aborts(self, caplog):
        sync = inventory_sync.InventorySync(dry_run=True)
        sync.dry_run = False
        assert sync.db is None
        with caplog.at_level("ERROR"):
            sync.sync_to_firestore([self._item(sync)])
        assert "Firestore not available" in caplog.text

    def test_transform_error_counts_and_continues(self, monkeypatch, tmp_path, caplog):
        monkeypatch.chdir(tmp_path)
        sync = inventory_sync.InventorySync(dry_run=True)
        monkeypatch.setattr(
            sync, "scrape_website_inventory", lambda: [{"specs": {}}]  # no model_name -> KeyError
        )
        with caplog.at_level("ERROR"):
            stats = sync.run_sync()
        assert stats["errors"] == 1


class TestMainEntrypointSync:
    """L735-753 + L757: argparse plumbing under runpy; scrape failure -> exit 0."""

    def test_main_dry_run_scrape_failure_exits_zero(self, monkeypatch):
        def _boom(url, headers=None, timeout=None):
            raise ConnectionError("site unreachable")

        monkeypatch.setattr(inventory_sync.requests, "get", _boom)
        monkeypatch.setattr(
            sys, "argv", ["inventory_sync.py", "--dry-run", "--force", "--scrape-only"]
        )

        import runpy

        with pytest.raises(SystemExit) as exc_info:
            runpy.run_path(str(Path(inventory_sync.__file__).resolve()), run_name="__main__")

        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# tools/image_storage.py
# ---------------------------------------------------------------------------


class _FakeGcsBlob:
    def __init__(self, payload: bytes | None = b"{}", exists: bool = True, fail_upload=False):
        self.name = ""
        self.size = len(payload) if payload else 0
        self.content_type = None
        self.cache_control = None
        self._payload = payload
        self._exists = exists
        self._fail_upload = fail_upload
        self.uploaded: list[bytes] = []
        self.deleted = False

    def upload_from_string(self, data, **kwargs):
        if self._fail_upload:
            raise RuntimeError("provider write failed")
        self.uploaded.append(data)
        self._payload = data
        self._exists = True

    def download_as_bytes(self):
        if self._payload is None:
            raise RuntimeError("provider read failed")
        return self._payload

    def exists(self):
        return self._exists

    def delete(self):
        self.deleted = True
        self._exists = False


class _FakeGcsBucket:
    """Buckets by blob name; list_blobs serves a fixed object inventory."""

    def __init__(self, blobs: dict[str, _FakeGcsBlob] | None = None, listing: list | None = None):
        self._blobs = blobs if blobs is not None else {}
        self._listing = listing

    def blob(self, name):
        if name not in self._blobs:
            # Auto-created blobs mirror GCS: the object does not exist until
            # something writes it.
            self._blobs[name] = _FakeGcsBlob(b"[]", exists=False)
        blob = self._blobs[name]
        blob.name = name
        return blob

    def list_blobs(self, prefix=""):
        if self._listing is not None:
            return list(self._listing)
        return [b for name, b in self._blobs.items() if name.startswith(prefix) and b.exists()]


def _reset_gcs_state(monkeypatch):
    """Un-latch module globals and let the real _get_bucket probe GCS."""
    monkeypatch.delenv("THO_DISABLE_GCS_UPLOADS", raising=False)
    monkeypatch.setattr(image_storage, "_gcs_unavailable", False)
    monkeypatch.setattr(image_storage, "_gcs_bucket", None)
    monkeypatch.setattr(image_storage, "_gcs_client", None)


def _enable_fake_gcs(monkeypatch, bucket):
    """Point _get_bucket at a fake bucket and un-latch the module globals."""
    _reset_gcs_state(monkeypatch)
    monkeypatch.setattr(image_storage, "_get_bucket", lambda: bucket)


class TestValidateImageBytesGaps:
    """L165, 168, 185: frame/pixel budgets and declared-type mismatch."""

    def test_too_many_frames_rejected(self, monkeypatch):
        monkeypatch.setattr(image_storage, "MAX_IMAGE_FRAMES", 1)
        with pytest.raises(image_storage.PhotoValidationError, match="too many animation frames"):
            image_storage.validate_image_bytes(_gif_bytes(frames=2), "image/gif")

    def test_oversized_frame_rejected(self, monkeypatch):
        monkeypatch.setattr(image_storage, "MAX_IMAGE_PIXELS", 10)
        with pytest.raises(image_storage.PhotoValidationError, match="dimensions are too large"):
            image_storage.validate_image_bytes(_gif_bytes(frames=1), "image/gif")

    def test_detected_type_must_match_declared(self):
        with pytest.raises(
            image_storage.PhotoValidationError, match="does not match its content"
        ):
            image_storage.validate_image_bytes(_jpeg_bytes(), "image/png")


class TestProcessImageGaps:
    """L210 (palette JPEG conversion) and L215 (WEBP branch)."""

    def test_palette_image_converted_for_jpeg(self):
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("P", (32, 32), 1).save(buffer, format="PNG")
        out = image_storage.process_image(buffer.getvalue(), "image/jpeg")
        with Image.open(io.BytesIO(out)) as im:
            assert im.format == "JPEG"
            assert im.mode == "RGB"

    def test_webp_branch(self):
        pytest.importorskip("PIL")
        from PIL import Image

        try:
            buffer = io.BytesIO()
            Image.new("RGB", (32, 32), "green").save(buffer, format="WEBP")
        except OSError:
            pytest.skip("WEBP support not compiled into this Pillow build")
        out = image_storage.process_image(buffer.getvalue(), "image/webp")
        with Image.open(io.BytesIO(out)) as im:
            assert im.format == "WEBP"


class TestGetBucket:
    """L267-283: GCS client init success, caching, and exception paths."""

    def _fake_storage_module(self, monkeypatch, client_cls):
        import google.cloud

        fake_storage = types.ModuleType("google.cloud.storage")
        fake_storage.Client = client_cls
        monkeypatch.setitem(sys.modules, "google.cloud.storage", fake_storage)
        monkeypatch.setattr(google.cloud, "storage", fake_storage, raising=False)

    def test_initializes_client_once_and_caches_bucket(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        _reset_gcs_state(monkeypatch)
        calls = []

        class _Client:
            def __init__(self):
                calls.append(1)

            def bucket(self, name):
                assert name == "tho-listing-photos"
                return sentinel_bucket

        sentinel_bucket = _FakeGcsBucket()
        self._fake_storage_module(monkeypatch, _Client)

        assert image_storage._get_bucket() is sentinel_bucket
        assert image_storage._get_bucket() is sentinel_bucket
        assert len(calls) == 1

    def test_init_failure_latches_unavailable_outside_cloud_run(self, monkeypatch, caplog, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        _reset_gcs_state(monkeypatch)

        class _Client:
            def __init__(self):
                raise RuntimeError("no ADC")

        self._fake_storage_module(monkeypatch, _Client)

        with caplog.at_level("WARNING"):
            assert image_storage._get_bucket() is None

        assert image_storage._gcs_unavailable is True  # not durable -> latch

    def test_init_failure_does_not_latch_on_cloud_run(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        _reset_gcs_state(monkeypatch)
        monkeypatch.setenv("K_SERVICE", "project-go-forward")

        class _Client:
            def __init__(self):
                raise RuntimeError("transient")

        self._fake_storage_module(monkeypatch, _Client)

        assert image_storage._get_bucket() is None
        assert image_storage._gcs_unavailable is False  # prod must be able to recover


class TestStorePhotoGcsPath:
    """L335-336: successful GCS upload returns a gcs-backed StoredPhoto."""

    def test_gcs_upload_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        bucket = _FakeGcsBucket()
        _enable_fake_gcs(monkeypatch, bucket)

        photo = image_storage.store_photo(
            "43372", PNG_BYTES, original_name="front.png", declared_content_type="image/png"
        )

        assert photo.backend == "gcs"
        assert photo.url == f"/api/inventory/photos/43372/{photo.filename}"
        blob = bucket.blob(f"listing_photos/43372/{photo.filename}")
        assert blob.cache_control == "public, max-age=86400"
        # process_image re-encodes the PNG, so compare the magic signature and
        # the stored length rather than the raw fixture bytes.
        assert len(blob.uploaded) == 1
        assert blob.uploaded[0][:8] == PNG_BYTES[:8]
        assert blob.uploaded[0] and photo.size_bytes == len(blob.uploaded[0])
        # Nothing may leak to the ephemeral local fallback.
        assert list(tmp_path.rglob("*")) == []


class TestReadOrderGaps:
    """L393-394 (schema), L396 (fall-through after read error), L399 (durable),
    L405-406 (corrupt local sidecar)."""

    def test_gcs_schema_invalid_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        bucket = _FakeGcsBucket(blobs={"listing_photos/43372/_order.json": _FakeGcsBlob(b'{"a": 1}')})
        _enable_fake_gcs(monkeypatch, bucket)

        assert image_storage._read_order("43372") == []

    def test_gcs_read_error_falls_back_to_local(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        bucket = _FakeGcsBucket(
            blobs={"listing_photos/43372/_order.json": _FakeGcsBlob(None)}
        )
        _enable_fake_gcs(monkeypatch, bucket)
        home_dir = tmp_path / "43372"
        home_dir.mkdir()
        (home_dir / "_order.json").write_text(json.dumps(["local-first.jpg"]))

        # Non-durable: provider read error must not raise; local sidecar wins.
        assert image_storage._read_order("43372") == ["local-first.jpg"]

    def test_bucket_none_on_cloud_run_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        monkeypatch.setenv("K_SERVICE", "project-go-forward")
        _enable_fake_gcs(monkeypatch, None)

        with pytest.raises(image_storage.PhotoStorageError, match="Durable"):
            image_storage._read_order("43372")

    def test_corrupt_local_sidecar_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        home_dir = tmp_path / "43372"
        home_dir.mkdir()
        (home_dir / "_order.json").write_text("{not json")

        assert image_storage._read_order("43372") == []


class TestWriteOrderGaps:
    """L426: GCS write error outside Cloud Run falls back to the local sidecar."""

    def test_write_error_falls_back_to_local(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        bucket = _FakeGcsBucket(
            blobs={
                "listing_photos/43372/_order.json": _FakeGcsBlob(b"[]", fail_upload=True),
            }
        )
        _enable_fake_gcs(monkeypatch, bucket)

        image_storage._write_order("43372", ["a.jpg", "b.jpg"])

        assert (tmp_path / "43372" / "_order.json").read_bytes() == b'["a.jpg", "b.jpg"]'

    def test_bucket_none_on_cloud_run_raises_before_local_write(self, monkeypatch, tmp_path):
        # L425-426: durable storage required and GCS unavailable -> fail closed
        # rather than persist display order on the ephemeral local disk.
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        monkeypatch.setenv("K_SERVICE", "project-go-forward")
        _enable_fake_gcs(monkeypatch, None)

        with pytest.raises(image_storage.PhotoStorageError, match="Durable"):
            image_storage._write_order("43372", ["a.jpg"])

        assert list(tmp_path.rglob("*")) == []


class TestListPhotosGaps:
    """L456: blobs with empty names and the order sidecar are skipped."""

    def test_skips_order_file_and_empty_blob_names(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        listing = [
            _named_blob("listing_photos/43372/front.jpg", 10),
            _named_blob("listing_photos/43372/_order.json", 2),
            _named_blob("listing_photos/43372/", 0),
        ]
        bucket = _FakeGcsBucket(listing=listing)
        _enable_fake_gcs(monkeypatch, bucket)

        photos = image_storage.list_photos("43372")

        assert [p.filename for p in photos] == ["front.jpg"]


def _named_blob(name: str, size: int) -> _FakeGcsBlob:
    blob = _FakeGcsBlob(b"x" * size)
    blob.name = name
    return blob


class TestGetPhotoGcsPaths:
    """L505-508 (hit) and fall-through to local after a provider error (L509-516)."""

    def test_gcs_hit_returns_bytes_and_sniffed_type(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        payload = b"\xff\xd8\xff" + b"jpeg-bytes"
        bucket = _FakeGcsBucket(
            blobs={"listing_photos/43372/photo.jpg": _FakeGcsBlob(payload)}
        )
        bucket.blob("listing_photos/43372/photo.jpg").content_type = None
        _enable_fake_gcs(monkeypatch, bucket)

        result = image_storage.get_photo("43372", "photo.jpg")

        assert result == (payload, "image/jpeg")

    def test_gcs_error_falls_back_to_local_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        bucket = _FakeGcsBucket(
            blobs={"listing_photos/43372/photo.png": _FakeGcsBlob(None)}
        )
        _enable_fake_gcs(monkeypatch, bucket)
        home_dir = tmp_path / "43372"
        home_dir.mkdir()
        (home_dir / "photo.png").write_bytes(PNG_BYTES)

        assert image_storage.get_photo("43372", "photo.png") == (PNG_BYTES, "image/png")

    def test_gcs_miss_falls_through_to_local(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        _enable_fake_gcs(monkeypatch, _FakeGcsBucket())
        home_dir = tmp_path / "43372"
        home_dir.mkdir()
        (home_dir / "photo.png").write_bytes(PNG_BYTES)

        assert image_storage.get_photo("43372", "photo.png") == (PNG_BYTES, "image/png")

    def test_gcs_miss_on_cloud_run_returns_none_without_touching_local(self, monkeypatch, tmp_path):
        # L515-516: with durable storage required, a GCS miss must return None
        # rather than consult the ephemeral local filesystem.
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        monkeypatch.setenv("K_SERVICE", "project-go-forward")
        _enable_fake_gcs(monkeypatch, _FakeGcsBucket())
        home_dir = tmp_path / "43372"
        home_dir.mkdir()
        (home_dir / "photo.png").write_bytes(PNG_BYTES)

        assert image_storage.get_photo("43372", "photo.png") is None


class TestListAllGroupedGaps:
    """L582 (cache hit), L588-596 (GCS grouping), L611 (non-dir entries skipped)."""

    def test_cache_hit_returns_same_object(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        image_storage.store_photo("43372", PNG_BYTES, declared_content_type="image/png")

        image_storage.list_all_grouped(use_cache=False)
        cached = image_storage.list_all_grouped(use_cache=True)

        assert cached is image_storage._grouped_cache

    def test_gcs_grouping_skips_sidecar_and_shallow_names(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        listing = [
            _named_blob("listing_photos/home1/a.jpg", 5),
            _named_blob("listing_photos/home1/_order.json", 2),
            _named_blob("listing_photos/shallow", 3),  # len(parts) < 3
            _named_blob("listing_photos/home2/", 0),  # empty filename
        ]
        bucket = _FakeGcsBucket(listing=listing)
        _enable_fake_gcs(monkeypatch, bucket)

        grouped = image_storage.list_all_grouped(use_cache=False)

        assert set(grouped) == {"home1"}
        assert [p.filename for p in grouped["home1"]] == ["a.jpg"]

    def test_local_walk_skips_non_directory_entries(self, monkeypatch, tmp_path):
        monkeypatch.setattr(image_storage, "_LOCAL_DIR", str(tmp_path))
        (tmp_path / "stray-file.txt").write_text("not a home dir")
        image_storage.store_photo("home1", PNG_BYTES, declared_content_type="image/png")

        grouped = image_storage.list_all_grouped(use_cache=False)

        assert set(grouped) == {"home1"}


# ---------------------------------------------------------------------------
# tools/inventory_seed.py
# ---------------------------------------------------------------------------


class TestCoercionGaps:
    """L45-46, 48, 55, 58-59, 61: _to_int/_to_number rejection branches."""

    def test_to_int_bad_values(self):
        assert inventory_seed._to_int("abc") is None
        assert inventory_seed._to_int([1]) is None
        assert inventory_seed._to_int(0) is None
        assert inventory_seed._to_int("-3") is None

    def test_to_number_empty_string(self):
        assert inventory_seed._to_number("") is None

    def test_to_number_bad_value(self):
        assert inventory_seed._to_number("abc") is None

    def test_to_number_zero_or_negative(self):
        assert inventory_seed._to_number(0) is None
        assert inventory_seed._to_number(-1.5) is None


class TestIsNewTagsOnly:
    """L76: marketing tags alone can flag pre-owned."""

    def test_tags_only_marks_preowned(self):
        assert inventory_seed._is_new({"status": "Available", "marketing_tags": ["Pre-Owned"]}) is False
        assert inventory_seed._is_new({"status": "Available", "marketing_tags": ["Used Lot Model"]}) is False
        assert inventory_seed._is_new({"status": "Available", "marketing_tags": []}) is True


class _FakeCrawlerModule(types.ModuleType):
    def __init__(self, context):
        super().__init__("tools.legacy_site_crawler")
        self.calls: list[int] = []
        self._context = context

        def load_legacy_inventory_context(limit=1000):
            self.calls.append(limit)
            return self._context

        self.load_legacy_inventory_context = load_legacy_inventory_context


class TestLoadSnapshotHomesGaps:
    """L131-136: default loader import, limit kwarg, None context."""

    def test_default_loader_uses_limit_kwarg(self, monkeypatch):
        fake = _FakeCrawlerModule({"homes": [{"id": "1"}]})
        monkeypatch.setitem(sys.modules, "tools.legacy_site_crawler", fake)

        homes = inventory_seed.load_snapshot_homes(limit=42)

        assert homes == [{"id": "1"}]
        assert fake.calls == [42]

    def test_none_context_yields_empty(self, monkeypatch):
        fake = _FakeCrawlerModule(None)
        monkeypatch.setitem(sys.modules, "tools.legacy_site_crawler", fake)

        assert inventory_seed.load_snapshot_homes() == []

    def test_missing_homes_key_yields_empty(self, monkeypatch):
        fake = _FakeCrawlerModule({"other": []})
        monkeypatch.setitem(sys.modules, "tools.legacy_site_crawler", fake)

        assert inventory_seed.load_snapshot_homes() == []


class _RecordingDb:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    def upsert_inventory(self, doc_id, data):
        self.docs[doc_id] = data


def _seed_home(i: int) -> dict:
    return {
        "id": f"home-{i}",
        "legacy_inventory_id": f"home-{i}",
        "model_name": f"Model {i}",
        "status": "Available",
        "specs": {"beds": 2, "baths": 2.0},
        "price_value": 50000 + i,
    }


class TestMainCliSeed:
    """L171-194: dry-run/apply modes, limit, planned-list preview cap."""

    def _patch_homes(self, monkeypatch, count):
        monkeypatch.setattr(
            inventory_seed, "load_snapshot_homes", lambda: [_seed_home(i) for i in range(count)]
        )

    def test_dry_run_previews_cap_at_25(self, monkeypatch, capsys):
        self._patch_homes(monkeypatch, 30)

        rc = inventory_seed.main([])

        assert rc == 0
        out = capsys.readouterr().out
        assert "[DRY-RUN]" in out
        assert "written: 0" in out
        assert out.count("would upsert") == 25
        assert "... and 5 more" in out
        assert "Re-run with --apply" in out

    def test_apply_writes_to_firestore(self, monkeypatch):
        self._patch_homes(monkeypatch, 3)
        db = _RecordingDb()
        monkeypatch.setitem(
            sys.modules, "database.firestore_client", types.SimpleNamespace(get_database=lambda: db)
        )

        rc = inventory_seed.main(["--apply"])

        assert rc == 0
        assert set(db.docs) == {"home-0", "home-1", "home-2"}

    def test_limit_caps_processed_homes(self, monkeypatch, capsys):
        self._patch_homes(monkeypatch, 5)

        inventory_seed.main(["--limit", "2"])

        out = capsys.readouterr().out
        assert "snapshot homes: 2" in out
