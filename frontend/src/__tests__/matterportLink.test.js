import { describe, expect, it } from 'vitest';
import { canonicalMatterportId, canonicalMatterportUrl } from '../utils/matterportLink';

describe('canonical Matterport links', () => {
  it('keeps an 11 character id and rebuilds the show URL', () => {
    expect(canonicalMatterportId('JbALitnto3g')).toBe('JbALitnto3g');
    expect(canonicalMatterportUrl(' JbALitnto3g ')).toBe(
      'https://my.matterport.com/show/?m=JbALitnto3g&play=1',
    );
  });

  it('rejects markup, other hosts, and short ids', () => {
    for (const value of [
      '',
      'short',
      'JbALitnto3gX',
      '<script>alert(1)</script>',
      'javascript:alert(1)',
      'https://evil.example/show/?m=JbALitnto3g',
      'https://my.matterport.com/show/?m=JbALitnto3g',
    ]) {
      expect(canonicalMatterportId(value)).toBe('');
      expect(canonicalMatterportUrl(value)).toBe('');
    }
  });
});
