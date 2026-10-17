"""Coverage mop-up for the c12-b CRM / data-pipeline batch.

Targets (measured 3.11-term-missing baseline, worktree agent/coverage-c12-b):

  * tools/lead_name_backfill.py — module is loaded by the existing test file via
    ``spec_from_file_location`` (name "lead_name_backfill"), so it never
    registers under the ``tools.lead_name_backfill`` coverage module. These
    tests import it through the ``tools`` package so the coverage module is
    actually exercised: collect_patches (lazy chat_history/lead_management
    imports), apply_patches, extract_name_with_llm (genai faked), _build_parser,
    _main, main, and the ``__main__`` guard via in-process runpy.
  * tools/lead_nurture.py — _get_db lazy Firestore import (success + failure),
    _now_utc, _coerce_dt branches, _last_contact None, find_stale_leads
    clamps/fallbacks/exception path, _log_nurture_send no-db + write-failure,
    run_nurture_batch send exception path.
  * tools/listing_urls.py — stock_id / is_instock_home guards, match by stock
    id, listing_page_images fallbacks, attach_listing_urls non-dict skip,
    _parse_date branches, _url_list, listing_photo_subject empty stem,
    path_from_url.
  * tools/house_orders_sync.py — _is_home_serial empty, _iter_home_rows blank
    model, house_order_row_to_inventory_doc rejection, parse_house_orders on a
    real xlsx fixture (pandas), load_house_orders_from_gcs OSError cleanup,
    main() dry-run / apply / planned-list truncation.

All tests hermetic: Firestore / GCS / genai / email_service are faked via
sys.modules (plus parent-package attribute patches where the import is
``from pkg import submodule``). No network.
"""

from __future__ import annotations

import builtins
import json
import os
import runpy
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools import house_orders_sync as hos
from tools import lead_name_backfill as lnb
from tools import lead_nurture
from tools import listing_urls as lnu

REPO_ROOT = Path(__file__).parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# tools/lead_name_backfill.py
# ─────────────────────────────────────────────────────────────────────────────


class _FakeLead:
    def __init__(self, lead_id, name=None, session_id=None, user_id=None):
        self.lead_id = lead_id
        self.name = name
        self.session_id = session_id
        self.user_id = user_id


class _FakeSession:
    def __init__(self, session_id, messages):
        self.session_id = session_id
        self.messages = messages


def _install_crm_fakes(monkeypatch, leads, sessions=None, user_sessions=None):
    """Fake the heavy ROOT-level modules chat_history / lead_management.

    collect_patches / apply_patches import them lazily inside the function
    (``from chat_history import ChatHistory``), so a sys.modules entry is
    sufficient — the real modules (and their Firestore constructors) are
    never touched.
    """

    sessions = sessions or {}
    user_sessions = user_sessions or {}

    class FakeChatHistory:
        def __init__(self):
            self.get_session_calls: list[str] = []

        async def get_session(self, session_id):
            self.get_session_calls.append(session_id)
            value = sessions.get(session_id)
            if isinstance(value, Exception):
                raise value
            return value

        async def get_user_sessions(self, user_id, limit=3):
            return user_sessions.get(user_id, [])

    class FakeLeadManager:
        def __init__(self):
            self.updated: list[_FakeLead] = []
            self.existing: dict[str, _FakeLead] = {}

        async def list_leads(self, limit=None):
            return list(leads)

        async def get_lead(self, lead_id):
            return self.existing.get(lead_id)

        async def update_lead(self, lead):
            self.updated.append(lead)

    chat_mod = types.ModuleType("chat_history")
    chat_mod.ChatHistory = FakeChatHistory
    lead_mod = types.ModuleType("lead_management")
    lead_mod.LeadManager = FakeLeadManager
    monkeypatch.setitem(sys.modules, "chat_history", chat_mod)
    monkeypatch.setitem(sys.modules, "lead_management", lead_mod)
    return FakeChatHistory, FakeLeadManager


class TestCollectPatches:
    @pytest.mark.asyncio
    async def test_collects_patch_from_session_on_lead(self, monkeypatch):
        lead = _FakeLead("L1", name="Unknown", session_id="S1", user_id="U1")
        _install_crm_fakes(monkeypatch, [lead], sessions={"S1": _FakeSession("S1", [
            {"role": "user", "text": "Hi, my name is Jane Doe"},
        ])})

        patches = await lnb.collect_patches()

        assert len(patches) == 1
        assert patches[0].lead_id == "L1"
        assert patches[0].proposed_name == "Jane Doe"
        assert patches[0].confidence == 0.95
        assert patches[0].current_name == "Unknown"
        assert "Jane Doe" in patches[0].source_message_excerpt

    @pytest.mark.asyncio
    async def test_falls_back_to_user_sessions_picking_most_messages(self, monkeypatch):
        lead = _FakeLead("L2", name="", session_id="missing", user_id="U2")
        _install_crm_fakes(
            monkeypatch,
            [lead],
            sessions={"missing": KeyError("no such session")},
            user_sessions={
                "U2": [
                    _FakeSession("S-old", [{"role": "user", "text": "hello"}]),
                    _FakeSession(
                        "S-busy",
                        [
                            {"role": "user", "text": "hi"},
                            {"role": "user", "text": "call me Bob Smith"},
                        ],
                    ),
                ]
            },
        )

        patches = await lnb.collect_patches()

        assert len(patches) == 1
        assert patches[0].source_session_id == "S-busy"
        assert patches[0].proposed_name == "Bob Smith"

    @pytest.mark.asyncio
    async def test_user_session_lookup_failure_is_tolerated(self, monkeypatch):
        lead = _FakeLead("L3", name="Unknown", session_id="S-raise", user_id="U3")

        class _RaisingChatHistory:
            async def get_session(self, session_id):
                raise RuntimeError("boom")

            async def get_user_sessions(self, user_id, limit=3):
                raise RuntimeError("boom again")

        chat_mod = types.ModuleType("chat_history")
        chat_mod.ChatHistory = _RaisingChatHistory
        lead_mod = types.ModuleType("lead_management")

        class _LM:
            async def list_leads(self, limit=None):
                return [lead]

        lead_mod.LeadManager = _LM
        monkeypatch.setitem(sys.modules, "chat_history", chat_mod)
        monkeypatch.setitem(sys.modules, "lead_management", lead_mod)

        assert await lnb.collect_patches() == []

    @pytest.mark.asyncio
    async def test_lead_without_any_session_is_skipped(self, monkeypatch):
        lead = _FakeLead("L4", name="Unknown", session_id=None, user_id=None)
        _install_crm_fakes(monkeypatch, [lead])
        assert await lnb.collect_patches() == []

    @pytest.mark.asyncio
    async def test_session_with_no_messages_is_skipped(self, monkeypatch):
        lead = _FakeLead("L5", name="Unknown", session_id="S-empty", user_id=None)
        _install_crm_fakes(
            monkeypatch, [lead], sessions={"S-empty": _FakeSession("S-empty", [])}
        )
        assert await lnb.collect_patches() == []

    @pytest.mark.asyncio
    async def test_llm_fallback_used_when_regex_finds_nothing(self, monkeypatch):
        lead = _FakeLead("L6", name="Unknown", session_id="S6", user_id=None)
        _install_crm_fakes(
            monkeypatch,
            [lead],
            sessions={"S6": _FakeSession("S6", [{"role": "user", "text": "just looking" }])},
        )

        async def _fake_llm(messages):
            return ("Zelda Fitzgerald", 0.72, "excerpt")

        monkeypatch.setattr(lnb, "extract_name_with_llm", _fake_llm)

        patches = await lnb.collect_patches(use_llm=True)

        assert len(patches) == 1
        assert patches[0].proposed_name == "Zelda Fitzgerald"
        assert patches[0].confidence == 0.72

    @pytest.mark.asyncio
    async def test_no_name_found_without_llm_is_skipped(self, monkeypatch):
        lead = _FakeLead("L7", name="Unknown", session_id="S7", user_id=None)
        _install_crm_fakes(
            monkeypatch,
            [lead],
            sessions={"S7": _FakeSession("S7", [{"role": "user", "text": "just looking"}])},
        )
        assert await lnb.collect_patches(use_llm=False) == []

    @pytest.mark.asyncio
    async def test_limit_is_forwarded_to_list_leads(self, monkeypatch):
        lead = _FakeLead("L8", name="Unknown", session_id=None, user_id=None)
        seen = {}

        class _LM:
            async def list_leads(self, limit=None):
                seen["limit"] = limit
                return [lead]

        lead_mod = types.ModuleType("lead_management")
        lead_mod.LeadManager = _LM
        monkeypatch.setitem(sys.modules, "lead_management", lead_mod)
        chat_mod = types.ModuleType("chat_history")
        chat_mod.ChatHistory = object  # imported but never constructed here
        monkeypatch.setitem(sys.modules, "chat_history", chat_mod)

        await lnb.collect_patches(limit=7)
        assert seen["limit"] == 7


class TestExtractNameWithLlm:
    def _fake_genai(self, monkeypatch, text=None, exc=None):
        """Install a fake ``google.genai`` (module + parent package attr)."""
        import google

        calls = {}

        class _Response:
            def __init__(self, body):
                self.text = body

        class _Models:
            def generate_content(self, model=None, contents=None):
                calls["model"] = model
                calls["contents"] = contents
                if exc is not None:
                    raise exc
                return _Response(text)

        class _Client:
            def __init__(self):
                self.models = _Models()

        fake = types.ModuleType("google.genai")
        fake.Client = _Client
        monkeypatch.setitem(sys.modules, "google.genai", fake)
        monkeypatch.setattr(google, "genai", fake, raising=False)
        return calls

    @pytest.mark.asyncio
    async def test_returns_none_when_no_user_messages(self):
        assert await lnb.extract_name_with_llm([{"role": "model", "text": "hi"}]) is None
        assert await lnb.extract_name_with_llm([]) is None

    @pytest.mark.asyncio
    async def test_parses_fenced_json_response(self, monkeypatch):
        calls = self._fake_genai(
            monkeypatch,
            text='```json\n{"name": "Jane Doe", "excerpt": "I am Jane Doe, looking"}\n```',
        )
        result = await lnb.extract_name_with_llm(
            [{"role": "user", "text": "I am Jane Doe, looking for a double wide"}]
        )
        assert result == ("Jane Doe", 0.72, "I am Jane Doe, looking")
        assert "extract the customer's full name" in calls["contents"]
        assert "User:" in calls["contents"]

    @pytest.mark.asyncio
    async def test_untolerated_null_or_too_short_name_returns_none(self, monkeypatch):
        self._fake_genai(monkeypatch, text='{"name": null, "excerpt": null}')
        assert (
            await lnb.extract_name_with_llm([{"role": "user", "text": "hi"}]) is None
        )

        self._fake_genai(monkeypatch, text='{"name": "J", "excerpt": "x"}')
        assert (
            await lnb.extract_name_with_llm([{"role": "user", "text": "hi"}]) is None
        )

    @pytest.mark.asyncio
    async def test_client_exception_is_logged_and_returns_none(self, monkeypatch):
        self._fake_genai(monkeypatch, exc=RuntimeError("genai down"))
        assert (
            await lnb.extract_name_with_llm([{"role": "user", "text": "hi"}]) is None
        )

    @pytest.mark.asyncio
    async def test_import_error_is_tolerated(self, monkeypatch):
        import google

        # Force `from google import genai` to fail: no attr on the package and
        # a None placeholder in sys.modules halts the submodule import.
        monkeypatch.delattr(google, "genai", raising=False)
        monkeypatch.setitem(sys.modules, "google.genai", None)
        result = await lnb.extract_name_with_llm([{"role": "user", "text": "hi"}])
        assert result is None


class TestApplyPatches:
    def _patch(self, lead_id="L1", confidence=0.95, name="Jane Doe"):
        return lnb.LeadNamePatch(
            lead_id=lead_id,
            current_name="Unknown",
            proposed_name=name,
            source_session_id="S1",
            source_message_excerpt="excerpt",
            confidence=confidence,
        )

    @pytest.mark.asyncio
    async def test_below_threshold_returns_zero_without_db(self, monkeypatch, capsys):
        _install_crm_fakes(monkeypatch, [])  # LeadManager exists but must not be used
        applied = await lnb.apply_patches([self._patch(confidence=0.5)])
        assert applied == 0
        assert "nothing to apply" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_declining_confirmation_aborts(self, monkeypatch, capsys):
        _, LM = _install_crm_fakes(monkeypatch, [])
        monkeypatch.setattr(builtins, "input", lambda prompt="": "n")
        applied = await lnb.apply_patches([self._patch()], skip_confirm=False)
        assert applied == 0
        assert "Aborted" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_confirmed_apply_updates_missing_and_failing_leads(
        self, monkeypatch, capsys
    ):
        _, LM = _install_crm_fakes(monkeypatch, [])
        ok_lead = _FakeLead("L1", name="Unknown")
        failing = _FakeLead("L3", name="Unknown")

        class _BoomManager(LM):
            async def update_lead(self, lead):
                if lead is failing:
                    raise RuntimeError("write failed")
                await super().update_lead(lead)

        mgr = _BoomManager()
        mgr.existing["L1"] = ok_lead
        mgr.existing["L3"] = failing
        # LeadManager() is constructed inside apply_patches; hand back the
        # pre-seeded instance via the faked module.
        boom_mod = types.ModuleType("lead_management")
        boom_mod.LeadManager = lambda: mgr  # type: ignore[assignment]
        monkeypatch.setitem(sys.modules, "lead_management", boom_mod)

        monkeypatch.setattr(builtins, "input", lambda prompt="": "yes")
        patches = [self._patch("L1"), self._patch("L2"), self._patch("L3", name="Zed")]
        applied = await lnb.apply_patches(patches, skip_confirm=False)

        assert applied == 1
        assert ok_lead.name == "Jane Doe"
        assert mgr.updated == [ok_lead]
        # L2 was missing from Firestore, L3's update raised — both skipped.
        assert capsys.readouterr().out  # confirmation listing printed

    @pytest.mark.asyncio
    async def test_skip_confirm_writes_directly(self, monkeypatch):
        _, LM = _install_crm_fakes(monkeypatch, [])
        mgr = LM()
        lead = _FakeLead("L1", name="Unknown")
        mgr.existing["L1"] = lead
        lead_mod = sys.modules["lead_management"]
        lead_mod.LeadManager = lambda: mgr  # type: ignore[assignment]

        applied = await lnb.apply_patches([self._patch()], skip_confirm=True)
        assert applied == 1
        assert lead.name == "Jane Doe"
        assert mgr.updated == [lead]


class TestBackfillCli:
    def _args(self, **overrides):
        ns = types.SimpleNamespace(
            apply=False, yes=False, output=None, confidence=0.8, llm=False, limit=None
        )
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def _patch(self):
        return lnb.LeadNamePatch(
            lead_id="L1",
            current_name="Unknown",
            proposed_name="Jane Doe",
            source_session_id="S1",
            source_message_excerpt="ex",
            confidence=0.95,
        )

    @pytest.mark.asyncio
    async def test_main_dry_run_prints_jsonl_and_writes_report(
        self, monkeypatch, tmp_path, capsys
    ):
        patch = self._patch()

        async def _fake_collect(use_llm=False, limit=None):
            return [patch]

        monkeypatch.setattr(lnb, "collect_patches", _fake_collect)
        out = tmp_path / "report.jsonl"

        rc = await lnb._main(self._args(output=str(out), llm=True, limit=3))

        assert rc == 0
        captured = capsys.readouterr()
        assert patch.model_dump_json() in captured.out
        assert out.read_text().strip() == patch.model_dump_json()
        summary = json.loads(captured.err.strip().splitlines()[-1])
        assert summary == {
            "summary": {"total_patches": 1, "high_confidence": 1, "dry_run": True}
        }

    @pytest.mark.asyncio
    async def test_main_summary_counts_high_confidence(self, monkeypatch, capsys):
        low = self._patch()
        low.confidence = 0.5

        async def _fake_collect(use_llm=False, limit=None):
            return [self._patch(), low]

        monkeypatch.setattr(lnb, "collect_patches", _fake_collect)
        rc = await lnb._main(self._args(confidence=0.8))
        assert rc == 0
        err_lines = capsys.readouterr().err.strip().splitlines()
        summary = json.loads(err_lines[-1])
        assert summary == {"summary": {"total_patches": 2, "high_confidence": 1, "dry_run": True}}

    @pytest.mark.asyncio
    async def test_main_apply_invokes_apply_patches(self, monkeypatch, capsys):
        applied = {}

        async def _fake_collect(use_llm=False, limit=None):
            return [self._patch()]

        async def _fake_apply(patches, min_confidence=0.8, skip_confirm=False):
            applied["min_confidence"] = min_confidence
            applied["skip_confirm"] = skip_confirm
            return 1

        monkeypatch.setattr(lnb, "collect_patches", _fake_collect)
        monkeypatch.setattr(lnb, "apply_patches", _fake_apply)

        rc = await lnb._main(self._args(apply=True, yes=True, confidence=0.9))
        assert rc == 0
        assert applied == {"min_confidence": 0.9, "skip_confirm": True}
        err_lines = capsys.readouterr().err.strip().splitlines()
        assert json.loads(err_lines[-1]) == {"applied": 1}

    def test_main_exits_via_system_exit(self, monkeypatch):
        async def _fake_main(args):
            return 0

        monkeypatch.setattr(lnb, "_main", _fake_main)
        monkeypatch.setattr(sys, "argv", ["lead_name_backfill.py"])
        with pytest.raises(SystemExit) as excinfo:
            lnb.main()
        assert excinfo.value.code == 0

    def test_module_main_guard_runs_end_to_end(self, monkeypatch, tmp_path):
        """runpy with run_name=__main__ exercises the ``if __name__`` block in-process."""
        _install_crm_fakes(monkeypatch, [])  # empty lead list -> empty patches
        monkeypatch.setattr(sys, "argv", ["lead_name_backfill.py"])

        # Hide the repo root so the module's ``sys.path.insert(0, _ROOT)`` guard
        # actually fires (otherwise the line is skipped as already-importable).
        root = str(REPO_ROOT)
        stripped = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != root]
        monkeypatch.setattr(sys, "path", stripped)

        module_path = REPO_ROOT / "tools" / "lead_name_backfill.py"
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_path(str(module_path), run_name="__main__")

        assert excinfo.value.code == 0


# ─────────────────────────────────────────────────────────────────────────────
# tools/lead_nurture.py
# ─────────────────────────────────────────────────────────────────────────────


def _install_firestore_module_fake(monkeypatch, client):
    """Fake ``from google.cloud import firestore`` for _get_db.

    Needs BOTH sys.modules["google.cloud.firestore"] and the attribute on the
    real google.cloud package (raising=False), mirroring production import.
    """
    import google.cloud

    fake = types.ModuleType("google.cloud.firestore")

    class _ClientFactory:
        def __call__(self, project=None):
            if isinstance(client, Exception):
                raise client
            return client

    fake.Client = _ClientFactory()
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", fake)
    monkeypatch.setattr(google.cloud, "firestore", fake, raising=False)


class TestGetDb:
    def test_lazy_client_success(self, monkeypatch):
        sentinel = object()
        _install_firestore_module_fake(monkeypatch, sentinel)
        lead_nurture._reset_client_for_tests()
        try:
            assert lead_nurture._get_db() is sentinel
            # Second call returns the cached client without re-importing.
            assert lead_nurture._get_db() is sentinel
        finally:
            lead_nurture._reset_client_for_tests()

    def test_client_failure_returns_none(self, monkeypatch):
        _install_firestore_module_fake(monkeypatch, RuntimeError("no creds"))
        lead_nurture._reset_client_for_tests()
        try:
            assert lead_nurture._get_db() is None
        finally:
            lead_nurture._reset_client_for_tests()


def test_now_utc_is_timezone_aware():
    now = lead_nurture._now_utc()
    assert now.tzinfo is not None
    assert now.utcoffset() == datetime(2026, 1, 1, tzinfo=UTC).utcoffset()


class TestCoerceDt:
    def test_none(self):
        assert lead_nurture._coerce_dt(None) is None

    def test_naive_datetime_assumes_utc(self):
        naive = datetime(2026, 3, 1, 10, 0, 0)
        coerced = lead_nurture._coerce_dt(naive)
        assert coerced.replace(tzinfo=None) == naive
        assert coerced.tzinfo is UTC

    def test_aware_datetime_passthrough(self):
        aware = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
        assert lead_nurture._coerce_dt(aware) is aware

    def test_iso_string_with_z_suffix(self):
        coerced = lead_nurture._coerce_dt("2026-03-01T10:00:00Z")
        assert coerced == datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)

    def test_naive_iso_string_assumes_utc(self):
        coerced = lead_nurture._coerce_dt("2026-03-01 10:00:00")
        assert coerced.tzinfo is UTC

    def test_invalid_string_returns_none(self):
        assert lead_nurture._coerce_dt("not a date") is None

    def test_firestore_like_object_with_isoformat(self):
        class _FsTimestamp:
            def isoformat(self):
                return "2026-03-01T10:00:00+00:00"

        coerced = lead_nurture._coerce_dt(_FsTimestamp())
        assert coerced == datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)

    def test_isoformat_raising_returns_none(self):
        class _Bad:
            def isoformat(self):
                raise ValueError("nope")

        assert lead_nurture._coerce_dt(_Bad()) is None

    def test_unsupported_type_returns_none(self):
        assert lead_nurture._coerce_dt(12345) is None


def test_last_contact_returns_none_without_any_timestamp():
    assert lead_nurture._last_contact({}) is None


# ── find_stale_leads branch coverage ────────────────────────────────────────


class _Doc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _Query:
    """Queryable fake: supports where/limit/stream like the Firestore API."""

    def __init__(self, docs, limit_raises_typeerror=False, stream_exc=None):
        self._docs = docs
        self._filters = []
        self._limit = None
        self._limit_raises = limit_raises_typeerror
        self._stream_exc = stream_exc

    def where(self, field, op, value):
        q = _Query(
            self._docs,
            limit_raises_typeerror=self._limit_raises,
            stream_exc=self._stream_exc,
        )
        q._filters = [*self._filters, (field, op, value)]
        q._limit = self._limit
        return q

    def limit(self, n):
        if self._limit_raises:
            return _LegacyQuery(self._docs, self._filters, self._stream_exc)
        q = _Query(
            self._docs, stream_exc=self._stream_exc
        )
        q._filters = list(self._filters)
        q._limit = n
        return q

    def stream(self, timeout=None):
        if self._stream_exc is not None:
            raise self._stream_exc
        emitted = 0
        for doc_id, data in self._docs.items():
            if all(data.get(f) == v for f, _, v in self._filters):
                yield _Doc(doc_id, data)
                emitted += 1
                if self._limit is not None and emitted >= self._limit:
                    return


class _LegacyQuery:
    """Old-API fake whose stream() takes no kwargs (forces the TypeError fallback)."""

    def __init__(self, docs, filters, stream_exc):
        self._docs = docs
        self._filters = filters
        self._stream_exc = stream_exc

    def stream(self):
        if self._stream_exc is not None:
            raise self._stream_exc
        for doc_id, data in self._docs.items():
            if all(data.get(f) == v for f, _, v in self._filters):
                yield _Doc(doc_id, data)


class _Db:
    def __init__(self, docs, **query_kwargs):
        self._docs = docs
        self._query_kwargs = query_kwargs

    def collection(self, name):
        assert name == lead_nurture.CUSTOMERS_COLLECTION
        return _Query(self._docs, **self._query_kwargs)


_STALE = {"status": "LEAD", "email": "a@example.com", "updated_at": "2020-01-01T00:00:00Z"}


def _install_db(monkeypatch, db):
    monkeypatch.setattr(lead_nurture, "_firestore_client", db)


class TestFindStaleLeadsBranches:
    def test_negative_days_is_clamped_to_zero(self, monkeypatch):
        fresh = {"status": "LEAD", "email": "f@example.com", "updated_at": "2026-01-01T00:00:00Z"}
        _install_db(monkeypatch, _Db({"fresh": fresh}))
        # days_inactive=-10 must not raise and must clamp to 0 (everything is stale).
        results = lead_nurture.find_stale_leads(days_inactive=-10)
        assert [r["customer_id"] for r in results] == ["fresh"]

    def test_non_numeric_max_results_falls_back_to_default(self, monkeypatch):
        docs = {f"c{i}": dict(_STALE, email=f"c{i}@example.com") for i in range(3)}
        _install_db(monkeypatch, _Db(docs))
        results = lead_nurture.find_stale_leads(days_inactive=14, max_results="junk")
        assert len(results) == 3

    def test_max_results_none_falls_back(self, monkeypatch):
        _install_db(monkeypatch, _Db({"a": dict(_STALE)}))
        results = lead_nurture.find_stale_leads(days_inactive=14, max_results=None)
        assert len(results) == 1

    def test_legacy_query_without_limit_chaining(self, monkeypatch):
        _install_db(monkeypatch, _Db({"a": dict(_STALE)}, limit_raises_typeerror=True))
        results = lead_nurture.find_stale_leads(days_inactive=14)
        assert [r["customer_id"] for r in results] == ["a"]

    def test_missing_timestamp_treated_as_max_stale(self, monkeypatch):
        no_ts = {"status": "LEAD", "email": "x@example.com"}
        _install_db(monkeypatch, _Db({"no-ts": no_ts}))
        results = lead_nurture.find_stale_leads(days_inactive=14)
        assert len(results) == 1
        assert results[0]["customer_id"] == "no-ts"
        assert results[0]["last_contact_at"] is None
        assert results[0]["days_inactive"] >= 1000  # datetime.min → huge

    def test_stream_exception_returns_empty(self, monkeypatch):
        _install_db(monkeypatch, _Db({"a": dict(_STALE)}, stream_exc=RuntimeError("rpc dead")))
        assert lead_nurture.find_stale_leads(days_inactive=14) == []


class TestLogNurtureSend:
    def test_no_db_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(lead_nurture, "_get_db", lambda: None)
        lead_nurture._log_nurture_send("c1", "e@x.com", "subj", {"success": True})

    def test_write_failure_is_logged_not_raised(self, monkeypatch):
        class _BadAddable:
            def add(self, data, timeout=None):
                raise RuntimeError("write failed")

        class _BadDb:
            def collection(self, name):
                return _BadAddable()

        monkeypatch.setattr(lead_nurture, "_get_db", lambda: _BadDb())
        lead_nurture._log_nurture_send("c1", "e@x.com", "subj", {"success": False})


class TestRunNurtureBatchSendFailure:
    def test_raising_send_counts_as_failed(self, monkeypatch):
        cohort = [
            {
                "customer_id": "c1",
                "name": "Bob",
                "email": "bob@example.com",
                "days_inactive": 30,
            }
        ]
        monkeypatch.setattr(lead_nurture, "find_stale_leads", lambda **kwargs: list(cohort))

        def _boom_send(**kwargs):
            raise RuntimeError("resend down")

        fake_email = types.ModuleType("email_service")
        fake_email.send_custom_email = _boom_send
        monkeypatch.setitem(sys.modules, "email_service", fake_email)

        logged = []

        def _fake_log(customer_id, email, subject, send_result):
            logged.append((customer_id, send_result))

        monkeypatch.setattr(lead_nurture, "_log_nurture_send", _fake_log)

        result = lead_nurture.run_nurture_batch(dry_run=False, days_inactive=14)

        assert result["cohort_size"] == 1
        assert result["sent"] == 0
        assert result["failed"] == 1
        assert logged == [("c1", {"success": False, "error": "resend down"})]


# ─────────────────────────────────────────────────────────────────────────────
# tools/listing_urls.py
# ─────────────────────────────────────────────────────────────────────────────


class TestListingUrlsGuards:
    def test_stock_id_non_dict(self):
        assert lnu.stock_id(None) is None
        assert lnu.stock_id("nope") is None

    def test_stock_id_all_empty_returns_none(self):
        assert lnu.stock_id({}) is None
        assert lnu.stock_id({"stock_number": "", "id": None}) is None

    def test_is_instock_non_dict(self):
        assert lnu.is_instock_home(None) is False
        assert lnu.is_instock_home([1, 2]) is False

    def test_is_instock_orderable_kind_rejected(self):
        home = {"inventory_kind": "orderable_floorplan", "status": "Available"}
        assert lnu.is_instock_home(home) is False
        assert lnu.listing_path(home) is None

    def test_is_instock_unknown_kind_rejected(self):
        home = {"inventory_kind": "mystery_kind", "status": "Available"}
        assert lnu.is_instock_home(home) is False

    def test_match_instock_home_without_home_token(self):
        assert lnu.match_instock_home("/plans/something", []) is None
        assert lnu.match_instock_home(None, []) is None

    def test_match_instock_home_by_stock_id(self):
        home = {
            "stock_number": "15677",
            "model_name": "Oak",
            "inventory_kind": "available_now",
            "status": "Available",
        }
        # Token doesn't equal the full path slug, but matches the stock id.
        assert lnu.match_instock_home("/homes/15677", [home]) is home

    def test_listing_page_images_non_dict(self):
        assert lnu.listing_page_images(None) == []

    def test_listing_page_images_floorplan_urls_list_fallback(self):
        home = {"floorplan_urls": ["  ", "https://cdn.example.com/plan.pdf"]}
        assert lnu.listing_page_images(home) == ["https://cdn.example.com/plan.pdf"]

    def test_listing_page_images_empty_floorplan_list_falls_through(self):
        # floorplan_urls is a list but every entry is blank → the final [].
        assert lnu.listing_page_images({"floorplan_urls": ["", "   "]}) == []
        # No photos, no floorplan keys at all → [].
        assert lnu.listing_page_images({"model_name": "Ghost"}) == []

    def test_attach_listing_urls_skips_non_dict_entries(self):
        good = {
            "stock_number": "42",
            "model_name": "Oak",
            "inventory_kind": "available_now",
            "status": "Available",
        }
        result = lnu.attach_listing_urls([None, "junk", good])
        assert result[2]["listing_url"] == "/homes/42-oak"
        assert "listing_url" not in result[0] if isinstance(result[0], dict) else True


class TestParseDate:
    def test_object_with_working_date(self):
        assert lnu._parse_date(datetime(2026, 3, 5, 12, 0, 0, tzinfo=UTC)) == "2026-03-05"

    def test_object_with_raising_date_falls_through(self):
        class _Weird:
            def date(self):
                raise TypeError("no date")

            def __str__(self):
                return "not a date at all"

        assert lnu._parse_date(_Weird()) is None

    def test_whitespace_only_returns_none(self):
        assert lnu._parse_date("   ") is None

    def test_iso_parse_failure_falls_back_to_date_prefix_regex(self):
        assert lnu._parse_date("2026-03-05 trailing junk") == "2026-03-05"
        assert lnu._parse_date("no date here") is None


class TestUrlList:
    def test_string_input(self):
        assert lnu._url_list("https://x.example/a.jpg") == ["https://x.example/a.jpg"]
        assert lnu._url_list("   ") == []

    def test_unsupported_type_returns_empty(self):
        assert lnu._url_list(123) == []
        assert lnu._url_list(None) == []


class TestPhotoSubjectAndPathFromUrl:
    def test_photo_subject_with_no_stem_parts(self):
        assert lnu.listing_photo_subject("https://cdn.example.com/.png") is None
        assert lnu.listing_photo_subject("") is None

    def test_path_from_url(self):
        assert lnu.path_from_url(None) is None
        assert lnu.path_from_url("") is None
        assert lnu.path_from_url("https://example.com/") is None
        assert lnu.path_from_url("https://example.com/homes/15677-oak?x=1") == "/homes/15677-oak"


# ─────────────────────────────────────────────────────────────────────────────
# tools/house_orders_sync.py
# ─────────────────────────────────────────────────────────────────────────────


def test_is_home_serial_blank_is_not_a_home():
    assert hos._is_home_serial("") is False
    assert hos._is_home_serial("   ") is False


def test_iter_home_rows_skips_rows_without_a_model():
    records = [{"Serial #": "15677", "Model": ""}, {"Serial #": "10543", "Model": "  "}]
    assert list(hos._iter_home_rows(records)) == []


def test_row_to_doc_rejects_non_home_row():
    assert hos.house_order_row_to_inventory_doc({"serial": "TOTAL", "model": "X"}) is None
    assert hos.house_order_row_to_inventory_doc({"serial": "", "model": "X"}) is None


def _write_house_orders_xlsx(path: Path, rows: list[dict], sheet: str = "Everybody Else") -> Path:
    """Build a tiny House Orders workbook with the real two-row header layout."""
    import pandas as pd

    columns = ["Serial #", "Model", "Manufacturing Plant", "Customer", "Customer #", "Invoice Amount"]
    frame = pd.DataFrame(rows, columns=columns)
    # header=1 in parse_house_orders means the SECOND row holds the column names,
    # so leave a blank title row above the header.
    frame.to_excel(path, sheet_name=sheet, startrow=1, index=False)
    return path


def test_parse_house_orders_reads_real_xlsx(tmp_path):
    xlsx = _write_house_orders_xlsx(
        tmp_path / "House Orders.xlsx",
        [
            {
                "Serial #": "15677",
                "Model": "Oak 28x56 3/2",
                "Manufacturing Plant": "Tru Belton",
                "Customer": None,
                "Customer #": None,
                "Invoice Amount": 60187.0,
            },
            {
                "Serial #": "10543",
                "Model": "Jackson 16x76",
                "Manufacturing Plant": "Jessup",
                "Customer": "Gabriel Mercado",
                "Customer #": "customer-456",
                "Invoice Amount": 88123.0,
            },
            {"Serial #": "Serial #", "Model": "Model"},  # header echo dropped
        ],
    )

    docs = hos.parse_house_orders(str(xlsx))

    assert {d["serial_number"] for d in docs} == {"15677", "10543"}
    by_serial = {d["serial_number"]: d for d in docs}
    assert by_serial["15677"]["status"] == "AVAILABLE"
    assert by_serial["10543"]["status"] == "SOLD"
    blob = repr(docs).lower()
    assert "mercado" not in blob and "invoice" not in blob and "60187" not in blob


def test_parse_house_orders_missing_sheet_raises(tmp_path):
    import pandas as pd

    empty = tmp_path / "empty.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(empty, sheet_name="Other", index=False)
    with pytest.raises(ValueError):
        hos.parse_house_orders(str(empty))


def test_load_from_gcs_cleans_up_even_when_unlink_fails(monkeypatch):
    """The finally-block OSError swallow (line ~236) fires when unlink fails."""
    captured = {}

    class _Blob:
        def download_to_filename(self, path):
            captured["path"] = path
            with open(path, "wb") as fh:
                fh.write(b"x")

    class _Bucket:
        def blob(self, name):
            return _Blob()

    class _Client:
        def bucket(self, name):
            return _Bucket()

    from google.cloud import storage as gcs

    monkeypatch.setattr(gcs, "Client", lambda: _Client())
    monkeypatch.setattr(
        hos,
        "parse_house_orders",
        lambda path: [{"serial_number": "X", "model_name": "M", "status": "AVAILABLE"}],
    )

    real_unlink = os.unlink

    def _flaky_unlink(path):
        if str(path).endswith(".xlsx"):
            raise OSError("file locked")
        return real_unlink(path)

    monkeypatch.setattr(os, "unlink", _flaky_unlink)

    docs = hos.load_house_orders_from_gcs("bucket", "blob.xlsx")
    assert docs == [{"serial_number": "X", "model_name": "M", "status": "AVAILABLE"}]
    assert captured["path"].endswith(".xlsx")


def test_main_dry_run_prints_plan(tmp_path, capsys):
    rows = [
        {
            "Serial #": "15677",
            "Model": "Oak 28x56 3/2",
            "Manufacturing Plant": "Tru Belton",
            "Customer": None,
            "Customer #": None,
            "Invoice Amount": 1.0,
        }
    ]
    xlsx = _write_house_orders_xlsx(tmp_path / "House Orders.xlsx", rows)

    rc = hos.main(["--path", str(xlsx)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[DRY-RUN]" in out
    assert "AVAILABLE 15677: Oak 28x56 3/2" in out
    assert "re-run with --apply" in out


def test_main_dry_run_truncates_long_plan(tmp_path, capsys):
    rows = [
        {
            "Serial #": f"1{i:04d}",
            "Model": f"Model {i} 16x76",
            "Manufacturing Plant": "Plant",
            "Customer": None,
            "Customer #": None,
            "Invoice Amount": None,
        }
        for i in range(30)
    ]
    xlsx = _write_house_orders_xlsx(tmp_path / "House Orders.xlsx", rows)

    rc = hos.main(["--path", str(xlsx)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "... and 5 more" in out


def test_main_apply_writes_approved_serials(tmp_path, capsys, monkeypatch):
    rows = [
        {
            "Serial #": "15677",
            "Model": "Oak 28x56 3/2",
            "Manufacturing Plant": "Tru Belton",
            "Customer": None,
            "Customer #": None,
            "Invoice Amount": None,
        },
        {
            "Serial #": "99999",
            "Model": "Jackson 16x76",
            "Manufacturing Plant": "Jessup",
            "Customer": None,
            "Customer #": None,
            "Invoice Amount": None,
        },
    ]
    xlsx = _write_house_orders_xlsx(tmp_path / "House Orders.xlsx", rows)

    db = FakeSyncDb()
    fake_client_mod = types.ModuleType("database.firestore_client")
    fake_client_mod.get_database = lambda: db
    monkeypatch.setitem(sys.modules, "database.firestore_client", fake_client_mod)

    rc = hos.main(["--path", str(xlsx), "--apply", "--approved-serial", "15677"])

    assert rc == 0
    assert set(db.docs) == {"15677"}
    assert db.docs["15677"]["model_name"] == "Oak 28x56 3/2"
    out = capsys.readouterr().out
    assert "[APPLIED]" in out
    assert "skipped unapproved: 1" in out


class FakeSyncDb:
    def __init__(self):
        self.docs = {}

    def upsert_inventory(self, inventory_id: str, data: dict) -> str:
        self.docs[inventory_id] = data
        return inventory_id


# ── residual branch closures ─────────────────────────────────────────────────


class TestExtractNameFromMessagesBranches:
    """Zero-cost regex extractor branches not hit elsewhere."""

    def test_non_user_messages_are_skipped(self):
        assert lnb.extract_name_from_messages([{"role": "model", "text": "my name is Jane"}]) is None

    def test_stop_word_tokens_are_rejected(self):
        # "The" is in _STOP_NAMES — no patch should be proposed.
        assert lnb.extract_name_from_messages([{"role": "user", "text": "my name is The Best"}]) is None

    def test_single_character_short_token_is_rejected(self):
        assert lnb.extract_name_from_messages([{"role": "user", "text": "my name is Al" }]) is None
        # ...but a longer single name is fine.
        result = lnb.extract_name_from_messages([{"role": "user", "text": "my name is Alex"}])
        assert result is not None and result[0] == "Alex"


def test_messages_to_dicts_normalises_objects():
    msg = types.SimpleNamespace(role="user", text="hi")
    assert lnb._messages_to_dicts([msg, {"role": "model"}]) == [
        {"role": "user", "text": "hi"},
        {"role": "model", "text": ""},
    ]
