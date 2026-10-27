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
  fireEvent.click(await screen.findByRole('button', { name: 'Change this home' }));
  fireEvent.change(screen.getByLabelText('Home name'), { target: { value: 'Updated Double' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save this home' }));

  await screen.findByText(/Saved\. Customers will see Call for Price/);
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
  fireEvent.click(await screen.findByRole('button', { name: 'Change this home' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save this home' }));

  await waitFor(() => expect(screen.getByText('Type the home\'s name before saving.')).toBeTruthy());
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
  fireEvent.click(await screen.findByRole('button', { name: 'Change this home' }));
  expect(screen.getByLabelText('Price on the website ($)').value).toBe('87500');
  fireEvent.change(screen.getByLabelText('Price on the website ($)'), { target: { value: '89900' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save this home' }));
  await screen.findByText(/Saved\. Customers will see this update/);
  expect(stored.public_sale_price).toBe(89900);
  fireEvent.click(await screen.findByRole('button', { name: 'Change this home' }));
  expect(screen.getByLabelText('Price on the website ($)').value).toBe('89900');
  fireEvent.change(screen.getByLabelText('Price on the website ($)'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save this home' }));
  await screen.findByText(/Saved\. Customers will see Call for Price/);
  expect(stored.public_sale_price).toBe(0);
  fireEvent.click(await screen.findByRole('button', { name: 'Change this home' }));
  expect(screen.getByLabelText('Price on the website ($)').value).toBe('');
});

it('does not populate the public price from the document-autofill MSRP fallback', async () => {
  adminFetch.mockResolvedValue({ json: async () => ({
    success: true, inventory: [{ ...existingHome, sale_price: 999999, public_sale_price: null }],
  }) });
  render(<InventoryManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Change this home' }));
  expect(await screen.findByText(/No real photos yet/i)).toBeTruthy();
  expect(screen.getByLabelText('Price on the website ($)').value).toBe('');
});

it('rejects a negative public sale price before sending a save', async () => {
  render(<InventoryManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Change this home' }));
  fireEvent.change(screen.getByLabelText('Price on the website ($)'), { target: { value: '-1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save this home' }));
  await screen.findByText('The price cannot be less than zero. Leave it blank if customers should call for the price.');
  expect(adminFetch.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false);
});

it('rejects fractions smaller than a cent instead of silently rounding the advertised price', async () => {
  render(<InventoryManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Change this home' }));
  fireEvent.change(screen.getByLabelText('Price on the website ($)'), { target: { value: '123.456' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save this home' }));
  await screen.findByText('Use dollars and cents only, like 89900 or 89900.50.');
  expect(adminFetch.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false);
});

it('shows a duplicated home once with a staff-visible hint', async () => {
  adminFetch.mockResolvedValue({ json: async () => ({
    success: true,
    inventory: [
      { ...existingHome, id: 'fs-big-blue', model_name: 'Big Blue', possible_duplicate_ids: ['44490'] },
      { ...existingHome, id: '44490', model_name: 'PRE-OWNED / Big Blue', duplicate_of: 'fs-big-blue' },
    ],
  }) });
  render(<InventoryManager />);
  expect(await screen.findByText('Big Blue')).toBeTruthy();
  expect(screen.getAllByText('Big Blue')).toHaveLength(1);
  expect(screen.getByTestId('possible-duplicate').textContent).toMatch(/44490/);
  expect(screen.queryByText('PRE-OWNED / Big Blue')).toBeNull();
});

it('asks in plain language before removing a home from the website', async () => {
  render(<InventoryManager />);
  fireEvent.click(await screen.findByRole('button', { name: /Remove from website/i }));
  expect(screen.getByRole('alertdialog').textContent).toMatch(
    /Remove this home from the website\? You can bring it back later/,
  );
  expect(adminFetch.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false);

  fireEvent.click(screen.getByRole('button', { name: /Yes, remove it/i }));
  await screen.findByText(/is off the website/);
  expect(adminFetch.mock.calls.some(([url, options]) => (
    options?.method === 'DELETE' && url === '/api/inventory/existing-home'
  ))).toBe(true);
});

it('does not remove the home when staff cancel the confirmation', async () => {
  render(<InventoryManager />);
  fireEvent.click(await screen.findByRole('button', { name: /Remove from website/i }));
  fireEvent.click(screen.getByRole('button', { name: /No, keep it/i }));
  expect(screen.queryByRole('alertdialog')).toBeNull();
  expect(adminFetch.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false);
});

it('stays on a new home so staff can add real photos on the same screen', async () => {
  adminFetch.mockImplementation(async (url, options) => {
    if (options?.method === 'POST' && String(url) === '/api/inventory') {
      return { json: async () => ({ success: true, id: 'new-home' }) };
    }
    if (String(url).includes('/photos') && options?.method === 'POST') {
      return { ok: true, json: async () => ({ uploaded: [{ filename: 'lot.jpg' }], errors: [] }) };
    }
    if (String(url).includes('/photos')) {
      return { ok: true, json: async () => ({ photos: [] }) };
    }
    return { json: async () => ({ success: true, inventory: [] }) };
  });

  render(<InventoryManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Add a home' }));
  expect(screen.getByText(/Save the home first/i)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Home name'), { target: { value: 'Lot 12' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save this home' }));

  expect(await screen.findByText(/Now add photos of the real home/i)).toBeTruthy();
  const file = new File(['photo'], 'lot.jpg', { type: 'image/jpeg' });
  fireEvent.change(screen.getByLabelText('Photos of the real home', { selector: 'input' }), { target: { files: [file] } });
  expect(await screen.findByText(/1 photo added/i)).toBeTruthy();
  const upload = adminFetch.mock.calls.find(([url, options]) => (
    options?.method === 'POST' && String(url) === '/api/inventory/new-home/photos'
  ));
  expect(upload).toBeTruthy();
});

it('refuses a file that is not a photo of the real home', async () => {
  render(<InventoryManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Change this home' }));
  const file = new File(['plan'], 'floorplan.pdf', { type: 'application/pdf' });
  fireEvent.change(screen.getByLabelText('Photos of the real home', { selector: 'input' }), { target: { files: [file] } });
  expect(await screen.findByRole('alert')).toHaveTextContent(/not a photo/i);
  expect(adminFetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
});
