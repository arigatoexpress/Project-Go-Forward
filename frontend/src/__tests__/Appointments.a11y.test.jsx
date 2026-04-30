import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import Appointments from '../pages/Appointments';

// Smoke tests for the multi-step appointment booking flow. Step 3 contains
// the form fields, so we walk forward to that step before asserting label
// associations. Steps 1 and 2 only need the heading-hierarchy + landmark
// guarantees.
describe('Appointments a11y', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => ({
      ok: true,
      json: async () => ({
        business_hours: '9:00 AM – 6:00 PM',
        available_slots: ['9:00 AM', '10:00 AM', '11:00 AM'],
      }),
    }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders a single h1 page heading at step 1', () => {
    render(<Appointments onBack={() => {}} />);
    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading).toBeInTheDocument();
    expect(heading.textContent).toMatch(/book an appointment/i);
  });

  it('wraps the page in a <main> landmark', () => {
    render(<Appointments onBack={() => {}} />);
    expect(screen.getByRole('main')).toBeInTheDocument();
  });

  it('exposes step 3 form fields with associated labels and aria-required', async () => {
    render(<Appointments onBack={() => {}} />);

    // Step 1 → pick a future date. The CalendarGrid disables past days, so we
    // grab the first enabled date button and click it.
    const dayButtons = screen
      .getAllByRole('button')
      .filter(b => /^\w+, \w+ \d+, \d{4}$/.test(b.getAttribute('aria-label') || ''));
    const firstEnabled = dayButtons.find(b => !b.disabled);
    expect(firstEnabled).toBeTruthy();
    fireEvent.click(firstEnabled);

    // Step 2 → pick a time slot.
    const timeBtn = await waitFor(() =>
      screen.getByRole('button', { name: /book at 9:00 AM/i })
    );
    fireEvent.click(timeBtn);

    // Step 3 → form fields with proper label associations.
    const nameInput = await waitFor(() => screen.getByLabelText(/full name/i));
    expect(nameInput).toBeInstanceOf(HTMLInputElement);
    expect(nameInput).toHaveAttribute('aria-required', 'true');

    const phoneInput = screen.getByLabelText(/phone number/i);
    expect(phoneInput).toBeInstanceOf(HTMLInputElement);
    expect(phoneInput).toHaveAttribute('aria-required', 'true');

    expect(screen.getByLabelText(/^email/i)).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByLabelText(/what homes are you interested in/i)).toBeInstanceOf(HTMLTextAreaElement);
  });
});
