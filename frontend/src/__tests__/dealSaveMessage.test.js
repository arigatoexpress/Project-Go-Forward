import { describe, expect, it } from 'vitest';
import { dealSaveMessage } from '../utils/dealSaveMessage';

describe('deal save message', () => {
  it('keeps the field name and says nothing was saved', () => {
    expect(dealSaveMessage({
      success: false,
      message: 'Could not save this deal. Unexpected field: buyer_nickname. Remove them and try again.',
    })).toBe(
      'Could not save this deal. Unexpected field: buyer_nickname. Remove them and try again. Nothing was saved. This deal is not in the list.',
    );
  });

  it('uses the same plain line when the server message is missing', () => {
    expect(dealSaveMessage(null)).toBe(
      'Could not save this deal. Nothing was saved. This deal is not in the list. Check the connection and try again.',
    );
    expect(dealSaveMessage({ error: 'Network error' })).toBe(
      'Could not save this deal. Nothing was saved. This deal is not in the list. Check the connection and try again.',
    );
  });
});
