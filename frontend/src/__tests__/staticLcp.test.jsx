import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import {
  STATIC_LCP_STALL_MS,
  staticLcpCoversHero,
  staticLcpHiddenForRoute,
  useStaticLcpCoversHero,
  watchStaticLcp,
} from '../utils/staticLcp';

function paint(src = '/api/inventory/lcp-image') {
  const image = document.createElement('img');
  image.id = 'tho-lcp';
  image.alt = '';
  image.setAttribute('src', src);
  document.body.appendChild(image);
  return image;
}

function Probe() {
  const covers = useStaticLcpCoversHero();
  return <div>{covers ? 'covering' : 'fallback'}</div>;
}

afterEach(() => {
  document.getElementById('tho-lcp')?.remove();
  vi.useRealTimers();
});

describe('static LCP hero', () => {
  it('keeps a failed image hidden on routes that show the hero', () => {
    const image = paint();
    expect(staticLcpHiddenForRoute(image, true)).toBe(false);
    image.setAttribute('data-lcp-failed', '1');
    expect(staticLcpHiddenForRoute(image, true)).toBe(true);
    expect(staticLcpHiddenForRoute(image, false)).toBe(true);
  });

  it('hides an image that already finished broken and does not cover the hero', () => {
    const image = paint();
    Object.defineProperty(image, 'complete', { configurable: true, get: () => true });
    Object.defineProperty(image, 'naturalWidth', { configurable: true, get: () => 0 });
    expect(staticLcpCoversHero(image)).toBe(false);
    watchStaticLcp(image);
    expect(image.hidden).toBe(true);
    expect(image.getAttribute('data-lcp-failed')).toBe('1');
  });

  it('retires a request that never loads', () => {
    vi.useFakeTimers();
    const stalled = paint();
    const stop = watchStaticLcp(stalled);
    act(() => { vi.advanceTimersByTime(STATIC_LCP_STALL_MS); });
    expect(stalled.hidden).toBe(true);
    expect(stalled.getAttribute('data-lcp-failed')).toBe('1');
    stop();
  });

  it('keeps a loaded image after the stall window', () => {
    vi.useFakeTimers();
    const loaded = paint();
    const stop = watchStaticLcp(loaded);
    act(() => { loaded.dispatchEvent(new Event('load')); });
    act(() => { vi.advanceTimersByTime(STATIC_LCP_STALL_MS); });
    expect(loaded.hidden).toBe(false);
    expect(loaded.getAttribute('data-lcp-failed')).toBeNull();
    expect(loaded.getAttribute('data-lcp-loaded')).toBe('1');
    stop();
  });

  it('tells React to fall back only after the image errors', () => {
    const image = paint();
    render(<Probe />);
    expect(screen.getByText('covering')).toBeInTheDocument();
    act(() => { image.dispatchEvent(new Event('error')); });
    expect(screen.getByText('fallback')).toBeInTheDocument();
    expect(image.hidden).toBe(true);
  });
});
