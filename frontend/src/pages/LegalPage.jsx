import legalPages from '../content/legalPages.json';

const EMAIL_RE = /([\w.+-]+@[\w-]+\.[\w.-]*\w)/;

function withEmailLinks(text) {
  return text.split(EMAIL_RE).map((part, i) => (
    i % 2 === 1
      ? <a key={i} href={`mailto:${part}`} className="text-[var(--cp-accent)] underline">{part}</a>
      : part
  ));
}

export default function LegalPage({ page }) {
  const content = legalPages[page];
  const other = page === 'privacy' ? 'terms' : 'privacy';
  return (
    <main id="main-content" className="flex-1 w-full max-w-3xl mx-auto px-4 py-10 text-[var(--cp-text)]">
      <h1 className="text-3xl font-bold mb-8">{content.title}</h1>
      {content.sections.map(([heading, text]) => (
        <section key={heading} className="mb-6">
          <h2 className="text-xl font-semibold mb-2">{heading}</h2>
          <p className="leading-relaxed">{withEmailLinks(text)}</p>
        </section>
      ))}
      <p>
        See also our{' '}
        <a className="text-[var(--cp-accent)] underline" href={`/${other}`}>
          {legalPages[other].title}
        </a>
        .
      </p>
    </main>
  );
}
