import { SERVICE_AREA_CITIES } from '../constants';

function serviceAreaPath(city) {
  const slug = String(city || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return `/manufactured-homes-in-${slug}-tx`;
}

export default function StorefrontLinks({ currentPath, tone = 'light', showHome = true }) {
  const path = String(currentPath || '/').replace(/\/+$/, '') || '/';
  const linkClass = tone === 'light'
    ? 'font-semibold text-white underline'
    : 'font-semibold text-[var(--cp-accent)] underline';
  const currentClass = tone === 'light'
    ? 'font-semibold text-white'
    : 'font-semibold text-[var(--cp-text)]';
  return (
    <nav aria-label="Home, inventory, and areas we serve" className="mt-4 flex flex-wrap gap-x-3 gap-y-2 text-sm">
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
