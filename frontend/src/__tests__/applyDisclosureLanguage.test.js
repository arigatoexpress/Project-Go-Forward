import { describe, it, expect } from 'vitest';
import { applyDisclosureLanguage } from '../pages/DocumentCenter';

const EN = 'TDHCA_1038_Consumer_Disclosure.pdf';
const ES = 'TDHCA_1040_Consumer_Disclosure_Spanish.pdf';

describe('applyDisclosureLanguage', () => {
  it('leaves the list unchanged for English (default)', () => {
    const list = ['TMHA_SalesContract.pdf', EN, 'TDHCA_1023-Statement-Ownership.pdf'];
    expect(applyDisclosureLanguage(list, 'en')).toEqual(list);
  });

  it('swaps the English disclosure for Spanish when es', () => {
    const list = ['TMHA_SalesContract.pdf', EN, 'TDHCA_1023-Statement-Ownership.pdf'];
    const out = applyDisclosureLanguage(list, 'es');
    expect(out).toContain(ES);
    expect(out).not.toContain(EN);
    // order + other docs preserved
    expect(out).toEqual(['TMHA_SalesContract.pdf', ES, 'TDHCA_1023-Statement-Ownership.pdf']);
  });

  it('never produces both editions (de-dupes) when Spanish already present', () => {
    const out = applyDisclosureLanguage([EN, ES], 'es');
    expect(out).toEqual([ES]);
  });

  it('strips a stray Spanish edition when English is chosen', () => {
    const out = applyDisclosureLanguage(['TMHA_SalesContract.pdf', ES], 'en');
    expect(out).toEqual(['TMHA_SalesContract.pdf']);
  });

  it('handles non-array input safely', () => {
    expect(applyDisclosureLanguage(undefined, 'es')).toEqual([]);
    expect(applyDisclosureLanguage(null, 'en')).toEqual([]);
  });
});
