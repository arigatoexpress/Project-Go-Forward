import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, screen } from '@testing-library/react';
import { ToastProvider, useToast } from '../components/Toast';

function Trigger({ message, duration }) {
  const { addToast } = useToast();
  return (
    <button onClick={() => addToast(message, 'info', duration)}>show</button>
  );
}

describe('Toast', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows a toast when addToast is called and auto-dismisses after the duration', () => {
    render(
      <ToastProvider>
        <Trigger message="Hello, THO" duration={3000} />
      </ToastProvider>,
    );

    // Show the toast.
    act(() => {
      screen.getByText('show').click();
    });

    expect(screen.getByText('Hello, THO')).toBeInTheDocument();

    // Auto-dismiss after the configured duration.
    act(() => {
      vi.advanceTimersByTime(3000);
    });

    expect(screen.queryByText('Hello, THO')).not.toBeInTheDocument();
  });

  it('does not inject a style element (CSP style-src has no unsafe-inline)', async () => {
    document.querySelectorAll('style').forEach((el) => el.remove());
    vi.resetModules();
    await import('../components/Toast');
    expect(document.querySelector('style')).toBeNull();
  });
});
