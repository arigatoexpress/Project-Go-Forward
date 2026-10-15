# main.py Coverage Understatement — Investigation Log

**Status (2026-10-15): root cause localized; no fix yet. This is a MEASUREMENT
bug, not a test-coverage gap.** The suite executes the endpoint handlers; the
coverage tracer fails to record most handler lines under `pytest --cov`.

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

1. Build a minimal repro of the CTracer silent death for a coverage.py bug
   report: shrink from the full app (FastAPI + fake `lead_management` module +
   one endpoint) until the cutoff survives.
2. Alternatively, fix `load_app` to stop stubbing `lead_management` (needs a
   Firestore-fake boundary that keeps tests hermetic — nontrivial).
3. Re-measure main.py endpoint coverage via the proven subprocess path for the
   launch-relevant missed blocks: `get_document_analytics` (2505),
   `bulk_import_inventory` (4209), `docuseal_webhook` (5276),
   `submit_chat_callback` (6624), `email_inbound_webhook` (6960),
   `get_user_activity` (8058).
