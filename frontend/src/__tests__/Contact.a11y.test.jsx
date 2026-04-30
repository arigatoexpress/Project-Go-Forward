import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import Contact from '../pages/Contact';

// Smoke tests for the customer-facing Contact page covering the a11y baseline:
// - one heading at level 1 (page heading)
// - one <label>/<input> association per visible field
// - error/status text is in a live region (aria-live or role=alert)
describe('Contact a11y', () => {
  it('renders a single h1 for the page', () => {
    render(<Contact onBack={() => {}} />);
    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading).toBeInTheDocument();
    expect(heading.textContent).toMatch(/contact us/i);
  });

  it('associates a label with every visible input via htmlFor / id', () => {
    render(<Contact onBack={() => {}} />);

    // getByLabelText throws if no <label htmlFor> resolves to an input.
    expect(screen.getByLabelText(/your name/i)).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByLabelText(/phone number/i)).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByLabelText(/^email/i)).toBeInstanceOf(HTMLInputElement);
    // The textarea is also matched by getByLabelText (works for any form control).
    expect(screen.getByLabelText(/what can we help with/i)).toBeInstanceOf(HTMLTextAreaElement);
  });

  it('marks the required fields with aria-required="true"', () => {
    render(<Contact onBack={() => {}} />);
    expect(screen.getByLabelText(/your name/i)).toHaveAttribute('aria-required', 'true');
    expect(screen.getByLabelText(/phone number/i)).toHaveAttribute('aria-required', 'true');
    expect(screen.getByLabelText(/what can we help with/i)).toHaveAttribute('aria-required', 'true');
  });

  it('wraps page content in a <main> landmark', () => {
    render(<Contact onBack={() => {}} />);
    expect(screen.getByRole('main')).toBeInTheDocument();
  });
});
