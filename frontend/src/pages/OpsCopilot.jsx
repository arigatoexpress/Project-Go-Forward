/**
 * Ops Copilot — in-app, admin/employee-only assistant.
 *
 * The GCP-native (Vertex/Gemini) in-app replacement for the external Telegram
 * bot. Staff ask it questions about live business data (leads, appointments,
 * inventory, deals, feedback) or how to use the platform, and get answers right
 * here. Read-only by design: it looks things up, it never changes anything.
 */

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Bot, Send, Loader2, Lock, User, Sparkles } from 'lucide-react';
import adminFetch from '../adminFetch';
import SafeMarkdown from '../components/SafeMarkdown';

// Map the prose typography colors onto the app theme tokens so markdown stays
// readable in both light and dark mode (prose otherwise hardcodes gray text,
// which is unreadable on the dark assistant bubble).
const MD_THEME =
  '[--tw-prose-body:var(--cp-text)] [--tw-prose-headings:var(--cp-text)] ' +
  '[--tw-prose-bold:var(--cp-text)] [--tw-prose-bullets:var(--cp-muted)] ' +
  '[--tw-prose-counters:var(--cp-muted)] [--tw-prose-links:var(--cp-accent)]';

const SUGGESTIONS = [
  'How many new leads do we have?',
  "What's on the appointment schedule?",
  'How do I upload photos for a home?',
  'How do I log into the back office?',
];

function Bubble({ role, content }) {
  const isUser = role === 'user';
  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div
        className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
          isUser ? 'bg-[var(--cp-accent)]/15' : 'bg-[var(--cp-panel)] border border-[var(--cp-border)]'
        }`}
      >
        {isUser ? (
          <User size={16} className="text-[var(--cp-accent)]" />
        ) : (
          <Bot size={16} className="text-[var(--cp-accent)]" />
        )}
      </div>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-3 text-[15px] leading-relaxed ${
          isUser
            ? 'bg-[var(--cp-accent)] text-white rounded-tr-sm whitespace-pre-wrap'
            : 'bg-[var(--cp-panel)] text-[var(--cp-text)] border border-[var(--cp-border)] rounded-tl-sm'
        }`}
      >
        {isUser ? (
          content
        ) : (
          <div className={MD_THEME}>
            <SafeMarkdown content={content} />
          </div>
        )}
      </div>
    </div>
  );
}

export default function OpsCopilot() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, loading]);

  const send = useCallback(
    async (text) => {
      const message = (text ?? input).trim();
      if (!message || loading) return;

      const history = messages.map((m) => ({ role: m.role, content: m.content }));
      setMessages((prev) => [...prev, { role: 'user', content: message }]);
      setInput('');
      setLoading(true);

      try {
        const res = await adminFetch('/api/admin/copilot', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message, history }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setMessages((prev) => [
          ...prev,
          { role: 'assistant', content: data.reply || 'Sorry, I had no answer for that.' },
        ]);
      } catch {
        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            content:
              "Sorry — I couldn't reach the assistant just now. Please try again in a moment.",
          },
        ]);
      } finally {
        setLoading(false);
      }
    },
    [input, loading, messages]
  );

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="max-w-3xl mx-auto px-4 py-6 flex flex-col" style={{ height: 'calc(100vh - 4rem)' }}>
      {/* Header */}
      <div className="flex items-center gap-3 mb-2">
        <div className="w-10 h-10 rounded-xl bg-[var(--cp-accent)]/15 flex items-center justify-center">
          <Sparkles size={20} className="text-[var(--cp-accent)]" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-[var(--cp-text)]">Ops Copilot</h1>
          <p className="text-sm text-[var(--cp-muted)] flex items-center gap-1.5">
            <Lock size={12} /> Staff-only · Read-only — I look things up, I don't change anything.
          </p>
        </div>
      </div>

      {/* Conversation */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto space-y-4 py-4 px-1"
      >
        {messages.length === 0 && (
          <div className="text-center py-10">
            <Bot size={40} className="mx-auto text-[var(--cp-accent)] mb-3" />
            <p className="text-[var(--cp-text)] font-medium mb-1">Hi! I'm your Ops Copilot.</p>
            <p className="text-sm text-[var(--cp-muted)] mb-6">
              Ask me about leads, appointments, inventory — or how to use the platform.
            </p>
            <div className="flex flex-wrap gap-2 justify-center">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="text-sm px-3 py-2 rounded-full border border-[var(--cp-border)] text-[var(--cp-text)] hover:border-[var(--cp-accent)] hover:text-[var(--cp-accent)] transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <Bubble key={i} role={m.role} content={m.content} />
        ))}

        {loading && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 bg-[var(--cp-panel)] border border-[var(--cp-border)]">
              <Bot size={16} className="text-[var(--cp-accent)]" />
            </div>
            <div className="rounded-2xl rounded-tl-sm px-4 py-3 bg-[var(--cp-panel)] border border-[var(--cp-border)]">
              <Loader2 size={16} className="animate-spin text-[var(--cp-muted)]" />
            </div>
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="mt-3 flex items-end gap-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder="Ask about leads, appointments, or how to do something…"
          className="flex-1 resize-none rounded-xl border border-[var(--cp-border)] bg-[var(--cp-panel)] text-[var(--cp-text)] px-4 py-3 focus:outline-none focus:border-[var(--cp-accent)] max-h-32"
        />
        <button
          onClick={() => send()}
          disabled={loading || !input.trim()}
          className="rounded-xl bg-[var(--cp-accent)] text-white px-4 py-3 disabled:opacity-40 hover:opacity-90 transition-opacity"
          aria-label="Send"
        >
          {loading ? <Loader2 size={18} className="animate-spin" /> : <Send size={18} />}
        </button>
      </div>
    </div>
  );
}
