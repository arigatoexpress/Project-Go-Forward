import React, { useState, useEffect, useRef, lazy, Suspense, useCallback } from 'react';
import { Send, Home, Menu, X, Phone, MapPin, Loader2, User, Bot, FileText, Video, Lock, ShieldCheck, CalendarDays, Users, MessageSquare, RotateCcw, WifiOff } from 'lucide-react';
import SafeMarkdown from './components/SafeMarkdown';
import SearchFilters from './components/SearchFilters';
import QuickActions from './components/QuickActions';
import ComparisonDrawer from './components/ComparisonDrawer';
import { useToast } from './components/Toast';
import { useNetworkStatus } from './components/NetworkStatus';
import { v4 as uuidv4 } from 'uuid';
import {
  BUSINESS_NAME, BUSINESS_PHONE, BUSINESS_PHONE_RAW, BUSINESS_ADDRESS,
  BUSINESS_CITY, BUSINESS_HOURS
} from './constants';

const API_URL = '/run'; // Relative path for single-container deployment

// Lazy-load heavy page components for code-splitting
const InventoryBrowse = lazy(() => import('./pages/InventoryBrowse'));
const Analytics = lazy(() => import('./pages/Analytics'));
const DocumentCenter = lazy(() => import('./pages/DocumentCenter'));
const AdStudio = lazy(() => import('./pages/AdStudio'));
const Contact = lazy(() => import('./pages/Contact'));
const Appointments = lazy(() => import('./pages/Appointments'));
const CRM = lazy(() => import('./pages/CRM'));

// Page loading fallback with skeleton
const PageLoader = () => (
  <div className="flex flex-col items-center justify-center min-h-[50vh] gap-3">
    <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
    <span className="text-sm text-gray-400 font-medium">Loading...</span>
  </div>
);

// ─── Shared Navigation Component ───
function NavBar({ activePage, navigateTo, adminAuthed, onAdminAccess, isMobileMenuOpen, setIsMobileMenuOpen, showSearchFilters, onApplyFilters, onClearFilters }) {
  const navItems = [
    { key: 'inventory', label: 'Inventory', icon: Home },
    { key: 'chat', label: 'Chat', icon: MessageSquare },
    { key: 'contact', label: 'Contact', icon: Phone },
    { key: 'appointments', label: 'Book Visit', icon: CalendarDays },
  ];

  const adminItems = adminAuthed ? [
    { key: 'documents', label: 'Documents', icon: FileText },
    { key: 'adstudio', label: 'Ad Studio', icon: Video },
    { key: 'crm', label: 'CRM', icon: Users },
  ] : [];

  const allItems = [...navItems, ...adminItems];

  return (
    <header className="bg-blue-900 text-white shadow-md z-30 sticky top-0">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        {/* Logo */}
        <div
          className="flex items-center space-x-3 cursor-pointer"
          onClick={() => navigateTo('inventory')}
          role="button"
          aria-label="Go to home page"
        >
          <Home className="h-7 w-7 text-red-400" />
          <h1 className="text-lg font-bold tracking-tight">{BUSINESS_NAME}</h1>
        </div>

        <div className="flex items-center gap-3">
          {/* Desktop nav */}
          <nav className="hidden md:flex items-center space-x-1 text-sm font-medium" aria-label="Main navigation">
            {allItems.map(item => {
              const Icon = item.icon;
              const isActive = activePage === item.key;
              return (
                <button
                  key={item.key}
                  onClick={() => navigateTo(item.key)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md transition-colors ${
                    isActive
                      ? 'bg-white/15 text-white'
                      : 'text-blue-200 hover:text-white hover:bg-white/10'
                  }`}
                  aria-current={isActive ? 'page' : undefined}
                >
                  <Icon size={15} />
                  {item.label}
                </button>
              );
            })}
          </nav>

          {/* Search filters — only on chat page */}
          {showSearchFilters && (
            <SearchFilters onApplyFilters={onApplyFilters} onClear={onClearFilters} />
          )}

          {/* Admin button — desktop */}
          <button
            onClick={onAdminAccess}
            className="hidden md:flex items-center gap-1 px-2 py-1.5 text-xs text-blue-300 hover:text-white hover:bg-white/10 rounded-md transition-colors"
            aria-label="Admin access"
          >
            {adminAuthed ? <ShieldCheck size={14} /> : <Lock size={14} />}
          </button>

          {/* Mobile menu button */}
          <button
            className="md:hidden p-2 hover:bg-white/10 rounded-lg transition"
            onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
            aria-label={isMobileMenuOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={isMobileMenuOpen}
          >
            {isMobileMenuOpen ? <X size={24} /> : <Menu size={24} />}
          </button>
        </div>
      </div>

      {/* Mobile Menu */}
      {isMobileMenuOpen && (
        <nav
          className="md:hidden bg-blue-900 border-t border-blue-800 py-2 px-4"
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
                className={`flex items-center w-full py-3 px-2 rounded-lg transition-colors ${
                  isActive
                    ? 'bg-white/15 text-white'
                    : 'text-blue-200 hover:text-white hover:bg-white/10'
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
            className="flex items-center w-full py-3 px-2 text-blue-300 hover:text-white hover:bg-white/10 rounded-lg transition-colors mt-1 border-t border-blue-800 pt-3"
          >
            {adminAuthed ? <ShieldCheck size={18} className="mr-3" /> : <Lock size={18} className="mr-3" />}
            {adminAuthed ? 'Analytics' : 'Admin'}
          </button>
        </nav>
      )}
    </header>
  );
}

// ─── Footer Component ───
function Footer({ navigateTo, adminAuthed, onAdminAccess }) {
  return (
    <footer className="bg-white border-t border-gray-200 py-4 text-center text-xs text-gray-500">
      <div className="flex items-center justify-center gap-6 flex-wrap">
        <span className="flex items-center"><MapPin size={12} className="mr-1" aria-hidden="true" /> {BUSINESS_ADDRESS}, {BUSINESS_CITY}</span>
        <a href={`tel:${BUSINESS_PHONE_RAW}`} className="flex items-center hover:text-blue-600 transition-colors">
          <Phone size={12} className="mr-1" aria-hidden="true" /> {BUSINESS_PHONE}
        </a>
        <span>{BUSINESS_HOURS}</span>
        <button onClick={onAdminAccess} className="flex items-center hover:text-blue-600 transition-colors">
          {adminAuthed ? <ShieldCheck size={12} className="mr-1" /> : <Lock size={12} className="mr-1" />}
          Admin
        </button>
      </div>
    </footer>
  );
}

// ─── Shared API call helper with retry logic ───
async function sendToAgent(sessionId, text, maxRetries = 2) {
  let lastError;
  
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const response = await fetch(API_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json'
        },
        body: JSON.stringify({
          appName: 'root_agent',
          userId: `web_user_${sessionId}`,
          sessionId: sessionId,
          newMessage: {
            role: 'user',
            parts: [{ text }]
          }
        })
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      if (data.error) return `System Error: ${data.error}`;
      if (data.text) return data.text;
      if (data.content) return typeof data.content === 'string' ? data.content : JSON.stringify(data.content);
      if (data.candidates?.[0]?.content?.parts) {
        return data.candidates[0].content.parts.map(p => p.text).join(' ');
      }
      return "I apologize, I didn't catch that. Could you rephrase?";
      
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
  const [sessionId] = useState(() => localStorage.getItem('tho_session_id') || uuidv4());
  const [activeFilters, setActiveFilters] = useState({});
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [comparisonList, setComparisonList] = useState([]);

  // Single page state
  const [activePage, setActivePage] = useState('inventory');

  // Admin auth — token validated by backend
  const [adminAuthed, setAdminAuthed] = useState(false);
  const [adminToken, setAdminToken] = useState(() => sessionStorage.getItem('tho_admin_token') || '');
  const [showPinModal, setShowPinModal] = useState(false);
  const [pinInput, setPinInput] = useState('');
  const [pinError, setPinError] = useState('');
  const [pinLoading, setPinLoading] = useState(false);
  
  // Retry failed messages
  const [failedMessages, setFailedMessages] = useState([]);

  // Verify stored token on mount
  useEffect(() => {
    if (!adminToken) return;
    fetch('/api/admin/check', { headers: { 'X-Admin-Token': adminToken } })
      .then(r => { if (r.ok) setAdminAuthed(true); else { setAdminToken(''); sessionStorage.removeItem('tho_admin_token'); } })
      .catch(() => {});
  }, [adminToken]);

  // Listen for expired admin session (fired by adminFetch on 401)
  useEffect(() => {
    const handleExpired = () => {
      setAdminAuthed(false);
      setAdminToken('');
      setShowPinModal(true);
      setPinError('Session expired — please re-enter PIN');
    };
    window.addEventListener('admin-session-expired', handleExpired);
    return () => window.removeEventListener('admin-session-expired', handleExpired);
  }, []);

  const messagesEndRef = useRef(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('admin') === 'true') {
      if (adminAuthed) {
        setActivePage('analytics');
      } else {
        setShowPinModal(true);
      }
    }
    localStorage.setItem('tho_session_id', sessionId);
  }, [sessionId, adminAuthed]);
  
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

  // Update page title per page
  useEffect(() => {
    const titles = {
      inventory: 'Browse Homes',
      chat: 'Chat with Tex',
      documents: 'Documents',
      adstudio: 'Ad Studio',
      contact: 'Contact Us',
      appointments: 'Book a Visit',
      analytics: 'Analytics',
      crm: 'CRM Dashboard',
    };
    document.title = titles[activePage]
      ? `${titles[activePage]} | ${BUSINESS_NAME}`
      : BUSINESS_NAME;
  }, [activePage]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const navigateTo = (page) => {
    setActivePage(page);
    setIsMobileMenuOpen(false);
    window.scrollTo({ top: 0 });
  };

  // Admin PIN handlers
  const handleAdminAccess = () => {
    if (adminAuthed) {
      navigateTo('analytics');
    } else {
      setShowPinModal(true);
      setPinInput('');
      setPinError('');
    }
  };

  const handlePinSubmit = async (e) => {
    e.preventDefault();
    setPinLoading(true);
    setPinError('');
    try {
      const res = await fetch('/api/admin/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin: pinInput }),
      });
      const data = await res.json();
      if (data.success && data.token) {
        setAdminAuthed(true);
        setAdminToken(data.token);
        sessionStorage.setItem('tho_admin_token', data.token);
        setShowPinModal(false);
        setPinInput('');
        navigateTo('analytics');
      } else {
        setPinError(data.error || 'Incorrect PIN. Please try again.');
        setPinInput('');
      }
    } catch {
      setPinError('Unable to verify. Please try again.');
    } finally {
      setPinLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage = { role: 'user', text: input };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);

    try {
      const botText = await sendToAgent(sessionId, userMessage.text);
      setMessages(prev => [...prev, { role: 'model', text: botText }]);
    } catch (error) {
      console.error('Error sending message:', error);
      
      // Store failed message for retry
      const failedMsg = { ...userMessage, timestamp: Date.now() };
      setFailedMessages(prev => [...prev, failedMsg]);
      
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
      const botText = await sendToAgent(sessionId, originalMessage);
      setMessages(prev => [...prev, { role: 'model', text: botText }]);
      addToast('Message sent successfully!', 'success');
    } catch (error) {
      addToast('Still having trouble connecting. Please try again later.', 'error');
    } finally {
      setIsLoading(false);
    }
  }, [sessionId, addToast]);

  const handleQuickAction = async (message) => {
    if (!isOnline) {
      addToast('You appear to be offline. Please check your connection.', 'warning');
      return;
    }
    
    setIsLoading(true);
    try {
      const botText = await sendToAgent(sessionId, message);
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
    setActiveFilters(filters);
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
    setActiveFilters({});
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
    <div className="fixed inset-0 bg-black/50 z-[100] flex items-center justify-center p-4" style={{ animation: 'tho-fade-in 0.15s ease' }}>
      <div className="bg-white rounded-xl shadow-2xl p-8 max-w-sm w-full" style={{ animation: 'tho-slide-up 0.2s ease' }}>
        <div className="flex items-center justify-center mb-4">
          <div className="p-3 bg-blue-100 rounded-full">
            <Lock size={24} className="text-blue-600" />
          </div>
        </div>
        <h2 className="text-xl font-bold text-center text-gray-900 mb-2">Admin Access</h2>
        <p className="text-sm text-gray-500 text-center mb-6">Enter your PIN to access the admin dashboard.</p>
        <form onSubmit={handlePinSubmit}>
          <input
            type="password"
            inputMode="numeric"
            maxLength={6}
            value={pinInput}
            onChange={(e) => { setPinInput(e.target.value); setPinError(''); }}
            placeholder="Enter PIN"
            className="w-full px-4 py-3 text-center text-xl tracking-widest border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
            autoFocus
            aria-label="Admin PIN"
          />
          {pinError && <p className="text-red-500 text-sm text-center mt-2">{pinError}</p>}
          <button
            type="submit"
            disabled={!pinInput.trim() || pinLoading}
            className="w-full mt-4 py-3 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition"
          >
            {pinLoading ? 'Verifying...' : 'Unlock'}
          </button>
          <button
            type="button"
            onClick={() => setShowPinModal(false)}
            className="w-full mt-2 py-2 text-gray-500 text-sm hover:text-gray-700 transition"
          >
            Cancel
          </button>
        </form>
      </div>
    </div>
  );

  // --- Shared nav props ---
  const navProps = {
    activePage,
    navigateTo,
    adminAuthed,
    onAdminAccess: handleAdminAccess,
    isMobileMenuOpen,
    setIsMobileMenuOpen,
  };

  // --- Page renders ---
  if (activePage === 'analytics' && adminAuthed) {
    return (
      <div className="bg-gray-50 min-h-screen">
        <NavBar {...navProps} />
        <Suspense fallback={<PageLoader />}>
          <Analytics />
        </Suspense>
      </div>
    );
  }

  if (activePage === 'crm' && adminAuthed) {
    return (
      <div className="bg-gray-50 min-h-screen">
        <NavBar {...navProps} />
        <Suspense fallback={<PageLoader />}>
          <CRM onBack={() => navigateTo('inventory')} />
        </Suspense>
      </div>
    );
  }

  if (activePage === 'documents' && adminAuthed) {
    return (
      <div className="bg-gray-50 min-h-screen">
        <NavBar {...navProps} />
        <Suspense fallback={<PageLoader />}>
          <DocumentCenter onBack={() => navigateTo('chat')} sessionId={sessionId} />
        </Suspense>
      </div>
    );
  }

  if (activePage === 'adstudio' && adminAuthed) {
    return (
      <div className="bg-gray-50 min-h-screen">
        <NavBar {...navProps} />
        <Suspense fallback={<PageLoader />}>
          <AdStudio onBack={() => navigateTo('chat')} />
        </Suspense>
      </div>
    );
  }

  if (activePage === 'contact') {
    return (
      <div className="bg-gray-50 min-h-screen">
        <NavBar {...navProps} />
        <Suspense fallback={<PageLoader />}>
          <Contact onBack={() => navigateTo('inventory')} />
        </Suspense>
      </div>
    );
  }

  if (activePage === 'appointments') {
    return (
      <div className="bg-gray-50 min-h-screen">
        <NavBar {...navProps} />
        <Suspense fallback={<PageLoader />}>
          <Appointments onBack={() => navigateTo('inventory')} />
        </Suspense>
      </div>
    );
  }

  if (activePage === 'inventory') {
    return (
      <div className="bg-gray-50 min-h-screen flex flex-col">
        {pinModal}
        <NavBar {...navProps} />

        <Suspense fallback={<PageLoader />}>
          <InventoryBrowse
            onAskTex={(message) => {
              navigateTo('chat');
              setMessages(prev => [...prev, { role: 'user', text: message }]);
              handleQuickAction(message);
            }}
            onBack={() => navigateTo('chat')}
            onCreateAd={adminAuthed ? (homeName) => {
              navigateTo('adstudio');
            } : undefined}
          />
        </Suspense>

        {/* Floating Chat Bubble */}
        <button
          onClick={() => navigateTo('chat')}
          className="fixed bottom-6 right-6 z-50 bg-blue-600 text-white p-4 rounded-full hover:bg-blue-700 hover:scale-105 active:scale-95 transition-all duration-200 group"
          aria-label="Chat with Tex"
          title="Chat with Tex"
          style={{ boxShadow: '0 4px 20px rgba(37, 99, 235, 0.4)' }}
        >
          <MessageSquare size={22} />
          <span className="absolute -top-1 -left-1 bg-red-500 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full shadow">
            Tex
          </span>
        </button>

        <Footer navigateTo={navigateTo} adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} />
      </div>
    );
  }

  // ─── Chat Page (default) ───
  return (
    <div className="flex flex-col h-screen bg-gray-50 font-sans text-gray-900">
      {pinModal}
      <NavBar {...navProps} showSearchFilters onApplyFilters={handleApplyFilters} onClearFilters={handleClearFilters} />

      {/* Main Chat Area */}
      <main className="flex-1 overflow-hidden flex flex-col max-w-4xl mx-auto w-full bg-white shadow-xl md:my-4 md:rounded-lg relative">

        {/* Messages List */}
        <div className="flex-1 overflow-y-auto p-4 space-y-6 scrollbar-thin pb-20">
          {messages.map((msg, index) => (
            <div key={index} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`flex max-w-[95%] md:max-w-[85%] ${msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}`}>

                {/* Avatar */}
                <div className={`flex-shrink-0 h-8 w-8 rounded-full flex items-center justify-center mx-2 overflow-hidden
                  ${msg.role === 'user' ? 'bg-blue-600' : ''}`}>
                  {msg.role === 'user' ? (
                    <User size={16} className="text-white" />
                  ) : (
                    <img src="/tex-icon.svg" alt="Tex assistant" className="h-8 w-8" />
                  )}
                </div>

                {/* Bubble */}
                <div className="flex flex-col">
                  <div className={`p-4 rounded-2xl text-sm leading-relaxed
                     ${msg.role === 'user'
                      ? 'bg-blue-600 text-white rounded-tr-none shadow-sm'
                      : msg.isError 
                        ? 'bg-red-50 text-red-800 rounded-tl-none border border-red-200'
                        : 'bg-gray-100 text-gray-800 rounded-tl-none border border-gray-200'
                    }`}>
                    <SafeMarkdown
                      content={msg.text}
                      comparisonList={comparisonList}
                      onToggleCompare={handleToggleCompare}
                    />
                    {/* Retry button for failed messages */}
                    {msg.isError && msg.originalMessage && (
                      <button
                        onClick={() => handleRetry(msg.originalMessage)}
                        disabled={isLoading}
                        className="mt-2 flex items-center gap-1.5 text-xs font-medium text-red-600 hover:text-red-800 disabled:opacity-50 transition-colors"
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

          {isLoading && (
            <div className="flex justify-start">
              <div className="flex flex-row items-center ml-12 space-x-3 bg-blue-50 p-4 rounded-xl border border-blue-100 shadow-sm" role="status" aria-label="Loading response">
                <div className="relative">
                  <Loader2 className="h-5 w-5 animate-spin text-blue-600" />
                  <div className="absolute inset-0 bg-blue-400 rounded-full animate-ping opacity-20"></div>
                </div>
                <span className="text-sm text-blue-700 font-medium">Tex is thinking...</span>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <div className="border-t border-gray-200 p-4 bg-white z-20">
          {!isOnline && (
            <div className="mb-3 flex items-center justify-center gap-2 text-xs text-amber-600 bg-amber-50 px-3 py-2 rounded-lg">
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
              className="w-full pl-4 pr-12 py-3 border border-gray-300 rounded-full focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition shadow-sm disabled:bg-gray-100"
              disabled={isLoading || !isOnline}
              aria-label="Chat message"
            />
            <button
              type="submit"
              disabled={!input.trim() || isLoading || !isOnline}
              className="absolute right-2 p-2 bg-blue-600 text-white rounded-full hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              aria-label="Send message"
            >
              <Send size={18} />
            </button>
          </form>
          <div className="mt-2 flex items-center justify-center gap-4 text-xs text-gray-400">
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

      <Footer navigateTo={navigateTo} adminAuthed={adminAuthed} onAdminAccess={handleAdminAccess} />
    </div>
  );
}

export default App;
