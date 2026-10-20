import {
  cappedListingLinks,
  cityFactsForSlug,
  paragraphParts,
} from '../content/cityLocal';
import StorefrontLinks from '../components/StorefrontLinks';
import {
  BUSINESS_CITY,
  BUSINESS_FULL_ADDRESS,
  BUSINESS_NAME,
  BUSINESS_PHONE,
} from '../constants';

function LinkedParagraph({ text }) {
  return (
    <p className="mt-3 text-base leading-relaxed text-[var(--cp-text-secondary)]">
      {paragraphParts(text).map((part, index) => (
        part.type === 'link'
          ? <a key={index} href={part.href} className="font-semibold text-[var(--cp-accent)] underline">{part.text}</a>
          : <span key={index}>{part.text}</span>
      ))}
    </p>
  );
}

export default function CityLanding({ cityLanding, homes }) {
  const facts = cityFactsForSlug(cityLanding.slug);
  const listings = cappedListingLinks(homes);
  return (
    <div className="min-h-screen bg-[var(--cp-bg)] text-[var(--cp-text)]">
      <article className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
        <nav aria-label="Breadcrumb" className="text-sm">
          <ol className="flex flex-wrap items-center gap-2">
            <li><a href="/" className="font-semibold text-[var(--cp-accent)] underline">Home</a></li>
            <li aria-hidden="true">/</li>
            <li><a href="/locations" className="font-semibold text-[var(--cp-accent)] underline">Locations</a></li>
            <li aria-hidden="true">/</li>
            <li><span aria-current="page">Homes in {cityLanding.city}, TX</span></li>
          </ol>
        </nav>
        <h1 className="mt-4 text-3xl font-extrabold leading-tight sm:text-4xl">
          {cityLanding.heading}
        </h1>
        <p className="mt-4 text-base leading-relaxed text-[var(--cp-text-secondary)]">
          {`${BUSINESS_NAME} delivers manufactured and mobile homes to ${cityLanding.city}, TX from the ${BUSINESS_CITY} showroom at ${BUSINESS_FULL_ADDRESS}. Call ${BUSINESS_PHONE} for the current price.`}
        </p>
        {facts && (
          <section aria-label={facts.heading} className="mt-8">
            <h2 className="text-2xl font-bold">{facts.heading}</h2>
            {facts.paragraphs.map((paragraph) => (
              <LinkedParagraph key={paragraph.slice(0, 48)} text={paragraph} />
            ))}
          </section>
        )}
        <p className="mt-6 text-base leading-relaxed">
          Homes and floorplans offered by our {BUSINESS_CITY} showroom:
        </p>
        <ul className="mt-3 list-disc space-y-2 pl-5">
          {listings.map((item) => (
            <li key={item.path}>
              <a href={item.path} className="font-semibold text-[var(--cp-accent)] underline">{item.label}</a>
            </li>
          ))}
        </ul>
        <p className="mt-4">
          <a href="/appointments" className="font-semibold text-[var(--cp-accent)] underline">Book a showroom visit</a>
        </p>
        <StorefrontLinks currentPath={cityLanding.path} tone="dark" showHome={false} />
      </article>
    </div>
  );
}
