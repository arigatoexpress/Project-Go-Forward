export default function SkipLink() {
  return (
    <a
      href="#main-content"
      className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-[200] focus:px-4 focus:py-2 focus:rounded-lg focus:bg-[var(--cp-accent)] focus:text-[var(--cp-bg)] focus:shadow-lg"
    >
      Skip to main content
    </a>
  );
}
