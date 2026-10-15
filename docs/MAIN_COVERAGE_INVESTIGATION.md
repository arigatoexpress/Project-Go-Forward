# main.py Coverage Understatement — Investigation Log

**Status (2026-10-15, cycle 3): WORKAROUND FOUND AND APPLIED.** The trigger is
the shape of faked async manager methods that shadow real
`asyncio.to_thread`-suspending implementations: mirroring the real suspension
heals coverage attribution (applied to `FakeLeadManager` and
`FakeAppointmentManager` in `tests/test_api_v1.py`). Full-suite A/B on
main.py: 1,893 → 1,804 missed lines (59% → 61%). The heal is per-endpoint —
each endpoint whose awaited fakes complete synchronously still under-records,
and the remaining 1,804 misses need either the real mechanism (below) or the
same treatment for other stubs. The underlying CTracer mechanism is still not
understood — a trivial standalone FastAPI+fake repro does NOT reproduce;
real-app context is required. This is a MEASUREMENT bug, not a test-coverage
gap.

## Symptom

`main.py` (10,079 lines) measures ~59% under pytest-cov. Missed lines are
dominated by endpoint handler bodies that tests demonstrably execute
(e.g. `submit_contact_form` returns 200 with a `lead_id` under TestClient, yet
coverage records nothing past the handler's entry lines).

## Verified fact chain (each item experimentally confirmed)

1. **Not a test gap.** A pure-Python `sys.settrace` probe in the exact broken
   scenario records every handler line (6708–6890) of `submit_contact_form`.
2. **Not `sys.settrace(None)` by a library.** `sys.addaudithook` on the
   `sys.settrace` audit event (catches every call, no exceptions) shows only
   coverage's own start/stop and per-thread installer events. The tracer stays
   installed for the whole request (`sys.gettrace()` probes at handler entry,
   pre/post-await all show the CTracer object).
3. **Not threads / TestClient portal.** Same loss with `httpx.ASGITransport` in
   the test's own `asyncio.run` in the main thread. Minimal repros (plain
   asyncio in a non-main thread, anyio blocking portal, FastAPI, slowapi) all
   record correctly.
4. **Not import side effects, module identity, or re-import.** The
   `sys.modules` diff between working and broken scenarios is pure probe
   artifact. Re-importing `main` alone is clean. Using the REAL
   `lead_management` module with patched attributes (`LeadManager`/`Lead`/
   `normalize_phone` swapped for fakes, mirroring how `database.models` is
   stubbed in `load_app`) still breaks.
5. **Not `concurrency` config.** CTracer with `concurrency=[]` still cut.
   (`concurrency=["thread"]` was also tried earlier: no-op, reverted.)
6. **Trigger isolated to the `lead_management` stub.** Bisecting `load_app`'s
   stub groups: stubbing `lead_management` alone reproduces the cutoff;
   stubbing `appointment_manager` or `email_service` alone does not. The real
   module heals it. The mechanism by which FakeLead/FakeLeadManager change
   tracer behavior is unknown (see hypotheses).
7. **Timid/pytracer mode crashes.** `IndexError: pop from empty list` in
   `pytracer.py` data_stack. Separately, a per-frame probe found
   return-without-call anomalies (`httpx` ASGI transport helper coroutines
   being closed), but these occur in BOTH scenarios, so they don't explain the
   difference.

## Hypotheses (unconfirmed)

- The CTracer's C-level data_stack or per-frame trace state gets corrupted by
  a specific coroutine lifecycle pattern that only occurs when the
  `lead_management` fakes are active (e.g. different await/yield structure in
  the request path). PyTracer crashes loudly from stack imbalance; CTracer
  silently stops recording.
- A CPython 3.11 trace-event quirk (RESUME/call-event accounting) interacting
  with those frames.

## Breakthrough (cycle 3, 2026-10-15)

- The trigger is precisely `FakeLeadManager` (not `FakeLead`): real Lead +
  fake manager → cut; fake Lead + real manager → records.
- The real `LeadManager.create_lead` suspends via `await asyncio.to_thread(...)`
  (Firestore write in a worker thread); the fake completed synchronously.
  Making the fake's `create_lead`/`update_lead` use `asyncio.to_thread` **heals
  the entire handler** — all lines of `submit_contact_form` record (max 6890),
  even though the apparent "cut" was earlier in the request. The CTracer's
  failure is determined by the await-tree shape, not a clean death point.
- A trivial standalone FastAPI repro (same fake shape) does NOT reproduce — the
  real app's context (middleware/slowapi/imports) is required. Minimal-repro
  shrinking for a coverage.py bug report remains open.
- **Fix applied**: `tests/test_api_v1.py` `FakeLeadManager.create_lead` /
  `update_lead` now mirror the real suspension. Other fake managers
  (test_obsidian_routes.FakeLeadManager, etc.) may need the same treatment if
  their endpoints show the same symptom.

## What does NOT work

- importlib.reload-based "sabotage" theory — disproven (an empty second test
  file "sabotaged" identically; handler never attributed even alone).
- `timid=True` — pytracer crashes (fact 7).
- `concurrency` tweaks — no effect (fact 5).

## Proven working measurement path

Subprocess + real modules: import `main` directly (no `load_app` stubs), hit
it with `httpx.ASGITransport`, measure with in-process
`coverage.Coverage(source=["main"])` — handler body lines record fully
(observed 6788–6879 on `/api/contact`). Slow (real Firestore RPC timeouts when
no emulator) but honest.

## Next steps

1. Validate the fix suite-wide: `pytest tests/ -q --cov=main --cov-report=term`
   — expect main.py coverage to jump well above the 59% / 1,893-missed
   baseline. Remaining misses after that are REAL gaps worth writing tests for.
2. If other endpoint groups still under-attribute, check their fake managers'
   async methods for the same missing-suspension pattern
   (`grep -rn "async def" tests/ | grep -v to_thread`).
3. Long-term: minimal repro of the CTracer silent death for a coverage.py bug
   report (needs real-app context; shrink by stripping main.py, not building
   up from scratch).
