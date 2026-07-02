// Review-request helper — pure message/link builders.
//
// Staff use these to send past customers a prefilled Google-review request.
// NOTHING auto-sends: we only build sms:/mailto: hrefs the staff member taps,
// so their own phone/email client opens with the message ready to go.
import { BUSINESS_NAME, BUSINESS_PHONE } from './constants';

export const SALESPERSON_STORAGE_KEY = 'tho_salesperson_name';

/** First word of a full name, for a friendly greeting. */
export function firstNameOf(name) {
  const first = (name || '').trim().split(/\s+/)[0];
  return first || 'there';
}

/**
 * Build the review-request message body.
 * Includes the greeting, salesperson attribution, the review link, and the
 * "call me first" safety valve so an unhappy customer reaches us before Google.
 */
export function buildReviewMessage({ customerName, salesperson, reviewLink }) {
  const from = (salesperson || '').trim()
    ? `${salesperson.trim()} at ${BUSINESS_NAME}`
    : BUSINESS_NAME;
  return (
    `Hi ${firstNameOf(customerName)}! This is ${from}. ` +
    `Thank you for choosing us for your new home — it was a pleasure working with you. ` +
    `If you have a minute, would you leave us a quick Google review? It really helps: ${reviewLink}\n\n` +
    `And if it wasn't a 5-star experience, please call me first at ${BUSINESS_PHONE} so I can make it right.`
  );
}

/** sms: href with prefilled body. `?&body=` form works on both iOS and Android. */
export function buildSmsHref(phone, body) {
  const digits = (phone || '').replace(/[^\d+]/g, '');
  return `sms:${digits}?&body=${encodeURIComponent(body)}`;
}

/** mailto: href with prefilled subject and body. */
export function buildMailtoHref(email, subject, body) {
  return `mailto:${encodeURIComponent(email || '')}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}
