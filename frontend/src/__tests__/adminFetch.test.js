import { afterEach, describe, expect, it, vi } from 'vitest';
import adminFetch from '../adminFetch';

afterEach(() => {
  document.cookie = 'tho_csrf_token=; Max-Age=0; path=/';
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('adminFetch retry safety', () => {
  it('never retries a POST after an ambiguous server failure by default', async () => {
    document.cookie = 'tho_csrf_token=conflict-resolution-token; path=/';
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 503 });
    vi.stubGlobal('fetch', fetchMock);

    const response = await adminFetch('/api/marketing/publish', { method: 'POST' });

    expect(response.status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/marketing/publish',
      expect.objectContaining({
        method: 'POST',
        headers: { 'X-CSRF-Token': 'conflict-resolution-token' },
      }),
    );
  });

  it('never retries a POST after an ambiguous network failure by default', async () => {
    const networkError = new Error('connection reset');
    const fetchMock = vi.fn().mockRejectedValue(networkError);
    vi.stubGlobal('fetch', fetchMock);

    await expect(adminFetch('/api/marketing/publish', { method: 'POST' })).rejects.toBe(networkError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('allows an explicitly idempotent POST to opt into bounded retries', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 503 })
      .mockResolvedValueOnce({ ok: true, status: 200 });
    vi.stubGlobal('fetch', fetchMock);

    const pending = adminFetch('/api/idempotent-operation', { method: 'POST', retry: true });
    await vi.advanceTimersByTimeAsync(1000);

    await expect(pending).resolves.toMatchObject({ status: 200 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][1]).not.toHaveProperty('retry');
  });
});
