import { BUSINESS_PHONE, BUSINESS_PHONE_RAW } from '../constants';
import { isUnpricedListing, publicPriceText } from '../utils/publicPrice';

/**
 * Listing price. Unpriced homes say Call for Price and show the showroom phone.
 */
export default function PublicPrice({ home, className = '' }) {
  if (!isUnpricedListing(home)) {
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
