import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import InventoryManager from '../pages/InventoryManager';
import adminFetch from '../adminFetch';

vi.mock('../adminFetch', () => ({ default: vi.fn() }));

const existingHome = {
  id: 'existing-home', model_name: 'Existing Double', manufacturer: null,
  serial_number: null, classification: 'Double Wide', status: 'AVAILABLE',
  is_new: false, beds: '3', baths: 2, width: '28', length: '60',
  features: ['Porch'],
};

beforeEach(() => {
  adminFetch.mockReset();
  adminFetch.mockImplementation(async (_url, options) => ({
    json: async () => options?.method === 'PUT'
      ? { success: true }
      : { success: true, inventory: [existingHome] },
  }));
});
afterEach(cleanup);

it('saves an existing home with null optional strings without losing its specifications', async () => {
  render(<InventoryManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
  fireEvent.change(screen.getByLabelText('Model name *'), { target: { value: 'Updated Double' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));

  await screen.findByText('Home updated.');
  const save = adminFetch.mock.calls.find(([, options]) => options?.method === 'PUT');
  expect(save[0]).toBe('/api/inventory/existing-home');
  expect(JSON.parse(save[1].body)).toEqual({
    model_name: 'Updated Double', classification: 'Double Wide', status: 'AVAILABLE',
    bedrooms: 3, bathrooms: 2, width: 28, length: 60, is_new: false, features: ['Porch'],
  });
});

it('requires a model name when an imported record has a null model name', async () => {
  adminFetch.mockResolvedValue({ json: async () => ({
    success: true, inventory: [{ ...existingHome, model_name: null }],
  }) });
  render(<InventoryManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));

  await waitFor(() => expect(screen.getByText('Model name is required.')).toBeTruthy());
  expect(adminFetch.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false);
});
