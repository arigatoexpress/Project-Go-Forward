import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import QuickActions from '../components/QuickActions';

describe('QuickActions', () => {
  it('renders a real tel: click-to-call link (highest-intent mobile action)', () => {
    render(<QuickActions onActionClick={() => {}} disabled={false} />);
    const call = screen.getByRole('link', { name: /Call/i });
    expect(call).toHaveAttribute('href', 'tel:+12813243020');
  });

  it('keeps chat actions as buttons, not links', () => {
    render(<QuickActions onActionClick={() => {}} disabled={false} />);
    expect(screen.getByRole('button', { name: /Find a Home/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Service\/Warranty/i })).toBeInTheDocument();
  });

  it('fires onActionClick with the chat message for chat buttons', () => {
    const onActionClick = vi.fn();
    render(<QuickActions onActionClick={onActionClick} disabled={false} />);
    screen.getByRole('button', { name: /Find a Home/i }).click();
    expect(onActionClick).toHaveBeenCalledWith("I'd like to find a home.");
  });
});
