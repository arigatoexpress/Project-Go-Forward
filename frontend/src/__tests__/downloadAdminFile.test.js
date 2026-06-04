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

  it('rejects an empty (0-byte) document instead of saving a broken file', async () => {
    adminFetch.mockResolvedValue({
      ok: true,
      status: 200,
      blob: vi.fn().mockResolvedValue({ size: 0, type: 'application/pdf' }),
      headers: { get: () => 'attachment; filename="Documents.pdf"' },
    });

    await expect(downloadAdminFile('/api/documents/download/Documents.pdf')).rejects.toThrow(
      'came back empty',
    );
  });

  it('triggers the download and DEFERS object-URL cleanup so large files are not cancelled', async () => {
    vi.useFakeTimers();

    const createObjectURL = vi.fn(() => 'blob:mock-url');
    const revokeObjectURL = vi.fn();
    const clickSpy = vi.fn();
    const removeSpy = vi.fn();
    const fakeAnchor = { click: clickSpy, remove: removeSpy, style: {} };
    const appendChild = vi.fn();

    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL });
    vi.stubGlobal('document', {
      createElement: vi.fn(() => fakeAnchor),
      body: { appendChild },
    });

    const blob = { size: 3242189, type: 'application/pdf' }; // ~3.2MB merged packet
    adminFetch.mockResolvedValue({
      ok: true,
      status: 200,
      blob: vi.fn().mockResolvedValue(blob),
      headers: { get: () => 'attachment; filename="Documents_Jordan_Brooks.pdf"' },
    });

    const filename = await downloadAdminFile('/api/documents/download/whatever.pdf');

    // Download was triggered and the resolved filename is returned for UI confirmation.
    expect(appendChild).toHaveBeenCalledWith(fakeAnchor);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(fakeAnchor.download).toBe('Documents_Jordan_Brooks.pdf');
    expect(filename).toBe('Documents_Jordan_Brooks.pdf');

    // Cleanup must NOT run synchronously — that is what cancelled large downloads.
    expect(revokeObjectURL).not.toHaveBeenCalled();
    expect(removeSpy).not.toHaveBeenCalled();

    // After the deferred timer fires, cleanup happens.
    vi.runAllTimers();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');
    expect(removeSpy).toHaveBeenCalledTimes(1);

    vi.unstubAllGlobals();
    vi.useRealTimers();
  });
});
