// Fire-and-forget first-party funnel events. Never include contact fields or
// other PII in `data`; this sink is for page/source/intent attribution only.
export function trackEvent(event, data = {}) {
  try {
    const payload = JSON.stringify({
      event,
      ...data,
      path: typeof window !== 'undefined' ? window.location.pathname : undefined,
    });
    if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
      navigator.sendBeacon('/api/analytics', new Blob([payload], { type: 'application/json' }));
    } else {
      fetch('/api/analytics', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true,
      }).catch(() => {});
    }
  } catch {
    // Analytics must never interrupt the conversion path.
  }
}
