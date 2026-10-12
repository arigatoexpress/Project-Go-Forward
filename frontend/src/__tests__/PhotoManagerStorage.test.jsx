import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import PhotoManager from '../pages/PhotoManager';
import adminFetch from '../adminFetch';

vi.mock('../adminFetch', () => ({ default: vi.fn() }));

const home = {
  id: '43372',
  model_name: 'Starter Home',
  status: 'AVAILABLE',
  real_photos: [],
};

function jsonResponse(body, { ok = true, status = 200 } = {}) {
  return { ok, status, json: vi.fn().mockResolvedValue(body) };
}

function inventoryThenPhotos() {
  adminFetch.mockImplementation((url) => {
    if (String(url).startsWith('/api/inventory?')) {
      return Promise.resolve(jsonResponse({ success: true, inventory: [home] }));
    }
    return Promise.resolve(jsonResponse({ photos: [] }));
  });
}

async function pickStarterHome() {
  fireEvent.click(await screen.findByRole('radio', { name: /Starter Home/i }));
}

beforeEach(() => {
  vi.clearAllMocks();
  inventoryThenPhotos();
});

describe('Photo Manager storage safety', () => {
  it('makes the home picker and upload target keyboard-accessible', async () => {
    render(<PhotoManager onBack={vi.fn()} />);

    await screen.findByRole('radio', { name: /Starter Home/i });
    expect(screen.getByRole('radiogroup', { name: /Choose a home/i })).toBeInTheDocument();
    const uploadTarget = screen.getByRole('button', { name: /Choose listing photos/i });
    expect(uploadTarget).toHaveAttribute('aria-disabled', 'true');
    expect(uploadTarget).toHaveAttribute('tabindex', '-1');

    await pickStarterHome();
    expect(uploadTarget).toHaveAttribute('aria-disabled', 'false');
    expect(uploadTarget).toHaveAttribute('tabindex', '0');
  });

  it('reports a partial durable write as an outage, not an invalid-image success', async () => {
    adminFetch.mockImplementation((url, options = {}) => {
      if (String(url).startsWith('/api/inventory?')) {
        return Promise.resolve(jsonResponse({ success: true, inventory: [home] }));
      }
      if (options.method === 'POST') {
        return Promise.resolve(jsonResponse({
          success: true,
          storage_unavailable: true,
          uploaded: [{ filename: 'front.png' }],
          errors: [{
            name: 'side.png',
            error: 'Durable photo storage is temporarily unavailable.',
          }],
          unattempted: ['rear.png'],
          unattempted_count: 1,
          retryable: ['side.png', 'rear.png'],
        }, { status: 207 }));
      }
      return Promise.resolve(jsonResponse({ photos: [] }));
    });

    const { container } = render(<PhotoManager onBack={vi.fn()} />);
    await pickStarterHome();

    const input = container.querySelector('input[type="file"]');
    const files = [
      new File(['first'], 'front.png', { type: 'image/png' }),
      new File(['second'], 'side.png', { type: 'image/png' }),
    ];
    fireEvent.change(input, { target: { files } });

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /1 photo was confirmed, then durable storage became unavailable/i,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(/rear\.png/i);
    expect(screen.getByRole('alert')).toHaveTextContent(/side\.png/i);
    await waitFor(() => expect(adminFetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(true));
  });

  it('explains a complete durable-storage outage', async () => {
    adminFetch.mockImplementation((url, options = {}) => {
      if (String(url).startsWith('/api/inventory?')) {
        return Promise.resolve(jsonResponse({ success: true, inventory: [home] }));
      }
      if (options.method === 'POST') {
        return Promise.resolve(jsonResponse({
          success: false,
          storage_unavailable: true,
          uploaded: [],
          errors: [{
            name: 'front.png',
            error: 'Durable photo storage is temporarily unavailable.',
          }],
          unattempted: ['side.png'],
          unattempted_count: 1,
          retryable: ['front.png', 'side.png'],
        }, { ok: false, status: 503 }));
      }
      return Promise.resolve(jsonResponse({ photos: [] }));
    });

    const { container } = render(<PhotoManager onBack={vi.fn()} />);
    await pickStarterHome();
    const input = container.querySelector('input[type="file"]');
    fireEvent.change(input, { target: { files: [
      new File(['first'], 'front.png', { type: 'image/png' }),
      new File(['second'], 'side.png', { type: 'image/png' }),
    ] } });

    expect(await screen.findByRole('alert')).toHaveTextContent(/durable photo storage/i);
    expect(screen.getByRole('alert')).toHaveTextContent(/front\.png/i);
    expect(screen.getByRole('alert')).toHaveTextContent(/side\.png/i);
  });
});
