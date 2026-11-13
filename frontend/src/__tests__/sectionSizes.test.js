import { describe, it, expect } from 'vitest';
import { joinIds, missingSectionSizeLabels } from '../pages/DocumentCenter';

// Mark Willcott, 2026-11-12: two-section homes need a size per section, and the
// single-slot forms need both section IDs.

describe('joinIds', () => {
  it('joins both section IDs', () => {
    expect(joinIds('LBL0100001', 'LBL0100002')).toBe('LBL0100001, LBL0100002');
  });
  it('drops blanks and duplicates', () => {
    expect(joinIds('A1', '')).toBe('A1');
    expect(joinIds('A1', ' A1 ')).toBe('A1');
    expect(joinIds(undefined, null)).toBe('');
  });
});

describe('missingSectionSizeLabels', () => {
  it('warns for both sections of a two-section home with no sizes', () => {
    expect(missingSectionSizeLabels({ no_of_sections: '2' })).toEqual([
      'Section 1 size (width x length)',
      'Section 2 size (width x length)',
    ]);
  });
  it('accepts the word form of the section count', () => {
    expect(missingSectionSizeLabels({ no_of_sections: 'Double Section' })).toHaveLength(2);
  });
  it('warns only for the section that is still blank', () => {
    expect(missingSectionSizeLabels({
      no_of_sections: '2', section_1_width: '14', section_1_length: '56',
    })).toEqual(['Section 2 size (width x length)']);
  });
  it('is quiet when both sections have a size', () => {
    expect(missingSectionSizeLabels({
      no_of_sections: '2',
      section_1_width: '14', section_1_length: '56',
      section_2_width: '14', section_2_length: '56',
    })).toEqual([]);
  });
  it('uses overall width and length for a single-section home', () => {
    expect(missingSectionSizeLabels({ no_of_sections: '1', width: '16', length: '76' })).toEqual([]);
    expect(missingSectionSizeLabels({ no_of_sections: 'single' })).toEqual([
      'Section 1 size (width x length)',
    ]);
  });
  it('never takes a multi-section size from the overall width and length', () => {
    expect(missingSectionSizeLabels({ no_of_sections: '2', width: '28', length: '56' })).toHaveLength(2);
  });
});
