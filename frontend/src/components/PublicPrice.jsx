import { BUSINESS_PHONE, BUSINESS_PHONE_RAW } from '../constants';
import { isUnpricedListing, publicPriceText } from '../utils/publicPrice';

/**
 * Listing price. Unpriced homes say Call for Price and, when this label is
 * not already inside a link or button, show the showroom phone.
 * Pass linkPhone={false} inside a clickable card so the phone is not nested.
 */
export default function PublicPrice({ home, className = '', linkPhone = true }) {
  if (!isUnpricedListing(home) || !linkPhone) {
    return <span className={className}>{publicPriceText(home)}</span>;
  }
  return (
    <span className={className}>
      <span className={className}>Call for Price</span>
      {' · '}
      <a href={`tel:${BUSINESS_PHONE_RAW}`} className="underline">
        {BUSINESS_PHONE}
      </a>
    </span>
  );
}
