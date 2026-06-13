# System Improvements Checklist

## ✅ Completed
- [x] Inventory sync (20 homes imported)
- [x] Trade-in calculator for pre-owned workflow
- [x] The Nassau images fixed
- [x] CRM with tasks & email templates
- [x] Document Center with 4-step wizard

## 🔍 Issues Found

### 1. Service Response Time
- Health check occasionally times out (10s+)
- May need cold start optimization

### 2. Missing Features (Status as of 2025-06-13)
- [x] Chat memory/persistence — **IMPLEMENTED** (`chat_history.py`, `conversation_memory.py`, Firestore-backed, admin API endpoints, tests in progress)
- [x] Error boundaries in React — **IMPLEMENTED** (`frontend/src/components/ErrorBoundary.jsx`, root + per-route wrapping, Sentry hook, retry/reload UI)
- [x] API rate limiting — **IMPLEMENTED** (slowapi per-IP 100/min default, custom `RateLimitMiddleware`, per-route caps on admin endpoints, chat-specific limits)
- [x] Request validation middleware — **IMPLEMENTED** (`resilient_validation_handler` in `main.py`, unified error envelope for SPA)
- [x] Audit logging for admin actions — **IMPLEMENTED** (`audit_log.py`, PII-stripping, 12 call sites in `main.py`, full test coverage in `tests/test_audit_log.py`)

### 3. Potential Improvements

#### A. Error Handling
- [x] Add global error boundary component — **DONE**
- [ ] Better error messages for users (ongoing polish)
- [ ] Sentry integration for error tracking (SDK hook ready; `@sentry/react` not yet installed)

#### B. Performance
- [x] Redis caching for inventory queries — **DONE** (`caching.py` with Redis + in-memory fallback)
- [ ] Lazy load heavy components
- [ ] Image optimization

#### C. Security
- [x] Rate limiting per IP — **DONE**
- [ ] Input sanitization (partial — PII redaction in audit log, broader sanitization needed)
- [ ] CSRF protection

#### D. Monitoring
- [ ] Health check dashboard
- [ ] Performance metrics
- [ ] User activity logging

## 📋 Recommended Next Steps

### High Priority (Code)
1. **Chat History Tests** — Write `tests/test_chat_history.py` and `tests/test_conversation_memory.py` (modules are in production but have zero test coverage)
2. **Input Sanitization** — Broader request-body sanitization beyond audit-log PII stripping
3. **CSRF Protection** — Add CSRF tokens for state-changing admin endpoints

### Medium Priority
4. **Lazy Loading** — Code-split heavy React components (Document Center, CRM, Chat History)
5. **Image Optimization** — Responsive images, WebP/AVIF, lazy loading
6. **Performance Metrics** — Structured latency logging + p95/p99 tracking

### Low Priority
7. **Sentry SDK** — Install `@sentry/react` and wire up `window.__SENTRY_HOOK__`
8. **A/B Testing Framework** — Test UI changes
9. **Feature Flags** — Roll out features gradually
10. **Documentation** — API docs (OpenAPI already available), user guides

### Operator / Launch Blockers (see LAUNCH_READINESS.md)
- Ops bootstrap (partner API key, monitoring, backups, budget alarm)
- DocuSeal e-sign deploy + E2E
- Prod PIN strength verification
- Staging E2E gauntlet + load test
