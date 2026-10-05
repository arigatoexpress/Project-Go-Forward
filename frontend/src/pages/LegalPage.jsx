import legalPages from '../content/legalPages.json';

export default function LegalPage({ page }) {
  const content = legalPages[page];
  return (
    <main className="flex-1 w-full max-w-3xl mx-auto px-4 py-10 text-[var(--cp-text)]">
      <h1 className="text-3xl font-bold mb-8">{content.title}</h1>
      {content.sections.map(([heading, text]) => (
        <section key={heading} className="mb-6">
          <h2 className="text-xl font-semibold mb-2">{heading}</h2>
          <p className="leading-relaxed">{text}</p>
        </section>
      ))}
      <a className="text-[var(--cp-accent)] underline" href={page === 'privacy' ? '/terms' : '/privacy'}>
        {page === 'privacy' ? 'Terms of Use' : 'Privacy Policy'}
      </a>
    </main>
  );
}
