import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import AnalyticsConsent from '../components/AnalyticsConsent';
import { getJourneyAttribution } from '../utils/attribution';

describe('AnalyticsConsent', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    window.history.replaceState({}, '', '/inventory');
    window.__THO_ANALYTICS_CONFIGURED__ = true;
    window.__THO_ENABLE_ANALYTICS__ = vi.fn();
    window.__THO_DISABLE_ANALYTICS__ = vi.fn();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    sessionStorage.clear();
    delete window.__THO_ANALYTICS_CONFIGURED__;
    delete window.__THO_ENABLE_ANALYTICS__;
    delete window.__THO_DISABLE_ANALYTICS__;
    delete window.gtag;
    window.history.replaceState({}, '', '/');
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

  it('never emits a grant-time page view from an admin/noindex route', () => {
    const gtag = vi.fn();
    window.gtag = gtag;
    window.history.replaceState({}, '', '/crm');

    render(<AnalyticsConsent />);
    fireEvent.click(screen.getByRole('button', { name: 'Allow analytics' }));

    expect(window.__THO_ENABLE_ANALYTICS__).toHaveBeenCalledTimes(1);
    expect(gtag).not.toHaveBeenCalledWith('event', 'page_view', expect.anything());
  });

  it('restores the landing campaign onto the grant-time page view', () => {
    window.history.replaceState({}, '', (
      '/inventory?utm_source=google&utm_medium=cpc'
      + '&utm_campaign=tho_search_high_intent_huffman&gclid=test-gclid-123456'
    ));
    getJourneyAttribution();
    window.history.pushState({}, '', '/inventory');
    const gtag = vi.fn();
    window.gtag = gtag;

    render(<AnalyticsConsent />);
    fireEvent.click(screen.getByRole('button', { name: 'Allow analytics' }));

    expect(window.location.search).toContain('utm_campaign=tho_search_high_intent_huffman');
    expect(window.location.search).toContain('gclid=test-gclid-123456');
    const params = gtag.mock.calls.find((call) => call[0] === 'event' && call[1] === 'page_view')[2];
    expect(params).toMatchObject({
      page_path: '/inventory',
      utm_source: 'google',
      utm_medium: 'cpc',
      utm_campaign: 'tho_search_high_intent_huffman',
      gclid: 'test-gclid-123456',
      tho_event: 'page_viewed',
    });
    expect(params.page_location).toContain('gclid=test-gclid-123456');
    expect(params).not.toHaveProperty('journey_id');
    expect(params).not.toHaveProperty('email');
  });
});
