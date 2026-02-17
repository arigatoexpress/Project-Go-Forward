import React, { useState, useEffect, useRef, lazy, Suspense } from 'react';
import { Send, Home, Menu, X, Phone, MapPin, Loader2, User, Bot, FileText, Video, Lock, ShieldCheck, CalendarDays, Users } from 'lucide-react';
import SafeMarkdown from './components/SafeMarkdown';
import SearchFilters from './components/SearchFilters';
import QuickActions from './components/QuickActions';
import ComparisonDrawer from './components/ComparisonDrawer';
import { v4 as uuidv4 } from 'uuid';

const API_URL = '/run'; // Relative path for single-container deployment

// Lazy-load heavy page components for code-splitting
const InventoryBrowse = lazy(() => import('./pages/InventoryBrowse'));
const Analytics = lazy(() => import('./pages/Analytics'));
const DocumentCenter = lazy(() => import('./pages/DocumentCenter'));
const AdStudio = lazy(() => import('./pages/AdStudio'));
const Contact = lazy(() => import('./pages/Contact'));
const Appointments = lazy(() => import('./pages/Appointments'));
const CRM = lazy(() => import('./pages/CRM'));

const BUSINESS_NAME = "Texas Home Outlet";
const BUSINESS_SHORT = "tho";
const BUSINESS_URL = "texashomeoutlet.com";
const BUSINESS_PHONE = "(281) 324-3020";
const BUSINESS_ADDRESS = "10685 FM 1960 East";
const BUSINESS_CITY = "Huffman";

// Admin PIN for analytics access (simple gate — not a full auth system)
const ADMIN_PIN = "4832";

// Page loading fallback
const PageLoader = () => (
  <div className="flex items-center justify-center min-h-[50vh]">
    <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
  </div>
);

// Shared API call helper — DRYs up handleSubmit and handleQuickAction
async function sendToAgent(sessionId, text) {
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

  if (!response.ok) throw new Error('Network response was not ok');

  const data = await response.json();

  // Normalize response — backend may return in different shapes
  if (data.error) return `System Error: ${data.error}`;
  if (data.text) return data.text;
  if (data.content) return typeof data.content === 'string' ? data.content : JSON.stringify(data.content);
  if (data.candidates?.[0]?.content?.parts) {
    return data.candidates[0].content.parts.map(p => p.text).join(' ');
  }
  return "I apologize, I didn't catch that. Could you rephrase?";
}

function App() {
  const [messages, setMessages] = useState([
    {
      role: 'model',
      text: `Howdy! 🤠 Welcome to ${BUSINESS_NAME}. I'm Tex, your virtual housing consultant. How can I help you today?`,
      showQuickActions: true
    }
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId] = useState(() => localStorage.getItem('tho_session_id') || uuidv4());
  const [activeFilters, setActiveFilters] = useState({});
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [comparisonList, setComparisonList] = useState([]);

  // Single page state instead of multiple booleans
  const [activePage, setActivePage] = useState('inventory'); // 'inventory' | 'chat' | 'documents' | 'adstudio' | 'contact' | 'appointments' | 'analytics' | 'crm'

  // Admin PIN gate
  const [adminAuthed, setAdminAuthed] = useState(() => sessionStorage.getItem('tho_admin') === 'true');
  const [showPinModal, setShowPinModal] = useState(false);
  const [pinInput, setPinInput] = useState('');
  const [pinError, setPinError] = useState('');

  const messagesEndRef = useRef(null);

  useEffect(() => {
    // Check for admin query param
    const params = new URLSearchParams(window.location.search);
    if (params.get('admin') === 'true') {
      if (adminAuthed) {
        setActivePage('analytics');
      } else {
        setShowPinModal(true);
      }
    }

    localStorage.setItem('tho_session_id', sessionId);
  }, [sessionId]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Navigation helper — cleans up repetitive setter calls
  const navigateTo = (page) => {
    setActivePage(page);
    setIsMobileMenuOpen(false);
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

  const handlePinSubmit = (e) => {
    e.preventDefault();
    if (pinInput === ADMIN_PIN) {
      setAdminAuthed(true);
      sessionStorage.setItem('tho_admin', 'true');
      setShowPinModal(false);
      setPinInput('');
      setPinError('');
      navigateTo('analytics');
    } else {
      setPinError('Incorrect PIN. Please try again.');
      setPinInput('');
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
      setMessages(prev => [...prev, { role: 'model', text: `I'm having trouble connecting to the home base right now. Please try again or call us at ${BUSINESS_PHONE}.` }]);
    } finally {
      setIsLoading(false);
    }
  };

  // Handle quick action button clicks
  const handleQuickAction = async (message) => {
    setIsLoading(true);
    try {
      const botText = await sendToAgent(sessionId, message);
      setMessages(prev => [...prev, { role: 'model', text: botText }]);
    } catch (error) {
      console.error('Error sending quick action:', error);
      setMessages(prev => [...prev, { role: 'model', text: `I'm having trouble connecting right now. Please try again or call us at ${BUSINESS_PHONE}.` }]);
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
      if (exists) {
        return prev.filter(p => p.id !== property.id);
      } else {
        if (prev.length >= 3) return prev;
        return [...prev, property];
      }
    });
  };

  const handleRemoveFromCompare = (id) => {
    setComparisonList(prev => prev.filter(p => p.id !== id));
  };

  // --- PIN Modal ---
  const pinModal = showPinModal && (
    <div className="fixed inset-0 bg-black/50 z-[100] flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl p-8 max-w-sm w-full">
        <div className="flex items-center justify-center mb-4">
          <div className="p-3 bg-blue-100 rounded-full">
            <Lock size={24} className="text-blue-600" />
          </div>
        </div>
        <h2 className="text-xl font-bold text-center text-gray-900 mb-2">Admin Access</h2>
        <p className="text-sm text-gray-500 text-center mb-6">Enter your PIN to access the analytics dashboard.</p>
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
            disabled={!pinInput.trim()}
            className="w-full mt-4 py-3 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition"
          >
            Unlock
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

  // --- Page renders with Suspense ---
  if (activePage === 'analytics' && adminAuthed) {
    return (
      <div className="bg-gray-50 min-h-screen">
        <button
          onClick={() => navigateTo('chat')}
          className="fixed bottom-4 right-4 z-50 bg-gray-800 text-white px-4 py-2 rounded-full shadow-lg hover:bg-gray-700 transition"
          aria-label="Exit Analytics"
        >
          Exit Analytics
        </button>
        <Suspense fallback={<PageLoader />}>
          <Analytics />
        </Suspense>
      </div>
    );
  }

  if (activePage === 'crm' && adminAuthed) {
    return (
      <div className="bg-gray-50 min-h-screen">
        <Suspense fallback={<PageLoader />}>
          <CRM onBack={() => navigateTo('inventory')} />
        </Suspense>
      </div>
    );
  }

  if (activePage === 'documents') {
    return (
      <div className="bg-gray-50 min-h-screen">
        <Suspense fallback={<PageLoader />}>
          <DocumentCenter onBack={() => navigateTo('chat')} sessionId={sessionId} />
        </Suspense>
      </div>
    );
  }

  if (activePage === 'adstudio') {
    return (
      <div className="bg-gray-50 min-h-screen">
        <button
          onClick={() => navigateTo('chat')}
          className="fixed bottom-4 right-4 z-50 bg-gray-800 text-white px-4 py-2 rounded-full shadow-lg hover:bg-gray-700 transition"
          aria-label="Exit Ad Studio"
        >
          Exit Ad Studio
        </button>
        <Suspense fallback={<PageLoader />}>
          <AdStudio onBack={() => navigateTo('chat')} />
        </Suspense>
      </div>
    );
  }

  if (activePage === 'contact') {
    return (
      <div className="bg-gray-50 min-h-screen">
        <Suspense fallback={<PageLoader />}>
          <Contact onBack={() => navigateTo('chat')} />
        </Suspense>
      </div>
    );
  }

  if (activePage === 'appointments') {
    return (
      <div className="bg-gray-50 min-h-screen">
        <Suspense fallback={<PageLoader />}>
          <Appointments onBack={() => navigateTo('chat')} />
        </Suspense>
      </div>
    );
  }

  if (activePage === 'inventory') {
    return (
      <div className="bg-gray-50 min-h-screen">
        {pinModal}
        {/* Sticky Header */}
        <header className="bg-blue-900 text-white shadow-md z-30 sticky top-0">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
            <div className="flex items-center space-x-3 cursor-pointer" onClick={() => navigateTo('inventory')} role="button" aria-label="Go to home page">
              <Home className="h-8 w-8 text-red-500" />
              <h1 className="text-xl font-bold tracking-tight">{BUSINESS_NAME}</h1>
            </div>
            <div className="flex items-center gap-4">
              <nav className="hidden md:flex space-x-6 text-sm font-medium" aria-label="Main navigation">
                <button onClick={() => navigateTo('inventory')} className="text-red-400">Inventory</button>
                <button onClick={() => navigateTo('documents')} className="flex items-center hover:text-red-400 transition">
                  <FileText size={16} className="mr-1" /> Documents
                </button>
                <button onClick={() => navigateTo('adstudio')} className="flex items-center hover:text-red-400 transition">
                  <Video size={16} className="mr-1" /> Ad Studio
                </button>
                <button onClick={() => navigateTo('contact')} className="hover:text-red-400 transition">Contact</button>
                <button onClick={() => navigateTo('appointments')} className="flex items-center hover:text-red-400 transition">
                  <CalendarDays size={16} className="mr-1" /> Book Visit
                </button>
                {adminAuthed && (
                  <button onClick={() => navigateTo('crm')} className="flex items-center hover:text-red-400 transition">
                    <Users size={16} className="mr-1" /> CRM
                  </button>
                )}
              </nav>
              <button
                className="md:hidden p-2 hover:bg-white/10 rounded-lg transition"
                onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
                aria-label={isMobileMenuOpen ? 'Close menu' : 'Open menu'}
              >
                {isMobileMenuOpen ? <X size={24} /> : <Menu size={24} />}
              </button>
            </div>
          </div>
          {isMobileMenuOpen && (
            <nav className="md:hidden bg-blue-900 border-t border-blue-800 py-4 px-4 space-y-4" aria-label="Mobile navigation">
              <button onClick={() => navigateTo('inventory')} className="flex items-center w-full py-2 text-red-400 border-b border-blue-800"><Home size={18} className="mr-3" /> Inventory</button>
              <button onClick={() => navigateTo('chat')} className="flex items-center w-full py-2 hover:text-red-400 transition border-b border-blue-800"><Send size={18} className="mr-3" /> Chat with Tex</button>
              <button onClick={() => navigateTo('documents')} className="flex items-center w-full py-2 hover:text-red-400 transition border-b border-blue-800"><FileText size={18} className="mr-3" /> Documents</button>
              <button onClick={() => navigateTo('adstudio')} className="flex items-center w-full py-2 hover:text-red-400 transition border-b border-blue-800"><Video size={18} className="mr-3" /> Ad Studio</button>
              <button onClick={() => navigateTo('contact')} className="flex items-center w-full py-2 hover:text-red-400 transition border-b border-blue-800"><Phone size={18} className="mr-3" /> Contact</button>
              <button onClick={() => navigateTo('appointments')} className="flex items-center w-full py-2 hover:text-red-400 transition border-b border-blue-800"><CalendarDays size={18} className="mr-3" /> Book Visit</button>
              {adminAuthed && (
                <button onClick={() => navigateTo('crm')} className="flex items-center w-full py-2 hover:text-red-400 transition"><Users size={18} className="mr-3" /> CRM Dashboard</button>
              )}
            </nav>
          )}
        </header>

        <Suspense fallback={<PageLoader />}>
          <InventoryBrowse
            onAskTex={(message) => {
              // Switch to chat and pre-fill + send the message
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
          className="fixed bottom-6 right-6 z-50 bg-blue-600 text-white p-4 rounded-full shadow-xl hover:bg-blue-700 hover:scale-110 transition-all duration-200 group"
          aria-label="Chat with Tex"
          title="Chat with Tex"
        >
          <Send size={22} />
          <span className="absolute -top-2 -left-2 bg-red-500 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full shadow group-hover:scale-110 transition">
            Tex
          </span>
        </button>

        {/* Footer */}
        <footer className="bg-white border-t border-gray-200 py-4 text-center text-xs text-gray-500 hidden md:block">
          <div className="flex justify-center space-x-6">
            <span className="flex items-center"><MapPin size={12} className="mr-1" /> {BUSINESS_ADDRESS} East, {BUSINESS_CITY}</span>
            <span className="flex items-center"><Phone size={12} className="mr-1" /> {BUSINESS_PHONE}</span>
            <span>Mon-Fri 9-6, Sat 9-5</span>
            <button onClick={handleAdminAccess} className="flex items-center hover:text-blue-600 transition-colors">
              {adminAuthed ? <ShieldCheck size={12} className="mr-1" /> : <Lock size={12} className="mr-1" />}
              Admin
            </button>
          </div>
        </footer>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50 font-sans text-gray-900">
      {pinModal}

      {/* Header */}
      <header className="bg-blue-900 text-white shadow-md z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center space-x-3 cursor-pointer" onClick={() => navigateTo('inventory')} role="button" aria-label="Go to home page">
            <Home className="h-8 w-8 text-red-500" />
            <h1 className="text-xl font-bold tracking-tight">{BUSINESS_NAME}</h1>
          </div>

          <div className="flex items-center gap-4">
            <nav className="hidden md:flex space-x-6 text-sm font-medium" aria-label="Main navigation">
              <button onClick={() => navigateTo('inventory')} className="hover:text-red-400 transition">Inventory</button>
              <button onClick={() => navigateTo('chat')} className={`hover:text-red-400 transition ${activePage === 'chat' ? 'text-red-400' : ''}`}>Chat</button>
              <button onClick={() => navigateTo('documents')} className={`flex items-center hover:text-red-400 transition ${activePage === 'documents' ? 'text-red-400' : ''}`}>
                <FileText size={16} className="mr-1" /> Documents
              </button>
              <button onClick={() => navigateTo('adstudio')} className={`flex items-center hover:text-red-400 transition ${activePage === 'adstudio' ? 'text-red-400' : ''}`}>
                <Video size={16} className="mr-1" /> Ad Studio
              </button>
              <button onClick={() => navigateTo('contact')} className={`hover:text-red-400 transition ${activePage === 'contact' ? 'text-red-400' : ''}`}>Contact</button>
              <button onClick={() => navigateTo('appointments')} className={`flex items-center hover:text-red-400 transition ${activePage === 'appointments' ? 'text-red-400' : ''}`}>
                <CalendarDays size={16} className="mr-1" /> Book Visit
              </button>
              {adminAuthed && (
                <button onClick={() => navigateTo('crm')} className={`flex items-center hover:text-red-400 transition ${activePage === 'crm' ? 'text-red-400' : ''}`}>
                  <Users size={16} className="mr-1" /> CRM
                </button>
              )}
            </nav>

            {/* Search Filters */}
            <SearchFilters
              onApplyFilters={handleApplyFilters}
              onClear={handleClearFilters}
            />

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

        {/* Mobile Menu Overlay */}
        {isMobileMenuOpen && (
          <nav className="md:hidden bg-blue-900 border-t border-blue-800 py-4 px-4 space-y-4 animate-in slide-in-from-top duration-200" aria-label="Mobile navigation">
            <button onClick={() => navigateTo('inventory')} className="flex items-center w-full py-2 hover:text-red-400 transition border-b border-blue-800">
              <Home size={18} className="mr-3" /> Browse Inventory
            </button>
            <button onClick={() => navigateTo('chat')} className="flex items-center w-full py-2 text-red-400 border-b border-blue-800">
              <Send size={18} className="mr-3" /> Chat with Tex
            </button>
            <button onClick={() => navigateTo('documents')} className="flex items-center w-full py-2 hover:text-red-400 transition border-b border-blue-800">
              <FileText size={18} className="mr-3" /> Documents
            </button>
            <button onClick={() => navigateTo('adstudio')} className="flex items-center w-full py-2 hover:text-red-400 transition border-b border-blue-800">
              <Video size={18} className="mr-3" /> Ad Studio
            </button>
            <button onClick={() => navigateTo('contact')} className="flex items-center w-full py-2 hover:text-red-400 transition border-b border-blue-800">
              <Phone size={18} className="mr-3" /> Contact
            </button>
            <button onClick={() => navigateTo('appointments')} className="flex items-center w-full py-2 hover:text-red-400 transition border-b border-blue-800">
              <CalendarDays size={18} className="mr-3" /> Book Visit
            </button>
            {adminAuthed && (
              <button onClick={() => navigateTo('crm')} className="flex items-center w-full py-2 hover:text-red-400 transition">
                <Users size={18} className="mr-3" /> CRM Dashboard
              </button>
            )}
          </nav>
        )}
      </header>

      {/* Main Chat Area */}
      <main className="flex-1 overflow-hidden flex flex-col max-w-4xl mx-auto w-full bg-white shadow-xl md:my-4 md:rounded-lg relative">

        {/* Messages List */}
        <div className="flex-1 overflow-y-auto p-4 space-y-6 scrollbar-thin scrollbar-thumb-gray-300 pb-20">
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
                  <div className={`p-4 rounded-2xl shadow-sm text-sm leading-relaxed
                     ${msg.role === 'user'
                      ? 'bg-blue-600 text-white rounded-tr-none'
                      : 'bg-gray-100 text-gray-800 rounded-tl-none border border-gray-200'
                    }`}>
                    <SafeMarkdown
                      content={msg.text}
                      comparisonList={comparisonList}
                      onToggleCompare={handleToggleCompare}
                    />
                  </div>
                  {/* Quick Action Buttons - show after initial greeting */}
                  {msg.showQuickActions && !isLoading && messages.length === 1 && (
                    <QuickActions
                      onActionClick={(message) => {
                        setInput(message);
                        // Auto-submit the message
                        setMessages(prev => [...prev, { role: 'user', text: message }]);
                        setInput('');
                        // Send to API
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
              <div className="flex flex-row items-center ml-12 space-x-2 bg-gray-50 p-3 rounded-xl" role="status" aria-label="Loading response">
                <Loader2 className="h-4 w-4 animate-spin text-gray-400" />
                <span className="text-xs text-gray-400 font-medium">Thinking...</span>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <div className="border-t border-gray-200 p-4 bg-white z-20">
          <form onSubmit={handleSubmit} className="relative flex items-center">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about homes or pricing..."
              className="w-full pl-4 pr-12 py-3 border border-gray-300 rounded-full focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition shadow-sm"
              disabled={isLoading}
              aria-label="Chat message"
            />
            <button
              type="submit"
              disabled={!input.trim() || isLoading}
              className="absolute right-2 p-2 bg-blue-600 text-white rounded-full hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              aria-label="Send message"
            >
              <Send size={18} />
            </button>
          </form>
          <div className="mt-2 text-center text-xs text-gray-400">
            AI can make mistakes. Please verify pricing and details with a human agent.
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

      {/* Footer Info */}
      <footer className="bg-white border-t border-gray-200 py-4 text-center text-xs text-gray-500 hidden md:block">
        <div className="flex justify-center space-x-6">
          <span className="flex items-center"><MapPin size={12} className="mr-1" aria-hidden="true" /> {BUSINESS_ADDRESS} East, {BUSINESS_CITY}</span>
          <span className="flex items-center"><Phone size={12} className="mr-1" aria-hidden="true" /> {BUSINESS_PHONE}</span>
          <span>Mon-Fri 9-6, Sat 9-5</span>
          <button onClick={handleAdminAccess} className="flex items-center hover:text-blue-600 transition-colors">
            {adminAuthed ? <ShieldCheck size={12} className="mr-1" /> : <Lock size={12} className="mr-1" />}
            Admin
          </button>
          <button onClick={() => navigateTo('documents')} className="hover:text-blue-600 transition-colors">Documents</button>
          <button onClick={() => navigateTo('adstudio')} className="hover:text-blue-600 transition-colors">Ad Studio</button>
          <button onClick={() => navigateTo('contact')} className="hover:text-blue-600 transition-colors">Contact</button>
          <button onClick={() => navigateTo('appointments')} className="hover:text-blue-600 transition-colors">Book Visit</button>
          {adminAuthed && <button onClick={() => navigateTo('crm')} className="hover:text-blue-600 transition-colors">CRM</button>}
        </div>
      </footer>
    </div>
  );
}

export default App;
