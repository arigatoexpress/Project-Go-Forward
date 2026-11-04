import { BUSINESS_CITY, SERVICE_AREA_CITIES } from '../constants';

function serviceAreaPath(city) {
  const slug = String(city || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return `/manufactured-homes-in-${slug}-tx`;
}

const TAP_LINK = 'inline-flex min-h-11 items-center rounded-md px-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--cp-accent)]';

export function AreasWeServe() {
  return (
    <section aria-labelledby="areas-we-serve-heading" className="border-b border-[var(--cp-border)] bg-[var(--cp-bg)]">
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <h2 id="areas-we-serve-heading" className="text-2xl font-bold text-[var(--cp-text)]">Areas we serve</h2>
        <p className="mt-2 max-w-2xl text-base leading-relaxed text-[var(--cp-text-secondary)]">
          The showroom is in {BUSINESS_CITY}. These pages are for towns around that lot.
        </p>
        <ul className="mt-4 flex flex-wrap gap-2">
          {SERVICE_AREA_CITIES.map((city) => (
            <li key={city}>
              <a
                href={serviceAreaPath(city)}
                className="inline-flex min-h-11 items-center rounded-md border border-[var(--cp-border)] bg-white px-3 text-sm font-semibold text-[var(--cp-secondary)] underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--cp-accent)]"
              >
                Homes in {city}, TX
              </a>
            </li>
          ))}
        </ul>
        <p className="mt-4">
          <a
            href="/locations"
            className="inline-flex min-h-11 items-center text-sm font-semibold text-[var(--cp-accent)] underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--cp-accent)]"
          >
            All locations
          </a>
        </p>
      </div>
    </section>
  );
}

export default function StorefrontLinks({ currentPath, tone = 'light', showHome = true }) {
  const path = String(currentPath || '/').replace(/\/+$/, '') || '/';
  const linkClass = tone === 'light'
    ? `${TAP_LINK} font-semibold text-white underline`
    : `${TAP_LINK} font-semibold text-[var(--cp-accent)] underline`;
  const currentClass = tone === 'light'
    ? `${TAP_LINK} font-semibold text-white`
    : `${TAP_LINK} font-semibold text-[var(--cp-text)]`;
  return (
    <nav aria-label="Home, inventory, and areas we serve" className="mt-4 flex flex-wrap gap-x-3 gap-y-1 text-sm">
      {showHome && path !== '/' && <a href="/" className={linkClass}>Home</a>}
      {path !== '/inventory' && <a href="/inventory" className={linkClass}>All homes</a>}
      {path === '/locations'
        ? <span aria-current="page" className={currentClass}>Locations</span>
        : <a href="/locations" className={linkClass}>Locations</a>}
      {SERVICE_AREA_CITIES.map((city) => {
        const href = serviceAreaPath(city);
        if (path === href) {
          return <span key={city} aria-current="page" className={currentClass}>Homes in {city}, TX</span>;
        }
        return <a key={city} href={href} className={linkClass}>Homes in {city}, TX</a>;
      })}
    </nav>
  );
}
