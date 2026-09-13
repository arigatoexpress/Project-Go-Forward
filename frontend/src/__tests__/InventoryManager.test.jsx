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
    bedrooms: 3, bathrooms: 2, width: 28, length: 60, is_new: false, features: ['Porch'], sale_price: 0,
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

it('loads, saves, reloads and clears the approved public sale price', async () => {
  let stored = { ...existingHome, public_sale_price: 87500, sale_price: 999999 };
  adminFetch.mockImplementation(async (_url, options) => {
    if (options?.method === 'PUT') {
      const payload = JSON.parse(options.body);
      expect(payload).not.toHaveProperty('msrp');
      stored = { ...stored, ...payload, public_sale_price: payload.sale_price };
      return { json: async () => ({ success: true }) };
    }
    return { json: async () => ({ success: true, inventory: [stored] }) };
  });
  render(<InventoryManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
  expect(screen.getByLabelText('Public sale price ($)').value).toBe('87500');
  fireEvent.change(screen.getByLabelText('Public sale price ($)'), { target: { value: '89900' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await screen.findByText('Home updated.');
  expect(stored.public_sale_price).toBe(89900);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
  expect(screen.getByLabelText('Public sale price ($)').value).toBe('89900');
  fireEvent.change(screen.getByLabelText('Public sale price ($)'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await screen.findByText('Home updated.');
  expect(stored.public_sale_price).toBe(0);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
  expect(screen.getByLabelText('Public sale price ($)').value).toBe('');
});

it('does not populate the public price from the document-autofill MSRP fallback', async () => {
  adminFetch.mockResolvedValue({ json: async () => ({
    success: true, inventory: [{ ...existingHome, sale_price: 999999, public_sale_price: null }],
  }) });
  render(<InventoryManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
  expect(screen.getByLabelText('Public sale price ($)').value).toBe('');
});

it('rejects a negative public sale price before sending a save', async () => {
  render(<InventoryManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
  fireEvent.change(screen.getByLabelText('Public sale price ($)'), { target: { value: '-1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await screen.findByText('Public sale price must be a nonnegative number.');
  expect(adminFetch.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false);
});

it('rejects fractions smaller than a cent instead of silently rounding the advertised price', async () => {
  render(<InventoryManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
  fireEvent.change(screen.getByLabelText('Public sale price ($)'), { target: { value: '123.456' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await screen.findByText('Public sale price can have at most two decimal places.');
  expect(adminFetch.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false);
});
