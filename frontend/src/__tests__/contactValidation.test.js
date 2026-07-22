import { describe, expect, it } from 'vitest';
import { normalizeOptionalEmail } from '../utils/contactValidation';

describe('normalizeOptionalEmail', () => {
  it('trims and returns a complete email address', () => {
    expect(normalizeOptionalEmail('  ari@example.com  ')).toBe('ari@example.com');
  });

  it.each(['', '   ', 'ari@', 'ari.example.com', '@example.com']) (
    'omits an empty or incomplete optional email: %j',
    (value) => {
      expect(normalizeOptionalEmail(value)).toBeUndefined();
    },
  );
});
