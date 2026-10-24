import React, { useState, useEffect, useRef, lazy, Suspense, useCallback } from 'react';
import { Send, Home, Menu, X, Phone, MapPin, Loader2, User, Bot, FileText, Video, Lock, ShieldCheck, CalendarDays, Users, MessageSquare, MessageCircle, RotateCcw, WifiOff, Moon, Sun, KeyRound, BookOpen, Activity, Camera, Sparkles, ClipboardList, Store, MoreHorizontal, LogOut, Search } from 'lucide-react';
import { useDarkMode } from './hooks/useDarkMode';
import SearchFilters from './components/SearchFilters';
import QuickActions from './components/QuickActions';
import ComparisonDrawer from './components/ComparisonDrawer';
import { useToast } from './components/Toast';
import { useNetworkStatus } from './components/NetworkStatus';
import ReportIssue from './components/ReportIssue';
import ErrorBoundary from './components/ErrorBoundary';
import ClosureBanner from './components/ClosureBanner';
import ChatCallbackCard from './components/ChatCallbackCard';
import InventoryBrowse from './pages/InventoryBrowse';
import LegalPage from './pages/LegalPage';
import { CHAT_SESSION_STORAGE_KEY, adoptRunSessionId, resolveChatSession } from './chatSession';
import { captureUtmFromUrl, getUtmParams } from './utils/utm';
import { getJourneyAttribution } from './utils/attribution';
import { attachPhoneClickTracking, isPublicAnalyticsPath, trackEvent } from './utils/analytics';
import { staticLcpElement, staticLcpHiddenForRoute } from './utils/staticLcp';
import { navigateDocument } from './utils/documentNavigation';
import { getInventoryCategoryRoute, isInventoryCategoryPath } from './utils/inventoryCategoryRoutes';
import {
  applyDocumentHead,
  getCityLanding,
  isHomeListingPath,
  isPlanPath,
  isUniqueListingPath,
} from './utils/listingRoutes';
import {
  HOME_DESCRIPTION,
  HOME_TITLE,
  INVENTORY_DESCRIPTION,
  INVENTORY_TITLE,
  LOCATIONS_TITLE,
  PUBLIC_DESCRIPTIONS,
  categoryTitle,
} from './seoCopy';
import { safeUserMessage, extractErrorMessage, describeFetchError } from './utils/apiError';
import { adminCsrfHeaders } from './adminFetch';
import StaffSignInPanel from './components/StaffSignInPanel';
import { clearStaffDrafts, keepOnlyCurrentStaffDrafts } from './components/ad-studio/staffDraftStorage';
import {
  BUSINESS_NAME, BUSINESS_PHONE, BUSINESS_PHONE_RAW, BUSINESS_FULL_ADDRESS,
  BUSINESS_HOURS, BUSINESS_LICENSE, BUSINESS_CITY, BUSINESS_STATE
} from './constants';

const API_URL = '/run'; // Relative path for single-container deployment
const ADMIN_SIGN_IN_CHECK_TIMEOUT_MS = 8000;

function chatMessageIncludesContact(text) {
  const value = String(text || '');
  if (/[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}/.test(value)) return true;
  const phone = value.match(/(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}/)?.[0];
  if (!phone) return false;
  return /[\s.()+-]/.test(phone)
    || /\b(call|text|phone|cell|reach|contact|number)\b/i.test(value);
}

// Lazy-load heavy page components for code-splitting.
// SafeMarkdown pulls in the markdown parser, which the inventory route does not paint.
const SafeMarkdown = lazy(() => import('./components/SafeMarkdown'));
const Analytics = lazy(() => import('./pages/Analytics'));
const DocumentCenter = lazy(() => import('./pages/DocumentCenter'));
const AdStudio = lazy(() => import('./pages/AdStudio'));
const Contact = lazy(() => import('./pages/Contact'));
const Appointments = lazy(() => import('./pages/Appointments'));
const CRM = lazy(() => import('./pages/CRM'));
const ChatHistory = lazy(() => import('./pages/ChatHistory'));
const OpsCopilot = lazy(() => import('./pages/OpsCopilot'));
const SystemHub = lazy(() => import('./pages/SystemHub'));
const GettingStarted = lazy(() => import('./pages/GettingStarted'));
const SecureHub = lazy(() => import('./pages/SecureHub'));
const About = lazy(() => import('./pages/About'));
const Locations = lazy(() => import('./pages/Locations'));
const Financing = lazy(() => import('./pages/Financing'));
const FAQ = lazy(() => import('./pages/FAQ'));
const Warranty = lazy(() => import('./pages/Warranty'));
const Delivery = lazy(() => import('./pages/Delivery'));
const PhotoManager = lazy(() => import('./pages/PhotoManager'));
const InventoryManager = lazy(() => import('./pages/InventoryManager'));
const HealthDashboard = lazy(() => import('./pages/HealthDashboard'));
const StaffAccess = lazy(() => import('./pages/StaffAccess'));
const StaffHome = lazy(() => import('./pages/StaffHome'));
// Max characters the admin PIN box accepts. The configured backend PIN may be a
// long alphanumeric secret, so the input must NOT strip non-digits or cap short —
// doing so (an old 8-digit-only cap) locked everyone out of the admin UI. Cap only
// at a generous upper bound so an over-long secret is still flagged
// (see scripts/generate_admin_pin_hash.py).
const ADMIN_PIN_MAXLEN = 64;
const ADMIN_PAGE_KEYS = new Set(['staff-home', 'analytics', 'crm', 'chat-history', 'documents', 'adstudio', 'system', 'getting-started', 'photos', 'manage-inventory', 'copilot', 'health', 'team']);
const NOINDEX_PAGE_KEYS = new Set([...ADMIN_PAGE_KEYS, 'staff-sign-in', 'hub', 'not-found']);

// Page loading fallback with skeleton
const PageLoader = () => (
  <div className="flex flex-col items-center justify-center min-h-[50vh] gap-3">
    <Loader2 className="h-8 w-8 animate-spin text-[var(--cp-accent)]" />
    <span className="text-sm text-[var(--cp-muted)] font-medium">Loading...</span>
  </div>
);

// ─── Shared Navigation Component ───
function NavItemButton({ item, activePage, onClick, className }) {
  const Icon = item.icon;
  const isActive = activePage === item.key;
  return (
    <button
      type="button"
      onClick={() => onClick(item.key)}
      className={className(isActive)}
      aria-current={isActive ? 'page' : undefined}
    >
      <Icon size={14} className="shrink-0" />
      <span className="whitespace-nowrap">{item.label}</span>
    </button>
  );
}

function NavBar({
  activePage,
  navigateTo,
  adminAuthed,
  onAdminAccess,
  onSignOut,
  onPasskeyRegister,
  passkeyLoading,
  showPasskeySave = false,
  isMobileMenuOpen,
  setIsMobileMenuOpen,
  showSearchFilters,
  onApplyFilters,
  onClearFilters,
  darkMode,
  onToggleDarkMode,
}) {
  const [moreOpen, setMoreOpen] = useState(false);
  const publicItems = [
    { key: 'inventory', label: 'Homes for Sale', icon: Store },
    { key: 'chat', label: 'Chat', icon: MessageSquare },
    { key: 'contact', label: 'Contact', icon: Phone },
    { key: 'appointments', label: 'Book Visit', icon: CalendarDays },
  ];
  const staffPrimary = adminAuthed ? [
    { key: 'manage-inventory', label: 'Manage Homes', icon: ClipboardList },
    { key: 'photos', label: 'Photos', icon: Camera },
    { key: 'documents', label: 'Documents', icon: FileText },
    { key: 'crm', label: 'Leads', icon: Users },
  ] : [];
  const staffMore = adminAuthed ? [
    { key: 'staff-home', label: 'Staff Home', icon: Home },
    { key: 'copilot', label: 'Ops Copilot', icon: Sparkles },
    { key: 'team', label: 'Team', icon: ShieldCheck },
    { key: 'system', label: 'System Hub', icon: Activity },
    { key: 'health', label: 'Health', icon: Activity },
    { key: 'getting-started', label: 'Guide', icon: BookOpen },
    { key: 'chat-history', label: 'Chat History', icon: MessageCircle },
    { key: 'adstudio', label: 'Ad Studio', icon: Video },
    { key: 'google-ads', label: 'Google Ads', icon: Search },
    { key: 'analytics', label: 'Numbers', icon: Activity },
  ] : [];
  const desktopPrimary = adminAuthed
    ? [publicItems[0], ...staffPrimary]
    : publicItems;
  const desktopMore = adminAuthed
    ? [...publicItems.slice(1), ...staffMore]
    : [];
  const allItems = [...publicItems, ...staffPrimary, ...staffMore];
  const go = (key) => {
    setMoreOpen(false);
    navigateTo(key);
  };
  const desktopBtn = (isActive) =>
    `flex items-center gap-1.5 px-3 min-h-11 rounded-md transition-colors text-sm font-semibold shrink-0 ${
      isActive
        ? 'bg-[var(--cp-accent-dim)] text-[var(--cp-accent)]'
        : 'text-[var(--cp-muted)] hover:text-[var(--cp-text)] hover:bg-[var(--cp-surface)]'
    }`;

  return (
    <>
      {/* Site-wide store-closure / holiday banner (auto-hides after end date) */}
      <ClosureBanner />

      {/* Legacy THO contact strip */}
      <div className="bg-[var(--cp-accent)] text-[var(--cp-bg)] border-b border-[var(--cp-accent-hot)] z-40 sticky top-0">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 min-h-9 flex items-center justify-between gap-3 py-1.5 text-[11px] sm:text-xs font-semibold">
          <a href={`tel:${BUSINESS_PHONE_RAW}`} className="inline-flex items-center gap-1.5 hover:underline">
            <Phone size={13} />
            {BUSINESS_PHONE}
          </a>
          <div className="hidden sm:flex items-center gap-2">
            <MapPin size={13} />
            <span>{BUSINESS_FULL_ADDRESS}</span>
          </div>
          <span className="hidden md:inline">{BUSINESS_HOURS}</span>
        </div>
      </div>

      {/* Main NavBar — never grows the page sideways from 1024–1920. Extra
          staff tools fold into More; Sign out stays in the right cluster. */}
      <header className="bg-[var(--cp-panel)] border-b border-[var(--cp-border)] z-30 sticky top-9 shadow-sm overflow-visible">
        <div className="max-w-7xl mx-auto px-3 sm:px-4 lg:px-6 h-14 flex items-center justify-between gap-2 min-w-0 overflow-visible">
          {/* Logo — clip only this text, not the More menu below the bar. */}
          <div
            className="flex items-center gap-2 cursor-pointer group min-w-0 shrink overflow-hidden"
            onClick={() => navigateTo(adminAuthed ? 'staff-home' : 'inventory')}
            role="button"
            aria-label={`${BUSINESS_NAME} — home`}
          >
            <Home className="h-6 w-6 shrink-0 text-[var(--cp-accent)] group-hover:drop-shadow-[0_2px_8px_rgba(80,29,29,0.35)] transition" />
            <span className="text-sm sm:text-base font-bold tracking-tight text-[var(--cp-text)] whitespace-nowrap truncate">
              {BUSINESS_NAME}
            </span>
          </div>

          <div className="flex items-center gap-1 sm:gap-2 shrink-0">
            {/* Desktop nav (1024px+). Staff extras live in More. */}
            <nav className="hidden lg:flex items-center gap-0.5 text-sm font-medium" aria-label="Main navigation">
              {desktopPrimary.map((item) => (
                <NavItemButton key={item.key} item={item} activePage={activePage} onClick={go} className={desktopBtn} />
              ))}
              {desktopMore.length > 0 && (
                <div className="relative">
                  <button
                    type="button"
                    onClick={() => setMoreOpen((open) => !open)}
                    className={desktopBtn(desktopMore.some((item) => item.key === activePage))}
                    aria-expanded={moreOpen}
                    aria-haspopup="menu"
                  >
                    <MoreHorizontal size={14} className="shrink-0" />
                    More
                  </button>
                  {moreOpen && (
                    <div
                      role="menu"
                      data-testid="staff-more-menu"
                      className="absolute right-0 top-full mt-1 z-50 min-w-[12rem] rounded-lg border border-[var(--cp-border)] bg-[var(--cp-panel)] py-1 shadow-lg"
                    >
                      {desktopMore.map((item) => {
                        const Icon = item.icon;
                        return (
                          <button
                            key={item.key}
                            type="button"
                            role="menuitem"
                            onClick={() => go(item.key)}
                            className={`flex w-full items-center gap-2 px-3 py-3 min-h-12 text-left text-base ${
                              activePage === item.key
                                ? 'bg-[var(--cp-accent-dim)] text-[var(--cp-accent)]'
                                : 'text-[var(--cp-text)] hover:bg-[var(--cp-surface)]'
                            }`}
                          >
                            <Icon size={14} className="shrink-0" />
                            {item.label}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </nav>

            {/* Search filters — only on chat page */}
            {showSearchFilters && (
              <SearchFilters onApplyFilters={onApplyFilters} onClear={onClearFilters} />
            )}

            {/* Dark mode toggle — desktop */}
            {onToggleDarkMode && (
              <button
                onClick={onToggleDarkMode}
                className="hidden lg:flex items-center gap-1 px-2 py-1.5 text-xs text-[var(--cp-muted)] hover:text-[var(--cp-text)] hover:bg-[var(--cp-surface)] rounded-md transition-colors"
                aria-label="Toggle dark mode"
                title="Toggle dark mode (Ctrl+D)"
              >
                {darkMode ? <Sun size={14} /> : <Moon size={14} />}
              </button>
            )}

            {/* Admin button — desktop */}
            <button
              onClick={onAdminAccess}
              className="hidden lg:flex items-center gap-1.5 px-3 min-h-11 text-sm font-semibold text-[var(--cp-muted)] hover:text-[var(--cp-accent)] hover:bg-[var(--cp-surface)] rounded-md transition-colors"
              aria-label="Admin access"
              title="Staff sign-in"
            >
              {adminAuthed ? <ShieldCheck size={16} className="text-[var(--cp-accent)]" /> : <Lock size={16} />}
              <span className="hidden xl:inline">{adminAuthed ? 'Signed in' : 'Staff'}</span>
            </button>

            {adminAuthed && showPasskeySave && (
              <button
                onClick={onPasskeyRegister}
                disabled={passkeyLoading}
                className="hidden lg:flex items-center gap-1.5 px-3 min-h-11 text-sm font-semibold text-[var(--cp-muted)] hover:text-[var(--cp-accent)] hover:bg-[var(--cp-surface)] rounded-md transition-colors disabled:opacity-50"
                aria-label="Save sign-in on this phone"
                title="Save sign-in on this phone"
              >
                <KeyRound size={14} aria-hidden="true" />
              </button>
            )}

            {adminAuthed && onSignOut && (
              <button
                type="button"
                onClick={onSignOut}
                className="hidden lg:inline-flex items-center gap-1.5 px-3 min-h-11 text-sm font-semibold text-[var(--cp-text)] hover:bg-[var(--cp-surface)] rounded-md shrink-0"
                data-testid="desktop-sign-out"
              >
                <LogOut size={14} />
                Sign out
              </button>
            )}

            {/* Phone / tablet menu (below 1024px) */}
            <button
              className="lg:hidden p-2 hover:bg-[var(--cp-surface)] rounded-lg transition text-[var(--cp-text)]"
              onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
              aria-label={isMobileMenuOpen ? 'Close menu' : 'Open menu'}
              aria-expanded={isMobileMenuOpen}
            >
              {isMobileMenuOpen ? <X size={24} /> : <Menu size={24} />}
            </button>
          </div>
        </div>

        {/* Phone / tablet Menu */}
        {isMobileMenuOpen && (
          <nav
            className="lg:hidden bg-[var(--cp-panel)] border-t border-[var(--cp-border)] py-2 px-4"
            aria-label="Mobile navigation"
            style={{ animation: 'tho-slide-up 0.15s ease' }}
          >
            {allItems.map(item => {
              const Icon = item.icon;
              const isActive = activePage === item.key;
              return (
                <button
                  key={item.key}
                  onClick={() => navigateTo(item.key)}
                  className={`flex items-center w-full min-h-12 py-3 px-2 rounded-lg transition-colors text-base ${
                    isActive
                      ? 'bg-[var(--cp-accent-dim)] text-[var(--cp-accent)]'
                      : 'text-[var(--cp-muted)] hover:text-[var(--cp-text)] hover:bg-[var(--cp-surface)]'
                  }`}
                  aria-current={isActive ? 'page' : undefined}
                >
                  <Icon size={18} className="mr-3" />
                  {item.label}
                </button>
              );
            })}
            <button
              onClick={onAdminAccess}
              className="flex items-center w-full py-3 px-2 text-[var(--cp-muted)] hover:text-[var(--cp-accent)] hover:bg-[var(--cp-surface)] rounded-lg transition-colors mt-1 border-t border-[var(--cp-border)] pt-3 text-sm"
            >
              {adminAuthed ? <ShieldCheck size={18} className="mr-3 text-[var(--cp-accent)]" /> : <Lock size={18} className="mr-3" />}
              {adminAuthed ? 'Staff Home' : 'Staff sign-in'}
            </button>
            {adminAuthed && showPasskeySave && (
              <button
                onClick={onPasskeyRegister}
                disabled={passkeyLoading}
                className="flex items-center w-full py-3 px-2 text-[var(--cp-muted)] hover:text-[var(--cp-accent)] hover:bg-[var(--cp-surface)] rounded-lg transition-colors text-sm disabled:opacity-50"
              >
                <KeyRound size={18} className="mr-3" aria-hidden="true" />
                Save sign-in on this phone
              </button>
            )}
            {adminAuthed && onSignOut && (
              <button
                type="button"
                onClick={onSignOut}
                className="flex items-center w-full py-3 px-2 text-base text-[var(--cp-text)] hover:bg-[var(--cp-surface)] rounded-lg"
              >
                Sign out
              </button>
            )}
          </nav>
        )}
      </header>
    </>
  );
}

// ─── Footer Component ───
function Footer({ adminAuthed, onAdminAccess, onNavigate }) {
  const linkClass = "hover:text-[var(--cp-accent)] transition-colors";
  return (
    <footer className="bg-[var(--cp-bg-2)] border-t border-[var(--cp-border)] py-6 text-center text-xs text-[var(--cp-muted)]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex flex-col md:flex-row items-center justify-center gap-4 md:gap-6 flex-wrap mb-4">
          <span className="flex items-center"><MapPin size={12} className="mr-1 text-[var(--cp-accent)]" aria-hidden="true" /> {BUSINESS_FULL_ADDRESS}</span>
          <a href={`tel:${BUSINESS_PHONE_RAW}`} className="flex items-center hover:text-[var(--cp-accent)] transition-colors">
            <Phone size={12} className="mr-1" aria-hidden="true" /> {BUSINESS_PHONE}
          </a>
          <span>{BUSINESS_HOURS}</span>
          <span className="flex items-center"><ShieldCheck size={12} className="mr-1 text-green-600" /> License #{BUSINESS_LICENSE}</span>
        </div>
        <nav aria-label="Footer" className="flex flex-wrap items-center justify-center gap-4 md:gap-6 mb-4">
          <a href="/" className={linkClass}>Home</a>
          <a href="/inventory" className={linkClass}>Inventory</a>
          <a href="/locations" className={linkClass}>Locations</a>
          <button onClick={() => onNavigate('about')} className={linkClass}>About</button>
          <button onClick={() => onNavigate('financing')} className={linkClass}>Financing</button>
          <button onClick={() => onNavigate('faq')} className={linkClass}>FAQ</button>
          <button onClick={() => onNavigate('warranty')} className={linkClass}>Warranty</button>
          <button onClick={() => onNavigate('delivery')} className={linkClass}>Delivery & Setup</button>
          <a href="/contact" className={linkClass} onClick={(e) => { e.preventDefault(); onNavigate('contact'); }}>Contact</a>
          <a href="/privacy" className={linkClass}>Privacy Policy</a>
          <a href="/terms" className={linkClass}>Terms of Use</a>
        </nav>
        <div className="flex items-center justify-center">
          <button onClick={onAdminAccess} className="flex items-center hover:text-[var(--cp-accent)] transition-colors">
            {adminAuthed ? <ShieldCheck size={12} className="mr-1 text-[var(--cp-accent)]" /> : <Lock size={12} className="mr-1" />}
            Admin
          </button>
        </div>
      </div>
    </footer>
  );
}

// ─── Shared API call helper with retry logic ───
async function sendToAgent(sessionId, text, maxRetries = 2) {
  let lastError;
  let activeSessionId = sessionId;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const response = await fetch(API_URL, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json'
        },
        body: JSON.stringify({
          appName: 'root_agent',
          userId: `web_user_${activeSessionId}`,
          sessionId: activeSessionId,
          newMessage: {
            role: 'user',
            parts: [{ text }]
          },
          // First-touch UTM/referrer so a chat-sourced lead is attributable to
          // the paid campaign that drove the visit (mirrors the contact form).
          ...getUtmParams(),
          ...getJourneyAttribution()
        })
      });

      if (!response.ok) {
        const httpError = new Error(`HTTP ${response.status}: ${response.statusText}`);
        // Carry the status so describeFetchError can map it; the message keeps
        // the legacy "HTTP 4xx" shape the retry check below matches on.
        httpError.status = response.status;
        throw httpError;
      }

      const data = await response.json();
      activeSessionId = adoptRunSessionId(activeSessionId, data);

      let reply;
      if (data.error) reply = `System Error: ${safeUserMessage(extractErrorMessage(data))}`;
      else if (data.text) reply = data.text;
      else if (data.content) reply = typeof data.content === 'string' ? data.content : JSON.stringify(data.content);
      else if (data.candidates?.[0]?.content?.parts) {
        reply = data.candidates[0].content.parts.map((part) => part.text).join(' ');
      } else reply = "I apologize, I didn't catch that. Could you rephrase?";
      return { text: reply, sessionId: activeSessionId };

    } catch (error) {
      lastError = error;
      console.warn(`API call attempt ${attempt + 1} failed:`, error.message);

      // Don't retry on client errors (4xx)
      if (error.message.includes('HTTP 4')) {
        break;
      }

      // Wait before retry (exponential backoff)
      if (attempt < maxRetries) {
        await new Promise(resolve => setTimeout(resolve, Math.pow(2, attempt) * 1000));
      }
    }
  }

  throw lastError;
}


function pageFromPath(path) {
  const p = (path || '').toLowerCase();
  if (p.startsWith('/documents') || p.startsWith('/document-center') || p.startsWith('/app/documents')) return 'documents';
  if (p.startsWith('/ad-studio') || p.startsWith('/studio') || p.startsWith('/app/studio')) return 'adstudio';
  if (p.startsWith('/crm')) return 'crm';
  if (p.startsWith('/analytics')) return 'analytics';
  if (p.startsWith('/getting-started') || p.startsWith('/guide')) return 'getting-started';
  if (p === '/privacy' || p === '/privacy/' || p === '/legal' || p === '/legal/') return 'privacy';
  if (p === '/terms' || p === '/terms/') return 'terms';
  if (p.startsWith('/contact')) return 'contact';
  if (p.startsWith('/appointments')) return 'appointments';
  if (p.startsWith('/about')) return 'about';
  if (p.startsWith('/financing')) return 'financing';
  if (p.startsWith('/faq')) return 'faq';
  if (p.startsWith('/warranty')) return 'warranty';
  if (p.startsWith('/delivery')) return 'delivery';
  if (p.startsWith('/chat-history')) return 'chat-history';
  if (p.startsWith('/manage-inventory')) return 'manage-inventory';
  if (p === '/photos' || p.startsWith('/photos/')) return 'photos';
  if (p.startsWith('/copilot') || p.startsWith('/ops-copilot')) return 'copilot';
  if (p.startsWith('/health')) return 'health';
  if (p.startsWith('/chat')) return 'chat';
  if (p === '/locations' || p === '/locations/') return 'locations';
  if (p.startsWith('/inventory')) return 'inventory';
  if (isInventoryCategoryPath(p)) return 'inventory';
  // City landing pages (/manufactured-homes-in-{city}-tx) render the inventory.
  if (p.startsWith('/manufactured-homes-in-')) return 'inventory';
  if (p.startsWith('/homes/')) return 'inventory';
  // Legacy texashomeoutlet.com deep links resolve inside the inventory page.
  if (p.startsWith('/inventory-detail/') || p.startsWith('/plan/') || p.startsWith('/quote/')) return 'inventory';
  if (p.startsWith('/hub/')) return 'hub';
  if (p === '/staff-home' || p.startsWith('/staff-home/')) return 'staff-home';
  if (p === '/staff' || p.startsWith('/staff/')) return 'staff-sign-in';
  if (p === '/team' || p.startsWith('/team/')) return 'team';
  if (p.startsWith('/system')) return 'system';
  if (p === '/' || p === '') return 'inventory';
  // Unknown path: the server already responded 404 — render a friendly view.
  return 'not-found';
}


function App() {
  const { addToast } = useToast();
  const isOnline = useNetworkStatus();

  const [messages, setMessages] = useState([
    {
      role: 'model',
      text: `Howdy! Welcome to ${BUSINESS_NAME}. I'm Tex, your virtual housing consultant. How can I help you today?`,
      showQuickActions: true
    }
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId, setSessionId] = useState(() => {
    try {
      return localStorage.getItem(CHAT_SESSION_STORAGE_KEY) || '';
    } catch {
      return '';
    }
  });
  const callbackStorageKey = sessionId ? `tho_chat_callback_${sessionId}` : '';
  const [chatCallbackCaptured, setChatCallbackCaptured] = useState(() => (
    callbackStorageKey ? localStorage.getItem(callbackStorageKey) === 'captured' : false
  ));
  const [chatCallbackDismissed, setChatCallbackDismissed] = useState(() => (
    callbackStorageKey ? sessionStorage.getItem(callbackStorageKey) === 'dismissed' : false
  ));
  const sessionRequestRef = useRef(null);
  const resolvedSessionRef = useRef('');

  useEffect(() => {
    if (!callbackStorageKey) {
      setChatCallbackCaptured(false);
      setChatCallbackDismissed(false);
      return;
    }
    setChatCallbackCaptured(localStorage.getItem(callbackStorageKey) === 'captured');
    setChatCallbackDismissed(sessionStorage.getItem(callbackStorageKey) === 'dismissed');
  }, [callbackStorageKey]);

  const loadSession = useCallback(() => {
    if (!sessionRequestRef.current) {
      sessionRequestRef.current = resolveChatSession()
        .then((result) => {
          if (result?.sessionId) resolvedSessionRef.current = result.sessionId;
          return result;
        })
        .finally(() => {
          sessionRequestRef.current = null;
        });
    }
    return sessionRequestRef.current;
  }, []);

  const ensureSessionId = useCallback(async () => {
    // Wait for the in-flight history read. It sets the owner cookie before
    // the first send, including chats that started before that cookie existed.
    let resolved = null;
    if (sessionRequestRef.current) {
      resolved = await sessionRequestRef.current;
    } else if (!resolvedSessionRef.current) {
      resolved = await loadSession();
    }
    const nextId = resolved?.sessionId || resolvedSessionRef.current || sessionId || '';
    if (nextId && nextId !== sessionId) setSessionId(nextId);
    return nextId;
  }, [loadSession, sessionId]);

  const acceptRunResult = useCallback((result) => {
    const nextId = result?.sessionId || '';
    if (nextId) {
      resolvedSessionRef.current = nextId;
      setSessionId((current) => (current === nextId ? current : nextId));
    }
    return typeof result?.text === 'string' ? result.text : '';
  }, []);

  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [comparisonList, setComparisonList] = useState([]);
  const [darkMode, setDarkMode] = useDarkMode();
  const [pathname, setPathname] = useState(() => window.location.pathname);
  const activePage = pageFromPath(pathname);
  const activeDealId = (() => {
    if (pathname.startsWith('/hub/')) {
      return pathname.split('/hub/')[1].split('/')[0];
    }
    return null;
  })();

  const navigatePath = useCallback((targetPath, { replace = false } = {}) => {
    const nextPath = String(targetPath || '/');
    if (isInventoryCategoryPath(pathname) && nextPath !== pathname) {
      navigateDocument(nextPath);
      return;
    }
    if (window.location.pathname !== nextPath) {
      window.history[replace ? 'replaceState' : 'pushState']({}, '', nextPath);
    }
    setPathname(nextPath);
  }, [pathname]);

  // Capture UTM params on first mount
  useEffect(() => {
    captureUtmFromUrl();
  }, []);

  // Load chat history for this browser, or start a fresh chat when the
  // owner cookie is missing (including sessions started before that cookie).
  useEffect(() => {
    let cancelled = false;
    loadSession()
      .then((result) => {
        if (cancelled) return;
        if (result.sessionId) setSessionId(result.sessionId);
        if (!result.messages.length) return;
        setMessages((prev) => {
          if (prev.length > 1) return prev;
          return [
            prev[0],
            ...result.messages.map((msg) => ({
              role: msg.role,
              text: msg.text,
              showQuickActions: false,
            })),
          ];
        });
      })
      .catch((error) => console.warn('Failed to load chat history:', error));
    return () => {
      cancelled = true;
    };
  }, [loadSession]);


  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e) => {
      // Ctrl+D / Cmd+D — toggle dark mode
      if ((e.ctrlKey || e.metaKey) && e.key === 'd') {
        e.preventDefault();
        setDarkMode(prev => !prev);
      }
      // Ctrl+K / Cmd+K — focus search/chat input
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        document.querySelector('input[type="text"], textarea')?.focus();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [setDarkMode]);

  const [appointmentHandoff, setAppointmentHandoff] = useState(null);
  const isStandaloneMode = pathname.startsWith('/app/') || window.location.search.includes('standalone=1');

  // A delegated listener covers every public click-to-call CTA, including
  // links rendered by lazy pages and future inventory components.
  useEffect(() => attachPhoneClickTracking(document), []);

  // GA4 does not automatically see client-side route changes. Keep public SPA
  // page views measurable while excluding every admin/noindex surface.
  useEffect(() => {
    if (isPublicAnalyticsPath(pathname)) {
      trackEvent('page_viewed', { page: activePage, page_path: pathname });
    }
  }, [activePage, pathname]);

  // The hero image is painted by the server and lives outside #root. Hide it
  // on routes that do not use the inventory hero so it cannot cover them.
  useEffect(() => {
    const image = staticLcpElement();
    if (!image) return undefined;
    const path = String(pathname || '/').replace(/\/+$/, '') || '/';
    const showHero = path === '/'
      || path === '/inventory'
      || path.startsWith('/homes/')
      || path.startsWith('/inventory-detail/')
      || path.startsWith('/plan/');
    image.hidden = staticLcpHiddenForRoute(image, showHero);
    return undefined;
  }, [pathname]);

  // Keep activePage in sync with browser back/forward navigation
  useEffect(() => {
    const handlePopState = () => {
      setPathname(window.location.pathname);
      setAppointmentHandoff(null);
      setIsMobileMenuOpen(false);
      window.scrollTo({ top: 0 });
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);


  // Admin auth. The token is validated by the backend via an httpOnly cookie.
  // adminSignInCheck stays "checking" until that first response, so a refresh
  // of /ad-studio does not flash the locked page.
  const [adminAuthed, setAdminAuthed] = useState(false);
  const [adminSignInCheck, setAdminSignInCheck] = useState('checking');
  const [showPinModal, setShowPinModal] = useState(false);
  const [signInNotice, setSignInNotice] = useState('');

  // Passkey state
  const [passkeyLoading, setPasskeyLoading] = useState(false);
  const [passkeyError, setPasskeyError] = useState('');
  const [passkeyStatus, setPasskeyStatus] = useState(null);
  const [passkeyAvailable] = useState(
    typeof window !== 'undefined' && !!window.PublicKeyCredential
  );
  const [showPasskeyEmailModal, setShowPasskeyEmailModal] = useState(false);
  const [passkeyEmail, setPasskeyEmail] = useState(() => {
    if (typeof window === 'undefined') return '';
    return window.localStorage.getItem('tho_passkey_email') || '';
  });
  const [passkeyEmailError, setPasskeyEmailError] = useState('');
  const [passkeySignInEnabled, setPasskeySignInEnabled] = useState(false);

  const refreshPasskeyStatus = useCallback(async () => {
    try {
      const response = await fetch('/api/admin/passkey/status', {
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
      });
      if (!response.ok) throw new Error('Passkey status unavailable');
      const data = await response.json();
      setPasskeyStatus(data);
      return data;
    } catch {
      setPasskeyStatus({ enabled: false, store_ready: false, has_keys: false });
      return null;
    }
  }, []);

  useEffect(() => {
    refreshPasskeyStatus();
  }, [refreshPasskeyStatus, adminAuthed]); // Refresh when auth state changes

  useEffect(() => {
    let cancelled = false;
    fetch('/api/admin/sign-in/options', {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!cancelled) setPasskeySignInEnabled(data?.passkey_sign_in === true);
      })
      .catch(() => {
        if (!cancelled) setPasskeySignInEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, [adminAuthed]);

  const handlePasskeyLogin = async () => {
    if (!window.PublicKeyCredential) { setPasskeyError('Passkeys not supported in this browser'); return; }
    if (passkeyStatus && !passkeyStatus.has_keys) {
      setPasskeyError('No passkeys are enrolled yet. Sign in with your PIN or an email code, then register this device.');
      return;
    }
    setPasskeyLoading(true); setPasskeyError('');
    try {
      const beginRes = await fetch('/api/admin/passkey/login/begin', { method: 'POST', credentials: 'same-origin' });
      if (!beginRes.ok) {
        const data = await beginRes.json().catch(() => ({}));
        throw new Error(safeUserMessage(extractErrorMessage(data), 'Server error starting passkey login'));
      }
      const options = await beginRes.json();
      // Convert base64url challenge / ids back to ArrayBuffer
      options.challenge = base64urlToBuffer(options.challenge);
      if (options.allowCredentials) {
        options.allowCredentials = options.allowCredentials.map(c => ({
          ...c,
          id: base64urlToBuffer(c.id),
        }));
      }
      const credential = await navigator.credentials.get({ publicKey: options });
      if (!credential) throw new Error('Passkey login cancelled');
      const payload = {
        id: credential.id,
        rawId: bufferToBase64url(credential.rawId),
        response: {
          authenticatorData: bufferToBase64url(credential.response.authenticatorData),
          clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
          signature: bufferToBase64url(credential.response.signature),
          userHandle: credential.response.userHandle ? bufferToBase64url(credential.response.userHandle) : null,
        },
        type: credential.type,
      };
      const completeRes = await fetch('/api/admin/passkey/login/complete', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await completeRes.json();
      if (data.success) {
        setAdminAuthed(true); setAdminSignInCheck('signed-in'); setShowPinModal(false); setPasskeyError('');
        navigateTo('staff-home');
      } else {
        setPasskeyError(safeUserMessage(extractErrorMessage(data), 'Passkey login failed'));
      }
    } catch (err) {
      console.warn('Passkey login error:', err);
      // Browsers use NotAllowedError for several outcomes (including timeout
      // and cancellation), so do not guess why the prompt did not finish.
      const message = err?.name === 'NotAllowedError' || err?.name === 'AbortError'
        ? 'Passkey sign-in did not finish. Try again, or use the email sign-in on this page.'
        : safeUserMessage(err?.message, describeFetchError(err, 'complete passkey sign-in'));
      setPasskeyError(message);
    } finally {
      setPasskeyLoading(false);
    }
  };

  const openPasskeyRegisterModal = useCallback(() => {
    if (!window.PublicKeyCredential) {
      setPasskeyError('Passkeys not supported in this browser');
      return;
    }
    if (!adminAuthed) {
      setPasskeyError('Sign in with your PIN or an email code before registering a staff passkey.');
      setShowPinModal(true);
      return;
    }
    if (!passkeySignInEnabled) return;
    setPasskeyError('');
    setPasskeyEmailError('');
    setShowPasskeyEmailModal(true);
    setIsMobileMenuOpen(false);
  }, [adminAuthed, passkeySignInEnabled]);

  const handlePasskeyRegister = async (emailValue) => {
    if (!window.PublicKeyCredential) { setPasskeyError('Passkeys not supported in this browser'); return; }
    const normalizedEmail = String(emailValue || '').trim().toLowerCase();
    if (!normalizedEmail || !normalizedEmail.includes('@')) {
      setPasskeyEmailError('Enter the approved owner email or a @texashomeoutlet.com staff email.');
      return;
    }
    setPasskeyLoading(true); setPasskeyError('');
    setPasskeyEmailError('');
    try {
      const beginRes = await fetch('/api/admin/passkey/register/begin', {
        method: 'POST',
        credentials: 'same-origin',
        headers: adminCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ email: normalizedEmail }),
      });
      if (!beginRes.ok) {
        const data = await beginRes.json().catch(() => ({}));
        throw new Error(safeUserMessage(extractErrorMessage(data), 'Sign in before registering a staff passkey'));
      }
      const options = await beginRes.json();
      options.user.id = base64urlToBuffer(options.user.id);
      options.challenge = base64urlToBuffer(options.challenge);
      if (options.excludeCredentials) {
        options.excludeCredentials = options.excludeCredentials.map(c => ({
          ...c,
          id: base64urlToBuffer(c.id),
        }));
      }
      const credential = await navigator.credentials.create({ publicKey: options });
      if (!credential) throw new Error('Passkey registration cancelled');
      const payload = {
        id: credential.id,
        rawId: bufferToBase64url(credential.rawId),
        response: {
          clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
          attestationObject: bufferToBase64url(credential.response.attestationObject),
        },
        type: credential.type,
        clientExtensionResults: credential.getClientExtensionResults(),
      };
      const completeRes = await fetch('/api/admin/passkey/register/complete', {
        method: 'POST', credentials: 'same-origin',
        headers: adminCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
      });
      const data = await completeRes.json();
      if (data.success) {
        setAdminAuthed(true); setAdminSignInCheck('signed-in'); setShowPinModal(false); setPasskeyError('');
        setShowPasskeyEmailModal(false);
        setPasskeyEmail(data.email || normalizedEmail);
        window.localStorage.setItem('tho_passkey_email', data.email || normalizedEmail);
        await refreshPasskeyStatus();
        addToast('Passkey registered for this staff email.', 'success');
        navigateTo('system');
      } else {
        setPasskeyEmailError(safeUserMessage(extractErrorMessage(data), 'Passkey registration failed'));
      }
    } catch (err) {
      console.warn('Passkey register error:', err);
      const message = err.name === 'InvalidStateError'
        ? 'That passkey provider already has a THO key. Try signing in with it, or choose another provider or device. Confirm another working sign-in method before revoking a key in System Hub.'
        : safeUserMessage(err?.message, describeFetchError(err, 'register your passkey'));
      setPasskeyEmailError(message);
    } finally {
      setPasskeyLoading(false);
    }
  };

  // base64url helpers
  function base64urlToBuffer(str) {
    const padding = '='.repeat((4 - (str.length % 4)) % 4);
    const base64 = str.replace(/-/g, '+').replace(/_/g, '/') + padding;
    const raw = atob(base64);
    const buf = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
    return buf.buffer;
  }
  function bufferToBase64url(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  const lastLoginTime = useRef(0);

  // Verify stored cookie on mount. A hung check must not leave staff on the
  // checking screen. The timeout fails closed: locked page, and saved staff
  // drafts are cleared the same way an invalid check clears them.
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      if (!active) return;
      clearStaffDrafts();
      setAdminSignInCheck('signed-out');
      controller.abort();
    }, ADMIN_SIGN_IN_CHECK_TIMEOUT_MS);
    fetch('/api/admin/check', {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      signal: controller.signal,
    })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!active || controller.signal.aborted) return;
        clearTimeout(timer);
        if (data?.valid) {
          setAdminAuthed(true);
          setAdminSignInCheck('signed-in');
          keepOnlyCurrentStaffDrafts();
        } else {
          if (data && data.valid === false) clearStaffDrafts();
          setAdminSignInCheck('signed-out');
        }
      })
      .catch(() => {
        if (!active || controller.signal.aborted) return;
        clearTimeout(timer);
        setAdminSignInCheck('signed-out');
      });
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, []);

  // Direct links to admin-only pages should ask for PIN instead of silently
  // falling back to the public shell. /ad-studio waits for the session check
  // so a refresh does not open the sign-in panel over a still-valid session.
  useEffect(() => {
    let mounted = true;
    if (activePage === 'adstudio' && adminSignInCheck === 'checking') {
      return undefined;
    }
    if (ADMIN_PAGE_KEYS.has(activePage) && !adminAuthed) {
      // Small delay to allow mounted check or session verify to finish
      const timer = setTimeout(() => {
        if (mounted && !adminAuthed) {
          setShowPinModal(true);
          setSignInNotice('');
          setPasskeyError('');
        }
      }, 100);
      return () => { mounted = false; clearTimeout(timer); };
    }
    return undefined;
  }, [activePage, adminAuthed, adminSignInCheck]);

  useEffect(() => {
    if (adminAuthed) {
      setShowPinModal(false);
      setSignInNotice('');
    }
  }, [adminAuthed]);

  // Listen for expired admin session (fired by adminFetch on 401)
  useEffect(() => {
    const handleExpired = () => {
      // Prevent session expiry from triggering if we JUST logged in (race condition protection)
      if (Date.now() - lastLoginTime.current < 5000) {
        console.warn('[App] Ignoring session-expired event triggered immediately after login.');
        return;
      }
      clearStaffDrafts();
      setAdminAuthed(false);
      setAdminSignInCheck('signed-out');
      setShowPinModal(true);
      setSignInNotice('Session expired — please sign in again');
    };
    window.addEventListener('admin-session-expired', handleExpired);

    const handleRateLimited = () => {
      addToast('Too many clicks, try again in a moment', 'warning', 4000);
    };
    window.addEventListener('admin-rate-limited', handleRateLimited);

    const handleTriggerRegister = (e) => {
      if (e.detail?.email) {
        setPasskeyEmail(e.detail.email);
      }
      openPasskeyRegisterModal();
    };
    window.addEventListener('trigger-passkey-register', handleTriggerRegister);

    return () => {
      window.removeEventListener('admin-session-expired', handleExpired);
      window.removeEventListener('admin-rate-limited', handleRateLimited);
      window.removeEventListener('trigger-passkey-register', handleTriggerRegister);
    };
  }, [openPasskeyRegisterModal, addToast]);

  const messagesEndRef = useRef(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('admin') === 'true') {
      if (adminAuthed) {
        navigatePath('/staff-home', { replace: true });
      } else {
        setShowPinModal(true);
      }
    }
    if (sessionId) localStorage.setItem('tho_session_id', sessionId);
  }, [sessionId, adminAuthed, navigatePath]);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e) => {
      // Ctrl/Cmd + K to focus chat input
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        document.querySelector('input[aria-label="Chat message"]')?.focus();
      }

      // Escape to close mobile menu
      if (e.key === 'Escape') {
        setIsMobileMenuOpen(false);
        setShowPinModal(false);
        setShowPasskeyEmailModal(false);
      }

      // Ctrl/Cmd + / to show keyboard shortcuts
      if ((e.ctrlKey || e.metaKey) && e.key === '/') {
        e.preventDefault();
        addToast('Keyboard shortcuts: Ctrl+K = Focus chat, Esc = Close menus, Ctrl+/ = This help', 'info', 5000);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [addToast]);

  useEffect(() => {
    if (/^\/legal\/?$/i.test(pathname)) navigatePath('/privacy', { replace: true });
  }, [pathname, navigatePath]);

  // Update page title per page. Public pages mirror the server-injected SEO
  // titles (seo_routes.py PUBLIC_ROUTES) so client-side navs stay in sync;
  // operator pages keep the short "Page | Business" pattern.
  useEffect(() => {
    const fullTitles = {
      inventory: HOME_TITLE,
      chat: `Chat with Tex — ${BUSINESS_NAME} Home Finder`,
      contact: `Contact ${BUSINESS_NAME} — ${BUSINESS_CITY}, ${BUSINESS_STATE}`,
      appointments: `Book a Showroom Visit | ${BUSINESS_NAME}`,
      privacy: `Privacy Policy | ${BUSINESS_NAME}`,
      terms: `Terms of Use | ${BUSINESS_NAME}`,
      about: `About Us | ${BUSINESS_NAME}`,
      financing: `Manufactured Home Financing in ${BUSINESS_CITY} | ${BUSINESS_NAME}`,
      faq: `Frequently Asked Questions | ${BUSINESS_NAME}`,
      warranty: `Warranty & Service | ${BUSINESS_NAME}`,
      delivery: `Delivery & Setup | ${BUSINESS_NAME}`,
      locations: LOCATIONS_TITLE,
      'not-found': `Page not found | ${BUSINESS_NAME}`,
    };
    const noindex = NOINDEX_PAGE_KEYS.has(activePage);
    const robots = noindex ? 'noindex' : 'index,follow';
    const shortTitles = {
      documents: 'Documents',
      adstudio: pathname.toLowerCase().startsWith('/ad-studio') ? 'Google Ads' : 'Ad Studio',
      analytics: 'Analytics',
      crm: 'CRM Dashboard',
      system: 'THO System Hub',
      health: 'Health Dashboard',
      team: 'Team access',
      'staff-sign-in': 'Staff sign-in',
      'staff-home': 'Staff Home',
      'getting-started': 'Getting Started',
      'chat-history': 'Chat History',
      'manage-inventory': 'Manage Inventory',
      photos: 'Photos',
    };
    const cityLanding = getCityLanding(pathname);
    if (cityLanding) {
      applyDocumentHead({
        title: cityLanding.title,
        description: cityLanding.description,
        canonicalPath: cityLanding.path,
        image: '/og-card.png',
        robots: 'index,follow',
      });
      return;
    }
    const inventoryCategory = activePage === 'inventory'
      ? getInventoryCategoryRoute(pathname)
      : null;
    if (inventoryCategory) {
      applyDocumentHead({
        title: categoryTitle(inventoryCategory.classification),
        description: inventoryCategory.description,
        canonicalPath: inventoryCategory.path,
        image: '/og-card.png',
        robots: 'index,follow',
      });
      return;
    }
    if (isHomeListingPath(pathname) || isPlanPath(pathname) || isUniqueListingPath(pathname)) {
      // InventoryBrowse sets the model-specific title once the listing loads.
      // Never fall back to the generic Huffman inventory title on these URLs.
      return;
    }
    const normalizedPath = pathname.replace(/\/+$/, '') || '/';
    if (normalizedPath === '/') {
      applyDocumentHead({
        title: HOME_TITLE,
        description: HOME_DESCRIPTION,
        canonicalPath: '/',
        image: '/og-card.png',
        robots: 'index,follow',
      });
      return;
    }
    if (normalizedPath === '/inventory') {
      applyDocumentHead({
        title: INVENTORY_TITLE,
        description: INVENTORY_DESCRIPTION,
        canonicalPath: '/inventory',
        image: '/og-card.png',
        robots: 'index,follow',
      });
      return;
    }
    const nextTitle = fullTitles[activePage]
      || (shortTitles[activePage] ? `${shortTitles[activePage]} | ${BUSINESS_NAME}` : BUSINESS_NAME);
    applyDocumentHead({
      title: nextTitle,
      description: PUBLIC_DESCRIPTIONS[activePage] || `${BUSINESS_NAME} staff tools.`,
      canonicalPath: noindex ? undefined : (pathname === '/' ? '/' : pathname.replace(/\/+$/, '') || '/'),
      image: !noindex && fullTitles[activePage] ? '/og-card.png' : undefined,
      robots,
    });
  }, [activePage, pathname]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const navigateTo = (page, { preserveAppointmentHandoff = false } = {}) => {
    if (!preserveAppointmentHandoff) setAppointmentHandoff(null);
    setIsMobileMenuOpen(false);
    window.scrollTo({ top: 0 });
    // Update URL for bookmarkable routes
    const urlMap = {
      inventory: '/',
      chat: '/chat',
      contact: '/contact',
      appointments: '/appointments',
      about: '/about',
      financing: '/financing',
      faq: '/faq',
      warranty: '/warranty',
      delivery: '/delivery',
      locations: '/locations',
      privacy: '/privacy',
      terms: '/terms',
      documents: '/documents',
      adstudio: '/studio',
      'google-ads': '/ad-studio',
      crm: '/crm',
      analytics: '/analytics',
      'getting-started': '/getting-started',
      'chat-history': '/chat-history',
      'manage-inventory': '/manage-inventory',
      photos: '/photos',
      copilot: '/copilot',
      system: '/system',
      health: '/health',
      team: '/team',
      'staff-sign-in': '/staff',
      'staff-home': '/staff-home',
    };
    const targetUrl = urlMap[page] || '/';
    navigatePath(targetUrl);
  };

  useEffect(() => {
    if (adminAuthed && activePage === 'staff-sign-in') {
      navigateTo('staff-home');
    }
    // navigateTo is recreated per render; adding it here would re-run the
    // redirect on every render. The semantic triggers are the two deps below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminAuthed, activePage]);

  const startAppointmentHandoff = (handoff) => {
    setAppointmentHandoff(handoff);
    navigateTo('appointments', { preserveAppointmentHandoff: true });
  };

  // Admin PIN handlers
  const completeStaffSignIn = () => {
    lastLoginTime.current = Date.now();
    setAdminAuthed(true);
    setAdminSignInCheck('signed-in');
    setShowPinModal(false);
    setSignInNotice('');
    const destination = activePage === 'staff-sign-in'
      ? 'staff-home'
      : (ADMIN_PAGE_KEYS.has(activePage) ? activePage : 'staff-home');
    navigateTo(destination);
  };

  const handleAdminAccess = () => {
    if (adminAuthed) {
      navigateTo('staff-home');
    } else if (activePage === 'staff-sign-in') {
      document.getElementById('staff-sign-in')?.scrollIntoView({ behavior: 'smooth' });
    } else {
      setShowPinModal(true);
      setSignInNotice('');
    }
  };

  const handleSignOut = async () => {
    clearStaffDrafts();
    try {
      await fetch('/api/admin/logout', { method: 'POST', credentials: 'same-origin' });
    } catch {
      // The button still leaves the screen. The cookie clear is best-effort.
    }
    setAdminAuthed(false);
    setAdminSignInCheck('signed-out');
    setShowPinModal(false);
    setSignInNotice('');
    navigateTo('inventory');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage = { role: 'user', text: input };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);

    try {
      const activeSessionId = await ensureSessionId();
      if (!activeSessionId) throw new Error('Chat session unavailable');
      const botText = acceptRunResult(await sendToAgent(activeSessionId, userMessage.text));
      setMessages(prev => [...prev, { role: 'model', text: botText }]);
    } catch (error) {
      console.error('Error sending message:', error);

      // Show error with retry option
      setMessages(prev => [...prev, {
        role: 'model',
        text: `I'm having trouble connecting right now. This might be a temporary issue.\n\n[Click to retry](/retry)`,
        isError: true,
        originalMessage: userMessage.text
      }]);

      addToast('Message failed to send. Click "Retry" to try again.', 'error', 8000);
    } finally {
      setIsLoading(false);
    }
  };

  // Retry failed message
  const handleRetry = useCallback(async (originalMessage) => {
    setIsLoading(true);
    try {
      const activeSessionId = await ensureSessionId();
      if (!activeSessionId) throw new Error('Chat session unavailable');
      const botText = acceptRunResult(await sendToAgent(activeSessionId, originalMessage));
      setMessages(prev => [...prev, { role: 'model', text: botText }]);
      addToast('Message sent successfully!', 'success');
    } catch {
      addToast('Still having trouble connecting. Please try again later.', 'error');
    } finally {
      setIsLoading(false);
    }
  }, [acceptRunResult, ensureSessionId, addToast]);

  const handleQuickAction = async (message) => {
    if (!isOnline) {
      addToast('You appear to be offline. Please check your connection.', 'warning');
      return;
    }

    setIsLoading(true);
    try {
      const activeSessionId = await ensureSessionId();
      if (!activeSessionId) throw new Error('Chat session unavailable');
      const botText = acceptRunResult(await sendToAgent(activeSessionId, message));
      setMessages(prev => [...prev, { role: 'model', text: botText }]);
    } catch (error) {
      console.error('Error sending quick action:', error);
      setMessages(prev => [...prev, {
        role: 'model',
        text: `I'm having trouble connecting right now. Please try again or call us at ${BUSINESS_PHONE}.`,
        isError: true
      }]);
      addToast('Quick action failed. Please try again.', 'error');
    } finally {
      setIsLoading(false);
    }
  };

  const handleApplyFilters = (filters) => {
    const filterParts = [];
    if (filters.bedrooms) filterParts.push(`${filters.bedrooms}+ bedrooms`);
    if (filters.bathrooms) filterParts.push(`${filters.bathrooms}+ bathrooms`);
    if (filters.minPrice && filters.maxPrice) {
      filterParts.push(`$${filters.minPrice.toLocaleString()}-$${filters.maxPrice.toLocaleString()}`);
    } else if (filters.maxPrice) {
      filterParts.push(`under $${filters.maxPrice.toLocaleString()}`);
    } else if (filters.minPrice) {
      filterParts.push(`over $${filters.minPrice.toLocaleString()}`);
    }
    if (filters.homeType) filterParts.push(filters.homeType);

    if (filterParts.length > 0) {
      const filterMessage = `Show me homes with ${filterParts.join(', ')}`;
      setInput(filterMessage);
    }
  };

  const handleClearFilters = () => {
    setInput('');
  };

  const handleToggleCompare = (property) => {
    setComparisonList(prev => {
      const exists = prev.some(p => p.id === property.id);
      if (exists) return prev.filter(p => p.id !== property.id);
      if (prev.length >= 3) return prev;
      return [...prev, property];
    });
  };

  const handleRemoveFromCompare = (id) => {
    setComparisonList(prev => prev.filter(p => p.id !== id));
  };

  // --- PIN Modal ---
  const pinModal = showPinModal && (
    <div className="fixed inset-0 bg-black/70 z-[100] flex items-center justify-center p-4 backdrop-blur-sm" style={{ animation: 'tho-fade-in 0.15s ease' }}>
      <div className="cp-panel p-6 sm:p-8 max-w-md w-full max-h-[calc(100dvh-2rem)] overflow-y-auto" style={{ animation: 'tho-slide-up 0.2s ease' }}>
        <StaffSignInPanel
          onSuccess={completeStaffSignIn}
          onCancel={() => setShowPinModal(false)}
          passkeyAvailable={passkeyAvailable}
          passkeyStatus={passkeyStatus}
          passkeyLoading={passkeyLoading}
          passkeyError={passkeyError}
          onPasskeyLogin={handlePasskeyLogin}
          initialNotice={signInNotice}
        />
      </div>
    </div>
  );

  const passkeyEmailModal = showPasskeyEmailModal && (
    <div className="fixed inset-0 bg-black/70 z-[110] flex items-center justify-center p-4 backdrop-blur-sm" style={{ animation: 'tho-fade-in 0.15s ease' }}>
      <div className="cp-panel p-6 sm:p-7 max-w-md w-full" style={{ animation: 'tho-slide-up 0.2s ease' }}>
        <div className="flex items-start gap-3 mb-5">
          <div className="p-2.5 bg-[var(--cp-accent-dim)] rounded-lg">
            <KeyRound size={20} className="text-[var(--cp-accent)]" />
          </div>
          <div>
            <h2 className="text-2xl font-bold text-[var(--cp-text)]">Save sign-in on this phone</h2>
            <p className="text-base text-[var(--cp-muted)] mt-1 leading-relaxed">
              Next time you can skip the email. Use your work email, the one that ends in @texashomeoutlet.com.
            </p>
          </div>
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            handlePasskeyRegister(passkeyEmail);
          }}
          className="space-y-4"
        >
          <label className="block">
            <span className="text-base font-semibold text-[var(--cp-text)]">Work email</span>
            <input
              type="email"
              autoComplete="email"
              value={passkeyEmail}
              onChange={(event) => {
                setPasskeyEmail(event.target.value);
                setPasskeyEmailError('');
              }}
              placeholder="name@texashomeoutlet.com"
              className="cp-input w-full mt-1.5 px-3 py-3 text-base min-h-12"
              autoFocus
              required
            />
          </label>

          {(passkeyEmailError || passkeyError) && (
            <div role="alert" className="text-base text-[var(--cp-danger)] border border-[var(--cp-danger)]/25 bg-[var(--cp-danger-dim)] rounded-md px-3 py-3 leading-relaxed">
              {passkeyEmailError || passkeyError}
            </div>
          )}

          <div className="flex flex-col sm:flex-row gap-2 sm:justify-end">
            <button
              type="button"
              onClick={() => {
                setShowPasskeyEmailModal(false);
                setPasskeyEmailError('');
              }}
              className="cp-btn-outline min-h-12 px-4 py-3 text-base"
              disabled={passkeyLoading}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={passkeyLoading || !passkeyEmail.trim()}
              className="cp-btn-accent min-h-12 px-4 py-3 text-base flex items-center justify-center gap-2"
            >
              {passkeyLoading ? <Loader2 size={15} className="animate-spin" /> : <KeyRound size={15} />}
              {passkeyLoading ? 'Saving...' : 'Save on this phone'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );

  const appModals = (
    <>
      {pinModal}
      {passkeyEmailModal}
    </>
  );

  // --- Shared nav props ---
  const navProps = {
    activePage: pathname.toLowerCase().startsWith('/ad-studio') ? 'google-ads' : activePage,
    navigateTo,
    adminAuthed,
    onAdminAccess: handleAdminAccess,
    onSignOut: handleSignOut,
    onPasskeyRegister: openPasskeyRegisterModal,
    passkeyLoading,
    showPasskeySave: passkeySignInEnabled,
    isMobileMenuOpen,
    setIsMobileMenuOpen,
    darkMode,
    onToggleDarkMode: () => setDarkMode(prev => !prev),
  };

  // --- Page renders ---
  if (activePage === 'staff-sign-in') {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {passkeyEmailModal}
        <NavBar {...navProps} />
        <main className="max-w-md mx-auto px-4 py-8">
          {adminAuthed ? (
            <div className="text-center">
              <h1 className="text-3xl font-bold mb-4">You are signed in</h1>
              <p className="text-lg leading-relaxed mb-6">
                This phone or computer stays signed in. Start with the people waiting, or a home that needs photos.
              </p>
              <button type="button" onClick={() => navigateTo('staff-home')} className="cp-btn-accent w-full min-h-12 px-6 py-4 text-lg mb-3">
                Go to staff home
              </button>
              <button type="button" onClick={handleSignOut} className="cp-btn-outline w-full min-h-12 px-6 py-3 text-lg">
                Sign out
              </button>
            </div>
          ) : (
            <StaffSignInPanel
              onSuccess={completeStaffSignIn}
              passkeyAvailable={passkeyAvailable}
              passkeyStatus={passkeyStatus}
              passkeyLoading={passkeyLoading}
              passkeyError={passkeyError}
              onPasskeyLogin={handlePasskeyLogin}
              initialNotice={signInNotice}
            />
          )}
        </main>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'staff-home' && adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="staff-home">
          <Suspense fallback={<PageLoader />}>
            <StaffHome onNavigate={navigateTo} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'team' && adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="team">
          <Suspense fallback={<PageLoader />}>
            <StaffAccess onBack={() => navigateTo('staff-home')} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'system' && adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="system">
          <Suspense fallback={<PageLoader />}>
            <SystemHub onBack={() => navigateTo('inventory')} adminAuthed={adminAuthed} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'health' && adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="health">
          <Suspense fallback={<PageLoader />}>
            <HealthDashboard onBack={() => navigateTo('system')} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'getting-started' && adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="getting-started">
          <Suspense fallback={<PageLoader />}>
            <GettingStarted
              onOpenDocuments={() => navigateTo('documents')}
              onOpenCRM={() => navigateTo('crm')}
              onOpenAdStudio={() => navigateTo('adstudio')}
              onOpenPhotos={() => navigateTo('photos')}
            />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'analytics' && adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="analytics">
          <Suspense fallback={<PageLoader />}>
            <Analytics />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'crm' && adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="crm">
          <Suspense fallback={<PageLoader />}>
            <CRM onBack={() => navigateTo('inventory')} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'photos' && adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="photos">
          <Suspense fallback={<PageLoader />}>
            <PhotoManager onBack={() => navigateTo('inventory')} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'manage-inventory' && adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="manage-inventory">
          <Suspense fallback={<PageLoader />}>
            <InventoryManager onBack={() => navigateTo('inventory')} onNavigate={navigateTo} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'chat-history' && adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="chat-history">
          <Suspense fallback={<PageLoader />}>
            <ChatHistory />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'copilot' && adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="copilot">
          <Suspense fallback={<PageLoader />}>
            <OpsCopilot />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'documents' && adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        {!isStandaloneMode && <NavBar {...navProps} />}
        {isStandaloneMode && (
          <header className="bg-[var(--cp-panel)] text-[var(--cp-text)] border-b border-[var(--cp-border)] z-30 sticky top-0">
            <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <FileText className="h-5 w-5 text-[var(--cp-accent)]" />
                <span className="font-bold text-lg">{BUSINESS_NAME} — Document Center</span>
              </div>
              <a href="/" className="text-sm text-[var(--cp-muted)] hover:text-[var(--cp-accent)]">← Main App</a>
            </div>
          </header>
        )}
        <ErrorBoundary scope="documents">
          <Suspense fallback={<PageLoader />}>
            <DocumentCenter onBack={() => navigateTo('chat')} sessionId={sessionId} standalone={isStandaloneMode} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'hub' && activeDealId) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="hub">
          <Suspense fallback={<PageLoader />}>
            <SecureHub dealId={activeDealId} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'adstudio' && adminSignInCheck === 'checking') {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        <NavBar {...navProps} />
        <main className="max-w-md mx-auto px-4 py-16 text-center">
          <h1 className="text-3xl font-bold mb-3">Checking your sign-in...</h1>
          <p className="text-lg leading-relaxed">
            One moment while we check whether this browser is signed in.
          </p>
        </main>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'adstudio' && adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        {!isStandaloneMode && <NavBar {...navProps} />}
        {isStandaloneMode && (
          <header className="bg-[var(--cp-panel)] text-[var(--cp-text)] border-b border-[var(--cp-border)] z-30 sticky top-0">
            <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Video className="h-5 w-5 text-[var(--cp-accent)]" />
                <span className="font-bold text-lg">{BUSINESS_NAME} — Ad Studio</span>
              </div>
              <a href="/" className="text-sm text-[var(--cp-muted)] hover:text-[var(--cp-accent)]">← Main App</a>
            </div>
          </header>
        )}
        <ErrorBoundary scope="adstudio">
          <Suspense fallback={<PageLoader />}>
            <AdStudio
              onBack={() => navigateTo('chat')}
              standalone={isStandaloneMode}
              initialTab={pathname.toLowerCase().startsWith('/ad-studio') ? 'paid-search' : 'create'}
            />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'adstudio' && !adminAuthed) {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <main className="max-w-md mx-auto px-4 py-16 text-center">
          <h1 className="text-3xl font-bold mb-3">Ad Studio is for staff</h1>
          <p className="text-lg leading-relaxed mb-6">
            This page stays locked until you sign in. You can sign in, or go back to the home page.
          </p>
          <button
            type="button"
            onClick={() => {
              setShowPinModal(true);
              setSignInNotice('');
            }}
            className="cp-btn-accent w-full min-h-12 px-6 py-4 text-lg mb-3"
          >
            Staff sign-in
          </button>
          <button
            type="button"
            onClick={() => navigateTo('inventory')}
            className="cp-btn-outline w-full min-h-12 px-6 py-3 text-lg"
          >
            Back to home
          </button>
        </main>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'privacy' || activePage === 'terms') {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen flex flex-col">
        {appModals}
        <NavBar {...navProps} />
        <LegalPage page={activePage} />
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'contact') {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="contact">
          <Suspense fallback={<PageLoader />}>
            <Contact
              onBack={() => navigateTo('inventory')}
              onBookAppointment={startAppointmentHandoff}
            />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'appointments') {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="appointments">
          <Suspense fallback={<PageLoader />}>
            <Appointments
              onBack={() => navigateTo('inventory')}
              prefill={appointmentHandoff}
              onHandoffComplete={() => setAppointmentHandoff(null)}
            />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'locations') {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="locations">
          <Suspense fallback={<PageLoader />}>
            <Locations />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'about') {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="about">
          <Suspense fallback={<PageLoader />}>
            <About onBack={() => navigateTo('inventory')} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'financing') {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="financing">
          <Suspense fallback={<PageLoader />}>
            <Financing onBack={() => navigateTo('inventory')} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'faq') {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="faq">
          <Suspense fallback={<PageLoader />}>
            <FAQ onBack={() => navigateTo('inventory')} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'warranty') {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="warranty">
          <Suspense fallback={<PageLoader />}>
            <Warranty onBack={() => navigateTo('inventory')} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'delivery') {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen">
        {appModals}
        <NavBar {...navProps} />
        <ErrorBoundary scope="delivery">
          <Suspense fallback={<PageLoader />}>
            <Delivery onBack={() => navigateTo('inventory')} />
          </Suspense>
        </ErrorBoundary>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'not-found') {
    return (
      <div className="bg-[var(--cp-bg)] min-h-screen flex flex-col">
        {appModals}
        <NavBar {...navProps} />
        <main className="flex-1 flex items-center justify-center px-6 py-16">
          <div className="max-w-md text-center">
            <p className="text-6xl font-bold text-[var(--cp-accent)] mb-3">404</p>
            <h1 className="text-2xl font-bold text-[var(--cp-text)] mb-2">
              Well, that page wandered off the lot
            </h1>
            <p className="text-[var(--cp-text-dim)] mb-8">
              The page you're looking for doesn't exist or may have moved.
              The homes are still right where we left them.
            </p>
            <div className="flex flex-wrap justify-center gap-3">
              <button
                onClick={() => navigateTo('inventory')}
                className="rounded-md bg-[var(--cp-accent)] px-5 py-3 text-sm font-bold text-[var(--cp-bg)] transition hover:bg-[var(--cp-accent-hot)]"
              >
                Browse Homes
              </button>
              <button
                onClick={() => navigateTo('chat')}
                className="rounded-md border border-[var(--cp-border-light)] bg-[var(--cp-panel)] px-5 py-3 text-sm font-semibold text-[var(--cp-text)] transition hover:border-[var(--cp-secondary)]"
              >
                Ask Tex
              </button>
              <button
                onClick={() => navigateTo('contact')}
                className="rounded-md border border-[var(--cp-border-light)] bg-[var(--cp-panel)] px-5 py-3 text-sm font-semibold text-[var(--cp-text)] transition hover:border-[var(--cp-secondary)]"
              >
                Contact Us
              </button>
            </div>
          </div>
        </main>
        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  if (activePage === 'inventory') {
    return (
      <div className="min-h-screen flex flex-col">
        {appModals}
        <NavBar {...navProps} />

        <ErrorBoundary scope="inventory">
          <Suspense fallback={<PageLoader />}>
            <InventoryBrowse
              key={pathname}
              pathname={pathname}
              adminAuthed={adminAuthed}
              onAskTex={(message) => {
                navigateTo('chat');
                setMessages(prev => [...prev, { role: 'user', text: message }]);
                handleQuickAction(message);
              }}
              onBack={() => navigateTo('chat')}
              onCreateAd={adminAuthed ? () => {
                navigateTo('adstudio');
              } : undefined}
              onBookAppointment={startAppointmentHandoff}
            />
          </Suspense>
        </ErrorBoundary>

        {/* Floating Chat Bubble */}
        <button
          onClick={() => navigateTo('chat')}
          className="fixed bottom-6 right-6 z-50 hidden rounded-full bg-[var(--cp-accent)] p-4 text-[var(--cp-bg)] transition-all duration-200 hover:scale-105 hover:bg-[var(--cp-accent-hot)] active:scale-95 sm:inline-flex cp-glow-accent"
          aria-label="Chat with Tex"
          title="Chat with Tex"
        >
          <MessageSquare size={22} />
          <span className="absolute -top-1 -left-1 bg-[var(--cp-danger)] text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full shadow font-mono">
            Tex
          </span>
        </button>

        <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      </div>
    );
  }

  // ─── Chat Page (default) ───
  const userMessages = messages.filter((message) => message.role === 'user');
  const hasSalesIntent = userMessages.some((message) => (
    /\b(home|inventory|price|pricing|bedroom|bathroom|budget|buy|financ\w*|appointment|visit|tour|available)\b/i
      .test(message.text || '')
  ));
  const volunteeredContact = userMessages.some((message) => chatMessageIncludesContact(message.text));
  const showChatCallback = hasSalesIntent
    && !chatCallbackDismissed
    && (!volunteeredContact || chatCallbackCaptured)
    && !isLoading;

  return (
    <div className="flex flex-col h-screen bg-[var(--cp-bg)] font-sans text-[var(--cp-text)]">
      {/* WCAG 2.4.1 bypass-blocks: lets keyboard users jump past the nav. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-[200] focus:px-4 focus:py-2 focus:rounded-lg focus:bg-[var(--cp-accent)] focus:text-[var(--cp-bg)] focus:shadow-lg"
      >
        Skip to main content
      </a>
      {appModals}
      <NavBar {...navProps} showSearchFilters onApplyFilters={handleApplyFilters} onClearFilters={handleClearFilters} />

      {/* Main Chat Area */}
      <main id="main-content" tabIndex={-1} className="flex-1 overflow-hidden flex flex-col max-w-4xl mx-auto w-full bg-[var(--cp-panel)] shadow-xl md:my-4 md:rounded-lg relative border border-[var(--cp-border)] focus:outline-none">

        {/* Messages List */}
        <div className="flex-1 overflow-y-auto p-4 space-y-6 scrollbar-thin pb-20">
          {messages.map((msg, index) => (
            <div key={index} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`flex max-w-[95%] md:max-w-[85%] ${msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}`}>

                {/* Avatar */}
                <div className={`flex-shrink-0 h-8 w-8 rounded-full flex items-center justify-center mx-2 overflow-hidden
                  ${msg.role === 'user' ? 'bg-[var(--cp-secondary)]' : 'bg-[var(--cp-surface)] border border-[var(--cp-border)]'}`}>
                  {msg.role === 'user' ? (
                    <User size={16} className="text-[var(--cp-bg)]" />
                  ) : (
                    <img src="/tex-icon.svg" alt="Tex assistant" className="h-8 w-8" />
                  )}
                </div>

                {/* Bubble */}
                <div className="flex flex-col">
                  <div className={`p-4 rounded-2xl text-sm leading-relaxed
                     ${msg.role === 'user'
                      ? 'bg-[var(--cp-secondary)] text-[var(--cp-bg)] rounded-tr-none shadow-sm'
                      : msg.isError
                        ? 'bg-[var(--cp-danger-dim)] text-[var(--cp-danger)] rounded-tl-none border border-[var(--cp-danger)]/20'
                        : 'bg-[var(--cp-surface)] text-[var(--cp-text)] rounded-tl-none border border-[var(--cp-border)]'
                    }`}>
                    <Suspense fallback={<span className="whitespace-pre-wrap">{msg.text}</span>}>
                      <SafeMarkdown
                        content={msg.text}
                        comparisonList={comparisonList}
                        onToggleCompare={handleToggleCompare}
                      />
                    </Suspense>
                    {/* Retry button for failed messages */}
                    {msg.isError && msg.originalMessage && (
                      <button
                        onClick={() => handleRetry(msg.originalMessage)}
                        disabled={isLoading}
                        className="mt-2 flex items-center gap-1.5 text-xs font-medium text-[var(--cp-danger)] hover:text-[var(--cp-danger)]/80 disabled:opacity-50 transition-colors font-mono"
                      >
                        <RotateCcw size={12} />
                        {isLoading ? 'Retrying...' : 'Retry'}
                      </button>
                    )}
                  </div>
                  {/* Quick Actions — only after initial greeting */}
                  {msg.showQuickActions && !isLoading && messages.length === 1 && (
                    <QuickActions
                      onActionClick={(message) => {
                        setInput(message);
                        setMessages(prev => [...prev, { role: 'user', text: message }]);
                        setInput('');
                        handleQuickAction(message);
                      }}
                      disabled={isLoading}
                    />
                  )}
                </div>
              </div>
            </div>
          ))}

          {showChatCallback && (
            <ChatCallbackCard
              sessionId={sessionId}
              captured={chatCallbackCaptured}
              onDismiss={() => {
                sessionStorage.setItem(callbackStorageKey, 'dismissed');
                setChatCallbackDismissed(true);
              }}
              onCaptured={() => {
                localStorage.setItem(callbackStorageKey, 'captured');
                setChatCallbackCaptured(true);
              }}
            />
          )}

          {isLoading && (
            <div className="flex justify-start">
              <div className="flex flex-row items-center ml-12 space-x-3 bg-[var(--cp-surface)] p-4 rounded-xl border border-[var(--cp-border)] shadow-sm" role="status" aria-label="Loading response">
                <div className="relative">
                  <Loader2 className="h-5 w-5 animate-spin text-[var(--cp-accent)]" />
                  <div className="absolute inset-0 bg-[var(--cp-accent)] rounded-full animate-ping opacity-20"></div>
                </div>
                <span className="text-sm text-[var(--cp-accent)] font-medium font-mono">Tex is thinking...</span>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <div className="border-t border-[var(--cp-border)] p-4 bg-[var(--cp-panel)] z-20">
          {!isOnline && (
            <div className="mb-3 flex items-center justify-center gap-2 text-xs text-[var(--cp-warn)] bg-[var(--cp-warn)]/10 px-3 py-2 rounded-lg font-mono border border-[var(--cp-warn)]/20">
              <WifiOff size={14} />
              <span>You're offline. Messages will be sent when you reconnect.</span>
            </div>
          )}
          <form onSubmit={handleSubmit} className="relative flex items-center">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={isOnline ? "Ask about homes or pricing... (Ctrl+K to focus)" : "Waiting for connection..."}
              className="cp-input w-full pl-4 pr-12 py-3 rounded-full shadow-sm disabled:opacity-50"
              disabled={isLoading || !isOnline}
              aria-label="Chat message"
            />
            <button
              type="submit"
              disabled={!input.trim() || isLoading || !isOnline}
              className="absolute right-2 p-2 bg-[var(--cp-accent)] text-[var(--cp-bg)] rounded-full hover:bg-[var(--cp-accent-hot)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors cp-glow-accent"
              aria-label="Send message"
            >
              <Send size={18} />
            </button>
          </form>
          <div className="mt-2 flex items-center justify-center gap-4 text-xs text-[var(--cp-muted)] font-mono">
            <span>AI can make mistakes. Please verify pricing with an agent.</span>
            <span className="hidden sm:inline">•</span>
            <span className="hidden sm:inline">Ctrl+K to focus</span>
            <span className="hidden sm:inline">•</span>
            <span className="hidden sm:inline">Ctrl+/ for help</span>
          </div>
        </div>

        {/* Comparison Drawer */}
        <ComparisonDrawer
          isOpen={comparisonList.length > 0}
          onClose={() => setComparisonList([])}
          properties={comparisonList}
          onRemove={handleRemoveFromCompare}
        />

      </main>

      <Footer adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} onNavigate={navigateTo} />
      <ReportIssue />
    </div>
  );
}

export default App;
