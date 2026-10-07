import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import PhotoManager from '../pages/PhotoManager';
import adminFetch from '../adminFetch';

vi.mock('../adminFetch', () => ({ default: vi.fn() }));

function jsonResponse(body) {
  return { ok: true, status: 200, json: vi.fn().mockResolvedValue(body) };
}

const homes = [
  { id: '44490', model_name: 'PRE-OWNED / Big Blue', status: 'AVAILABLE', stock_number: '44490', real_photos: [] },
  { id: 'dup', model_name: 'Big Blue', status: 'AVAILABLE', duplicate_of: '44490', real_photos: [] },
  { id: 'sold', model_name: 'Old Sold Home', status: 'SOLD', stock_number: '100', real_photos: [] },
  { id: 'series', model_name: 'The Promotional Series / The Nassau FAC28483A', status: 'AVAILABLE', serial_number: 'TXL9', real_photos: ['a.jpg'] },
];

beforeEach(() => {
  vi.clearAllMocks();
  adminFetch.mockImplementation((url) => {
    if (String(url).startsWith('/api/inventory?')) {
      expect(String(url)).toContain('include_staff_photos=true');
      return Promise.resolve(jsonResponse({ success: true, inventory: homes }));
    }
    return Promise.resolve(jsonResponse({ photos: [] }));
  });
});

describe('Photo Manager home picker', () => {
  it('defaults to active inventory with friendly names and wraps labels', async () => {
    render(<PhotoManager onBack={vi.fn()} />);

    expect(await screen.findByText('Big Blue')).toBeInTheDocument();
    expect(screen.getByText('Stock #44490 · Needs photos')).toBeInTheDocument();
    expect(screen.getByText('The Nassau')).toBeInTheDocument();
    expect(screen.getByText('Serial TXL9')).toBeInTheDocument();
    expect(screen.queryByText(/PRE-OWNED/)).toBeNull();
    expect(screen.queryByText(/Promotional Series/)).toBeNull();
    expect(screen.queryByText('Old Sold Home')).toBeNull();
    expect(screen.getAllByRole('radio')).toHaveLength(2);
    expect(screen.getByRole('radiogroup', { name: /Choose a home/i }).className).toMatch(/break-words|overflow/);
  });

  it('shows sold homes only after staff tick Include sold/archived', async () => {
    render(<PhotoManager onBack={vi.fn()} />);
    await screen.findByText('Big Blue');

    fireEvent.click(screen.getByLabelText('Include sold/archived homes'));
    expect(screen.getByText('Old Sold Home')).toBeInTheDocument();
    expect(screen.getByText(/Sold · Needs photos/)).toBeInTheDocument();
  });
});
