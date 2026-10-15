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

## Breakthrough (cycle 3, 2026-10-15) — later refined, see cycle 4 below

- The trigger is precisely `FakeLeadManager` (not `FakeLead`): real Lead +
  fake manager → cut; fake Lead + real manager → records.
- The real `LeadManager.create_lead` suspends via `await asyncio.to_thread(...)`
  (Firestore write in a worker thread); the fake completed synchronously.
  Making the fake's `create_lead`/`update_lead` use `asyncio.to_thread` was
  measured (same cycle, 1,893 → 1,804 missed) as improving attribution.
  **Correction (cycle 4): it does NOT heal the whole handler** — see below.
- **Fix applied**: `tests/test_api_v1.py` `FakeLeadManager.create_lead` /
  `update_lead` now mirror the real suspension. Other fake managers
  (test_obsidian_routes.FakeLeadManager, etc.) may need the same treatment if
  their endpoints show the same symptom.

## Root cause confirmed (cycle 4, 2026-10-15)

Facts established by direct experiment on the current tree (probe scaffolding
since removed; all findings reproducible):

1. **The executing bytecode is correct.** The live `submit_contact_form`
   code object's `co_lines()` is byte-identical to a fresh
   `compile(open("main.py").read(), ...)` — the earlier "0–2910 span" anomaly
   was an extraction bug (co_lines() tuples are `(byte_offset, size, line)`,
   not `(line, ...)`). No stale pyc (3.11 pyc matches source mtime/size), no
   template `exec` shadowing the route handler.
2. **CPython fires every line event.** A raw `sys.settrace` Python tracer
   installed *inside the portal thread* during the request sees all lines of
   the handler, including every "missing" one (6713–6769 etc.).
3. **coverage's CTracer silently drops them.** With pytest-cov active, the
   portal thread holds the `coverage.CTracer` (`sys.gettrace()` confirms it,
   `f_trace_lines` is True), yet raw `data.lines()` at report time lacks the
   span. No `sys.settrace` calls occur during the run (audit hook, 0 events).
4. **The rule (deterministic per code shape, verified across 6 probes):** in
   TestClient portal-thread async handlers, the CTracer records lines up to
   the first suspension (`await request.json()`), then goes silent for
   everything executed between that resume and the next suspension, and
   records again from the next suspension's resume line onward. An endpoint
   with no second suspension loses its entire tail (probe: lines after
   `request.json()` all missing); an exception before the second suspension
   loses the tail plus the executed `except` handler (probe: swallowed
   `TypeError` case); a `to_thread` second suspension shrinks the blind
   window to the span between the two awaits (the real handler: blind
   6710–6769, sighted 6770–6890 — the earlier "max 6890 = healed" claim
   conflated the tracer recording lines 6770+ with full coverage).
5. **Why the to_thread fake still helped:** without a real suspension at
   `create_lead`, the blind window extended from `request.json()` to the end
   of the handler; `asyncio.to_thread` gives a recovery point at 6770. It
   shrinks the artifact, it does not remove it.
6. Not code-shape-timing flaky: probe5 (sighted after 2nd suspension; its
   lines 19–20 between awaits were *also* missing) and the blind probes are
   stable across repeated runs.

This matches a known upstream issue class with no fix released as of
coverage 7.16.2 / CPython 3.11.13: coverage.py #2124 (executed async lines
missing after await; 3.11.11) and #2245 (same symptom verified with
sys.settrace receiving events the tracer drops). Our variant is
CTracer-specific (pure Python tracer sees everything), which #2245 says is
NOT always the case — worth a bug report only with a minimal repro, which we
do not have (a trivial standalone FastAPI app does not reproduce; the real
app's middleware/slowapi/portal context is required).

## Practical consequences

- Coverage of `main.py` (and other async handlers exercised through
  `TestClient`) is **understated by a measurement artifact**, on top of any
  real gaps. Treat big missing spans in portal-thread handlers with
  suspicion; the "Proven working measurement path" below (subprocess +
  ASGITransport) remains the way to get honest numbers for a specific route.
- Options not yet tried (next cycles): (a) Python ≥ 3.12 venv with
  `core = sysmon` — sys.monitoring replaces settrace-based line events and
  may not have this defect; (b) async tests via `httpx.AsyncClient` in the
  main thread instead of TestClient's blocking portal, so handlers run in a
  traced main-thread loop; (c) minimal repro for a coverage.py report.

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

1. Coverage re-measured after the to_thread fake fix: 1,893 → 1,804 missed
   (59% → 61%). Remaining misses are a mix of this artifact (portal-thread
   handler spans between the first and second suspension) and REAL gaps worth
   writing tests for — distinguish them using the measurement paths above
   before writing tests aimed at a phantom miss.
2. If other endpoint groups still under-attribute, check their fake managers'
   async methods for the same missing-suspension pattern
   (`grep -rn "async def" tests/ | grep -v to_thread`).
3. Measurement-side candidates (unproven): Python ≥ 3.12 venv with
   `core = sysmon`; async main-thread tests via `httpx.AsyncClient` instead of
   TestClient's blocking portal.
4. Long-term: minimal repro of the CTracer silent death for a coverage.py bug
   report (needs real-app context; shrink by stripping main.py, not building
   up from scratch).
