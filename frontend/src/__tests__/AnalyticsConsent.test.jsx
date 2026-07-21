import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import AnalyticsConsent from '../components/AnalyticsConsent';

describe('AnalyticsConsent', () => {
  beforeEach(() => {
    localStorage.clear();
    window.__THO_ANALYTICS_CONFIGURED__ = true;
    window.__THO_ENABLE_ANALYTICS__ = vi.fn();
    window.__THO_DISABLE_ANALYTICS__ = vi.fn();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    delete window.__THO_ANALYTICS_CONFIGURED__;
    delete window.__THO_ENABLE_ANALYTICS__;
    delete window.__THO_DISABLE_ANALYTICS__;
  });

  it('stays hidden when no analytics provider is configured', () => {
    window.__THO_ANALYTICS_CONFIGURED__ = false;
    render(<AnalyticsConsent />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('stores an explicit grant before enabling analytics', () => {
    render(<AnalyticsConsent />);
    fireEvent.click(screen.getByRole('button', { name: 'Allow analytics' }));
    expect(localStorage.getItem('tho_analytics_consent_v1')).toBe('granted');
    expect(window.__THO_ENABLE_ANALYTICS__).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('stores a denial and never loads analytics', () => {
    render(<AnalyticsConsent />);
    fireEvent.click(screen.getByRole('button', { name: 'No thanks' }));
    expect(localStorage.getItem('tho_analytics_consent_v1')).toBe('denied');
    expect(window.__THO_DISABLE_ANALYTICS__).toHaveBeenCalledTimes(1);
    expect(window.__THO_ENABLE_ANALYTICS__).not.toHaveBeenCalled();
  });

  it('does not ask again after a saved choice', () => {
    localStorage.setItem('tho_analytics_consent_v1', 'denied');
    render(<AnalyticsConsent />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
