import { describe, it, expect, vi, beforeEach } from 'vitest';
import adminFetch from '../adminFetch';
import downloadAdminFile from '../downloadAdminFile';

vi.mock('../adminFetch', () => ({
  default: vi.fn(),
}));

describe('downloadAdminFile', () => {
  beforeEach(() => {
    adminFetch.mockReset();
  });

  it('keeps the actionable expired-session message for 401 downloads', async () => {
    adminFetch.mockResolvedValue({
      ok: false,
      status: 401,
      json: vi.fn().mockResolvedValue({ detail: 'Not authenticated' }),
    });

    await expect(downloadAdminFile('/api/admin/documents/latest')).rejects.toThrow(
      'Your admin session expired before the document could download.',
    );
  });

  it('uses backend error details for non-auth download failures', async () => {
    adminFetch.mockResolvedValue({
      ok: false,
      status: 422,
      json: vi.fn().mockResolvedValue({ detail: 'Sales price is required' }),
    });

    await expect(downloadAdminFile('/api/admin/documents/latest')).rejects.toThrow(
      'Sales price is required',
    );
  });
});
