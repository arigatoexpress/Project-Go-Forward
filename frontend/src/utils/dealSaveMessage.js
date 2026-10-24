import { extractErrorMessage, safeUserMessage } from './apiError';

export const DEAL_NOT_SAVED = 'Nothing was saved. This deal is not in the list.';

export function dealSaveMessage(body) {
  const extracted = safeUserMessage(extractErrorMessage(body), '');
  if (extracted && /^could not save this deal\b/i.test(extracted)) {
    return `${extracted} ${DEAL_NOT_SAVED}`;
  }
  return `Could not save this deal. ${DEAL_NOT_SAVED} Check the connection and try again.`;
}
