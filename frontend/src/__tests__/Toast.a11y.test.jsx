import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, screen } from '@testing-library/react';
import { ToastProvider, useToast } from '../components/Toast';

function Trigger({ message, type = 'info', duration = 5000 }) {
  const { addToast } = useToast();
  return <button onClick={() => addToast(message, type, duration)}>show</button>;
}

// Verifies the live-region behavior added in feat/a11y-customer-pages.
// Errors render as role=alert (assertive); the container is role=status with
// aria-live=polite so info/success messages are announced after the user
// finishes their current action.
describe('Toast a11y', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders the container as a polite live region', () => {
    render(
      <ToastProvider>
        <Trigger message="hi" type="info" duration={3000} />
      </ToastProvider>
    );

    // The container exists even before any toast is shown.
    const region = screen.getByRole('status');
    expect(region).toHaveAttribute('aria-live', 'polite');
    expect(region).toHaveAttribute('aria-atomic', 'true');
  });

  it('elevates error toasts to role=alert (assertive)', () => {
    render(
      <ToastProvider>
        <Trigger message="something broke" type="error" duration={5000} />
      </ToastProvider>
    );

    act(() => {
      screen.getByText('show').click();
    });

    expect(screen.getByRole('alert')).toHaveTextContent('something broke');
  });
});
