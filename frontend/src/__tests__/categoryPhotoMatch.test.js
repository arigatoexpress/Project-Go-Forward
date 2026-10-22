import { describe, expect, it } from 'vitest';
import { matchCategoryPhoto } from '../pages/InventoryBrowse';

const PNG = 'https://cdn.example.com/manufacturer/1/floorplan/2/5.png';
const JPG = 'https://cdn.example.com/manufacturer/1/floorplan/2/5.jpg';
const WEBP = 'https://cdn.example.com/manufacturer/1/floorplan/2/5.webp';
const FIFTEEN = 'https://cdn.example.com/manufacturer/1/floorplan/2/15.png';
const TEN = 'https://cdn.example.com/manufacturer/1/floorplan/2/10.jpg';
const ELEVEN = 'https://cdn.example.com/manufacturer/1/floorplan/2/11.png';

describe('matchCategoryPhoto', () => {
  it('maps a category filename onto the one gallery photo with the same stem', () => {
    expect(matchCategoryPhoto('5.jpg', [FIFTEEN, PNG, TEN])).toBe(PNG);
  });

  it('does not treat a longer numeric filename as the same stem', () => {
    expect(matchCategoryPhoto('5.jpg', [FIFTEEN, TEN, ELEVEN])).toBe('');
    expect(matchCategoryPhoto('1.jpg', [ELEVEN, TEN])).toBe('');
  });

  it('does not guess when two gallery photos share the stem', () => {
    expect(matchCategoryPhoto('5.jpg', [PNG, WEBP])).toBe('');
  });

  it('prefers an exact filename over a same-stem sibling', () => {
    expect(matchCategoryPhoto('5.jpg', [PNG, JPG])).toBe(JPG);
  });
});
