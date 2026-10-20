import { LOCATION_INDEX, cityFactsForSlug, cityPagePath, citySlug } from '../content/cityLocal';
import StorefrontLinks from '../components/StorefrontLinks';
import { SERVICE_AREA_CITIES } from '../constants';

export default function Locations() {
  const index = LOCATION_INDEX;
  return (
    <div className="min-h-screen bg-[var(--cp-bg)] text-[var(--cp-text)]">
      <article className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
        <nav aria-label="Breadcrumb" className="text-sm">
          <ol className="flex flex-wrap items-center gap-2">
            <li><a href="/" className="font-semibold text-[var(--cp-accent)] underline">Home</a></li>
            <li aria-hidden="true">/</li>
            <li><span aria-current="page">Locations</span></li>
          </ol>
        </nav>
        <h1 className="mt-4 text-3xl font-extrabold leading-tight sm:text-4xl">{index.heading}</h1>
        <p className="mt-4 text-base leading-relaxed text-[var(--cp-text-secondary)]">{index.intro}</p>
        <ul className="mt-6 space-y-4">
          {SERVICE_AREA_CITIES.map((city) => {
            const facts = cityFactsForSlug(citySlug(city));
            return (
              <li key={city}>
                <a href={cityPagePath(city)} className="font-semibold text-[var(--cp-accent)] underline">
                  Homes in {city}, TX
                </a>
                {facts?.index_blurb ? `. ${facts.index_blurb}` : null}
              </li>
            );
          })}
        </ul>
        <p className="mt-6 text-base leading-relaxed">
          <a href={index.inventory_href} className="font-semibold text-[var(--cp-accent)] underline">{index.inventory_label}</a>
          {'. '}
          <a href={index.contact_href} className="font-semibold text-[var(--cp-accent)] underline">{index.contact_label}</a>
          .
        </p>
        <StorefrontLinks currentPath="/locations" tone="dark" showHome={false} />
      </article>
    </div>
  );
}
