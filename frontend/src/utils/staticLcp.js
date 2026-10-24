import { useSyncExternalStore } from 'react';

// The server paints #tho-lcp before JavaScript. It only helps first paint
// while that image actually loads. A 404, an encode failure, or a request
// that never settles must give the hero back to the original photo.
export const STATIC_LCP_ELEMENT_ID = 'tho-lcp';
export const STATIC_LCP_STALL_MS = 8000;
const FAILED_ATTR = 'data-lcp-failed';
const LOADED_ATTR = 'data-lcp-loaded';
const FAILED_EVENT = 'tho-lcp-failed';

export function staticLcpElement() {
  if (typeof document === 'undefined') return null;
  return document.getElementById(STATIC_LCP_ELEMENT_ID);
}

function hasSrc(image) {
  return Boolean(image?.getAttribute('src'));
}

export function staticLcpFailed(image = staticLcpElement()) {
  return Boolean(image) && image.getAttribute(FAILED_ATTR) === '1';
}

function markedLoaded(image) {
  return Boolean(image) && image.getAttribute(LOADED_ATTR) === '1';
}

function loadedSuccessfully(image) {
  if (!image || staticLcpFailed(image)) return false;
  if (markedLoaded(image)) return true;
  return hasSrc(image) && image.complete && image.naturalWidth > 0;
}

// A finished request with no pixels is a broken image. An in-flight request
// has complete === false, so a slow success is not treated as a failure.
function settledBroken(image) {
  if (!image || staticLcpFailed(image) || markedLoaded(image)) return false;
  return hasSrc(image) && image.complete && image.naturalWidth === 0;
}

export function staticLcpCoversHero(image = staticLcpElement()) {
  if (!image || staticLcpFailed(image) || settledBroken(image)) return false;
  return true;
}

// Keep a failed hero hidden even when a route change would otherwise show it.
export function staticLcpHiddenForRoute(image, showOnThisRoute) {
  if (!image) return false;
  return !showOnThisRoute || staticLcpFailed(image);
}

function hideFailedLcp(image) {
  if (!image || staticLcpFailed(image)) return false;
  image.setAttribute(FAILED_ATTR, '1');
  image.hidden = true;
  return true;
}

export function retireStaticLcp(image = staticLcpElement()) {
  if (!hideFailedLcp(image)) return;
  image.dispatchEvent(new Event(FAILED_EVENT));
}

export function watchStaticLcp(image = staticLcpElement()) {
  if (!image || staticLcpFailed(image)) return () => {};
  if (loadedSuccessfully(image)) {
    image.setAttribute(LOADED_ATTR, '1');
    return () => {};
  }
  if (settledBroken(image)) {
    // The snapshot already says this image does not cover the hero.
    // Hiding it here must not notify, or the store subscriber re-enters.
    hideFailedLcp(image);
    return () => {};
  }

  let timer = 0;
  const onError = () => {
    window.clearTimeout(timer);
    retireStaticLcp(image);
  };
  const onLoad = () => {
    window.clearTimeout(timer);
    image.setAttribute(LOADED_ATTR, '1');
  };
  image.addEventListener('error', onError);
  image.addEventListener('load', onLoad);
  if (hasSrc(image)) {
    timer = window.setTimeout(() => {
      if (markedLoaded(image) || staticLcpFailed(image) || loadedSuccessfully(image)) {
        if (loadedSuccessfully(image)) image.setAttribute(LOADED_ATTR, '1');
        return;
      }
      retireStaticLcp(image);
    }, STATIC_LCP_STALL_MS);
  }
  return () => {
    image.removeEventListener('error', onError);
    image.removeEventListener('load', onLoad);
    window.clearTimeout(timer);
  };
}

function subscribeStaticLcp(onChange) {
  const image = staticLcpElement();
  if (!image) return () => {};
  const onFailed = () => onChange();
  image.addEventListener(FAILED_EVENT, onFailed);
  const stop = watchStaticLcp(image);
  return () => {
    stop();
    image.removeEventListener(FAILED_EVENT, onFailed);
  };
}

function staticLcpServerSnapshot() {
  return false;
}

export function useStaticLcpCoversHero() {
  return useSyncExternalStore(
    subscribeStaticLcp,
    staticLcpCoversHero,
    staticLcpServerSnapshot,
  );
}
