// Host rules for the candidate gauntlet. Keep the default URL in sync with
// scripts/candidate_load_sanity.py DEFAULT_CANDIDATE_URL.

export const DEFAULT_CANDIDATE_URL =
  'https://candidate---project-go-forward-trgi34bxuq-uc.a.run.app';

const LIVE_HOSTS = new Set([
  'www.texashomeoutlet.com',
  'texashomeoutlet.com',
  'tho.sapphirealpha.xyz',
]);

function hostnameOf(target) {
  return target.hostname.toLowerCase().replace(/\.$/, '');
}

export function isLiveHost(hostname) {
  const host = String(hostname || '').toLowerCase().replace(/\.$/, '');
  return LIVE_HOSTS.has(host) || host.endsWith('.texashomeoutlet.com');
}

export function candidateOrigin(baseURL) {
  const raw = String(baseURL || '').trim() || DEFAULT_CANDIDATE_URL;
  let target;
  try {
    target = new URL(raw);
  } catch {
    throw new Error('BASE_URL must be an HTTP(S) URL');
  }
  if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password) {
    throw new Error('BASE_URL must be an HTTP(S) URL without credentials');
  }
  const host = hostnameOf(target);
  if (isLiveHost(host)) {
    throw new Error(
      'Refusing the live storefront. The gauntlet runs only against the candidate tag host.',
    );
  }
  const loopback = host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === '[::1]';
  const candidate = host.startsWith('candidate---') && host.endsWith('.run.app');
  if (!loopback && !candidate) {
    throw new Error(
      'BASE_URL must be the Cloud Run candidate tag host (candidate---*.run.app) or a loopback host.',
    );
  }
  if (candidate && target.protocol !== 'https:') {
    throw new Error('The candidate tag URL must use https.');
  }
  return target.origin;
}
