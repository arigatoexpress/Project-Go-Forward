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
- RESOLVED below: measure under Python ≥ 3.12 (see "RESOLVED (cycle 5)").

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

## RESOLVED (cycle 5, 2026-10-15): CPython 3.11 defect — any Python ≥ 3.12 measures correctly

A/B matrix, same probe test (`create_client` + `POST /api/contact` with
`{"name": "Synthetic Buyer", "phone": "5125550123"}`), same dump of raw
`data.lines()` for the previously-blind span 6713–6769 of `submit_contact_form`:

| Python   | coverage core | span 6713–6769        |
|----------|---------------|-----------------------|
| 3.11.13  | CTracer       | **DROPPED** (the bug) |
| 3.12.8   | CTracer       | records fully ✓       |
| 3.13.5   | CTracer       | records fully ✓       |
| 3.14.5   | CTracer       | records fully ✓       |
| 3.14.5   | sysmon        | records fully ✓       |

Conclusion: the blind-window artifact is a **CPython 3.11 line-event delivery
defect** — after certain coroutine suspensions 3.11 fires `py_tracefunc` (the
pure-Python tracer) but not `c_tracefunc` (the CTracer), which is why the
Python-level probe saw every line while the CTracer saw none. The defect is
gone in 3.12+ (the monitoring/PEP 669 work rewrote that dispatch). coverage.py
is blameless; no coverage upgrade and no `core = sysmon` switch is needed —
just don't measure on 3.11.

Recommendations:
- Any coverage measurement where the number matters (launch-readiness
  evidence, gap analysis) should run under Python ≥ 3.12. Moving the dev venv
  is a bigger job: the full suite (3003 tests) has only been validated on
  3.11, so validate on 3.12+ before switching. Note also that a FRESH env
  install currently fails dependency resolution: `google-cloud-aiplatform
  ==1.161.0` requires `google-cloud-storage>=3.10` while requirements.txt pins
  `google-cloud-storage==2.18.0` (the existing .venv predates the conflict).
  Both must be resolved before CI can move to a newer Python.
- Keep the fake managers' `asyncio.to_thread` mirroring (committed cycle 3):
  it faithfully mirrors the real managers' suspension behavior, is harmless
  under 3.12+, and remains correct if measurement ever runs on 3.11 again.
- Extending `to_thread` to the obsidian/mira fake managers is coverage-neutral
  (verified: obsidian handlers have their manager await as the sole/first
  suspension, so no inter-suspension blind window exists; mira suites run
  pytest-asyncio in the main thread, unaffected). Not worth the churn.
- Upstream references (coverage.py #2124, #2245) describe the same symptom on
  3.11/3.13; our controlled A/B shows 3.13.5 is clean, so those reporters'
  variants may differ or may have had residual 3.11-era environments. If a
  coverage.py bug report is still wanted, the repro here is: Python 3.11.13 +
  coverage 7.16.2 + TestClient portal + handler with code between
  `request.json()` and a second suspension.

## Next steps

1. Coverage re-measured after the to_thread fake fix: 1,893 → 1,804 missed
   (59% → 61%). **Re-measure under Python ≥ 3.12 to get the honest number** —
   expect a further jump for portal-thread handler spans; the remaining misses
   after that are REAL gaps worth writing tests for.
2. If other endpoint groups still under-attribute on 3.11, check their fake
   managers' async methods for the same missing-suspension pattern
   (`grep -rn "async def" tests/ | grep -v to_thread`) — under 3.12+
   measurement this becomes moot.
3. Long-term: validate the full suite under Python 3.12/3.13 and resolve the
   requirements.txt storage-vs-aiplatform pin conflict so the measurement
   venv can move off 3.11.
