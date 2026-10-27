import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import PhotoManager from '../pages/PhotoManager';
import adminFetch from '../adminFetch';

vi.mock('../adminFetch', () => ({ default: vi.fn() }));

const home = {
  id: '43372',
  model_name: 'Starter Home',
  status: 'AVAILABLE',
  real_photos: [],
};

const TOUR_URL = 'https://my.matterport.com/show/?m=JbALitnto3g&play=1';

function jsonResponse(body, { ok = true, status = 200 } = {}) {
  return { ok, status, json: vi.fn().mockResolvedValue(body) };
}

function mockFetches({ tour, onTour } = {}) {
  adminFetch.mockImplementation((url, options = {}) => {
    const path = String(url);
    if (path.includes('/api/marketing/inventory-context')) {
      return Promise.resolve(jsonResponse({ success: true, homes: [] }));
    }
    if (path.startsWith('/api/inventory?')) {
      return Promise.resolve(jsonResponse({ success: true, inventory: [home] }));
    }
    if (path.endsWith('/tour')) {
      if (onTour) onTour(path, options);
      if (options.method === 'POST') return Promise.resolve(tour.post);
      if (options.method === 'DELETE') return Promise.resolve(tour.remove);
      return Promise.resolve(tour.get);
    }
    return Promise.resolve(jsonResponse({ photos: [] }));
  });
}

async function pickHome() {
  fireEvent.click(await screen.findByRole('radio', { name: /Starter Home/i }));
  await screen.findByRole('region', { name: '3D tour link' });
  await waitFor(() => expect(screen.queryByText(/Loading 3D tour/i)).not.toBeInTheDocument());
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('Image', class {
    set src(_value) {
      queueMicrotask(() => this.onload?.());
    }
  });
});

describe('Photo Manager 3D tour link', () => {
  it('saves a Matterport URL and shows the rebuilt tour link', async () => {
    const calls = [];
    mockFetches({
      onTour: (path, options) => calls.push({ path, options }),
      tour: {
        get: jsonResponse({ success: true, matterport_id: null, matterport_url: null }),
        post: jsonResponse({
          success: true,
          matterport_id: 'JbALitnto3g',
          matterport_url: TOUR_URL,
        }),
      },
    });

    render(<PhotoManager onBack={vi.fn()} />);
    await pickHome();
    const region = screen.getByRole('region', { name: '3D tour link' });
    expect(region.textContent).not.toMatch(/—/);
    expect(screen.getByText(/No 3D tour saved for this home yet/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Matterport link').className).toMatch(/min-h-12/);
    expect(screen.getByLabelText('Matterport link').className).toMatch(/text-base/);
    expect(screen.getByRole('button', { name: 'Save 3D tour' }).className).toMatch(/min-h-12/);

    const pasted = 'https://my.matterport.com/show/?m=JbALitnto3g&play=1&next=https://evil.example';
    fireEvent.change(screen.getByLabelText('Matterport link'), { target: { value: pasted } });
    fireEvent.click(screen.getByRole('button', { name: 'Save 3D tour' }));

    expect(await within(region).findByRole('status')).toHaveTextContent(
      '3D tour saved. Customers will see it on the website shortly.',
    );
    expect(screen.getByRole('link', { name: 'Open 3D tour' })).toHaveAttribute('href', TOUR_URL);
    expect(screen.getByRole('link', { name: 'Open 3D tour' }).getAttribute('href')).not.toContain('evil.example');
    const post = calls.find((call) => call.options.method === 'POST');
    expect(post.path).toBe('/api/inventory/43372/tour');
    expect(post.options.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(post.options.body)).toEqual({ matterport: pasted });
  });

  it('shows a fixed error for junk and script input', async () => {
    mockFetches({
      tour: {
        get: jsonResponse({ success: true, matterport_id: null }),
        post: jsonResponse(
          { detail: '<script>alert(1)</script> was rejected' },
          { ok: false, status: 400 },
        ),
      },
    });

    render(<PhotoManager onBack={vi.fn()} />);
    await pickHome();
    const payload = '<script>alert(1)</script>';
    fireEvent.change(screen.getByLabelText('Matterport link'), { target: { value: payload } });
    fireEvent.click(screen.getByRole('button', { name: 'Save 3D tour' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(
      'That is not a Matterport tour link. Paste the link from my.matterport.com, or the tour ID.',
    );
    expect(alert.textContent).not.toContain('<script');
    expect(alert.textContent).not.toContain('was rejected');
    expect(screen.queryByRole('link', { name: 'Open 3D tour' })).toBeNull();
  });

  it('loads a saved tour and removes it', async () => {
    const calls = [];
    mockFetches({
      onTour: (_path, options) => calls.push(options.method || 'GET'),
      tour: {
        get: jsonResponse({ success: true, matterport_id: 'JbALitnto3g', matterport_url: TOUR_URL }),
        remove: jsonResponse({ success: true, matterport_id: null, matterport_url: null }),
      },
    });

    render(<PhotoManager onBack={vi.fn()} />);
    await pickHome();
    expect(screen.getByLabelText('Matterport link')).toHaveValue(TOUR_URL);
    expect(screen.getByRole('link', { name: 'Open 3D tour' })).toHaveAttribute('href', TOUR_URL);

    fireEvent.click(screen.getByRole('button', { name: 'Remove 3D tour' }));
    fireEvent.click(screen.getByRole('button', { name: 'Yes, remove the tour' }));

    expect(await within(screen.getByRole('region', { name: '3D tour link' })).findByRole('status'))
      .toHaveTextContent('3D tour link removed.');
    expect(screen.getByText(/No 3D tour saved for this home yet/i)).toBeInTheDocument();
    expect(calls).toContain('DELETE');
  });

  it('asks staff to sign in again when the save is rejected', async () => {
    mockFetches({
      tour: {
        get: jsonResponse({ success: true, matterport_id: null }),
        post: jsonResponse({ detail: 'Admin authentication required' }, { ok: false, status: 401 }),
      },
    });

    render(<PhotoManager onBack={vi.fn()} />);
    await pickHome();
    fireEvent.change(screen.getByLabelText('Matterport link'), { target: { value: 'JbALitnto3g' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save 3D tour' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Please sign in again, then save the 3D tour.',
    );
  });

  it('does not treat a failed load as an empty tour', async () => {
    mockFetches({
      tour: {
        get: jsonResponse({ message: 'The 3D tour could not be loaded. Please try again.' }, { ok: false, status: 503 }),
      },
    });

    render(<PhotoManager onBack={vi.fn()} />);
    fireEvent.click(await screen.findByRole('radio', { name: /Starter Home/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not load the 3D tour. Refresh and try again.',
    );
    expect(screen.queryByText(/No 3D tour saved for this home yet/i)).toBeNull();
  });

  it('mentions an existing catalog tour without offering remove', async () => {
    adminFetch.mockImplementation((url) => {
      const path = String(url);
      if (path.startsWith('/api/inventory?')) {
        return Promise.resolve(jsonResponse({
          success: true,
          inventory: [{ ...home, matterport_id: 'X4kxMpwHFCj' }],
        }));
      }
      if (path.endsWith('/tour')) {
        return Promise.resolve(jsonResponse({ success: true, matterport_id: null }));
      }
      return Promise.resolve(jsonResponse({ success: true, homes: [], photos: [] }));
    });

    render(<PhotoManager onBack={vi.fn()} />);
    await pickHome();
    expect(screen.getByText(/already has a 3D tour/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the current 3D tour' })).toHaveAttribute(
      'href',
      'https://my.matterport.com/show/?m=X4kxMpwHFCj&play=1',
    );
    expect(screen.queryByRole('button', { name: 'Remove 3D tour' })).toBeNull();
  });
});
