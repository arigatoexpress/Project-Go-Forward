const JOURNEY_STORAGE_KEY = 'tho_journey_id';
const JOURNEY_ID_PATTERN = /^j_[0-9a-f]{32}$/;

function generateJourneyId() {
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  return `j_${Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
}

export function getJourneyId() {
  try {
    const existing = sessionStorage.getItem(JOURNEY_STORAGE_KEY);
    if (JOURNEY_ID_PATTERN.test(existing || '')) return existing;
    const generated = generateJourneyId();
    sessionStorage.setItem(JOURNEY_STORAGE_KEY, generated);
    return generated;
  } catch {
    return generateJourneyId();
  }
}

export function getJourneyAttribution() {
  return { journey_id: getJourneyId() };
}
