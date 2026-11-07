import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { MessageSquare, X } from 'lucide-react';
import {
  PHONE_CHAT_MEDIA,
  currentChatViewport,
  isPhoneChatViewport,
  markChatEntry,
  markPromptDismissed,
  markPromptShown,
  promptAlreadyShown,
  promptDismissed,
  trackChatEvent,
} from '../chatAnalytics';

const STOREFRONT_PAGES = new Set([
  'inventory',
  'contact',
  'appointments',
  'about',
  'financing',
  'faq',
  'warranty',
  'delivery',
  'locations',
  'privacy',
  'terms',
  'not-found',
]);

function usePhoneChatViewport() {
  const [phone, setPhone] = useState(isPhoneChatViewport);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const query = window.matchMedia(PHONE_CHAT_MEDIA);
    const apply = () => setPhone(query.matches);
    apply();
    query.addEventListener('change', apply);
    return () => query.removeEventListener('change', apply);
  }, []);

  return phone;
}

export default function MobileChatPrompt({ activePage, onOpenChat }) {
  const phone = usePhoneChatViewport();
  const eligible = phone && STOREFRONT_PAGES.has(activePage);
  const [dismissed, setDismissed] = useState(promptDismissed);

  useEffect(() => {
    if (!eligible || dismissed || promptAlreadyShown()) return;
    markPromptShown();
    trackChatEvent('chat_prompt_shown', {
      source: 'mobile_prompt',
      placement: 'mobile_prompt',
      viewport: currentChatViewport(),
      page_path: window.location.pathname || '/',
    });
  }, [dismissed, eligible]);

  if (!eligible || typeof document === 'undefined') return null;

  const open = () => {
    markChatEntry(dismissed ? 'mobile_launcher' : 'mobile_prompt');
    onOpenChat();
  };

  const dismiss = () => {
    markPromptDismissed();
    setDismissed(true);
    trackChatEvent('chat_prompt_dismissed', {
      source: 'mobile_prompt',
      placement: 'mobile_prompt',
      viewport: 'phone',
      page_path: window.location.pathname || '/',
    });
  };

  const node = dismissed ? (
    <button
      type="button"
      onClick={open}
      data-testid="mobile-chat-launcher"
      className="tho-mobile-chat-prompt fixed right-3 z-[55] inline-flex min-h-14 items-center gap-2 rounded-full bg-[var(--cp-accent)] px-5 text-base font-bold text-[var(--cp-bg)] shadow-xl"
      style={{ bottom: 'max(0.75rem, env(safe-area-inset-bottom))' }}
    >
      <MessageSquare size={22} aria-hidden="true" />
      Chat with Tex
    </button>
  ) : (
    <section
      data-testid="mobile-chat-prompt"
      aria-label="Chat with Tex"
      className="tho-mobile-chat-prompt tho-mobile-chat-card fixed inset-x-3 z-[55] rounded-2xl border border-[var(--cp-border)] bg-[var(--cp-panel)] p-4 text-[var(--cp-text)] shadow-2xl"
      style={{ bottom: 'max(3.25rem, calc(env(safe-area-inset-bottom) + 2.75rem))' }}
    >
      <div className="tho-mobile-chat-heading flex min-w-0 flex-1 items-start gap-3">
        <img src="/tex-icon.svg" alt="" className="h-12 w-12 shrink-0 rounded-full" />
        <div className="min-w-0 flex-1 pt-0.5">
          <p className="text-base font-bold leading-snug">Chat with Tex</p>
          <p className="tho-mobile-chat-copy mt-1 text-sm leading-snug text-[var(--cp-text-secondary)]">
            Questions about a home, a price, or a visit? Ask here.
          </p>
        </div>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss chat prompt"
          className="inline-flex min-h-12 min-w-12 shrink-0 items-center justify-center rounded-full text-[var(--cp-text)] hover:bg-[var(--cp-surface)]"
        >
          <X size={22} aria-hidden="true" />
        </button>
      </div>
      <button
        type="button"
        onClick={open}
        className="tho-mobile-chat-open mt-3 inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-[var(--cp-accent)] px-4 text-base font-bold text-[var(--cp-bg)]"
      >
        <MessageSquare size={20} aria-hidden="true" />
        Chat with Tex
      </button>
    </section>
  );

  return createPortal(node, document.body);
}
