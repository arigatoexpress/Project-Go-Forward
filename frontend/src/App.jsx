import React, { useState, useEffect, useRef } from 'react';
import { Send, Home, Menu, X, Phone, MapPin, Loader2, User, Bot } from 'lucide-react';
import SafeMarkdown from './components/SafeMarkdown';
import SearchFilters from './components/SearchFilters';
import QuickActions from './components/QuickActions';
import ComparisonDrawer from './components/ComparisonDrawer';
import { v4 as uuidv4 } from 'uuid';

const API_URL = '/run'; // Relative path for single-container deployment

import Analytics from './pages/Analytics';

function App() {
  const [messages, setMessages] = useState([
    {
      role: 'model',
      text: "Howdy! 🤠 Welcome to {{BUSINESS_NAME}}. I'm Tex, your virtual housing consultant. How can I help you today?",
      showQuickActions: true
    }
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId] = useState(() => localStorage.getItem('tho_session_id') || uuidv4());
  const [activeFilters, setActiveFilters] = useState({});
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [comparisonList, setComparisonList] = useState([]);
  const [showAnalytics, setShowAnalytics] = useState(false);
  const messagesEndRef = useRef(null);

  useEffect(() => {
    // Check for admin query param
    const params = new URLSearchParams(window.location.search);
    if (params.get('admin') === 'true') {
      setShowAnalytics(true);
    }

    localStorage.setItem('tho_session_id', sessionId);
    // Ensure session exists on backend (optional strictly speaking as /run might create it, but good practice)
    createSession();
  }, [sessionId]);

  const createSession = async () => {
    try {
      await fetch(`https://{{BUSINESS_SHORT}}-ai-agent-suy7mgxwyq-uc.a.run.app/apps/root_agent/users/web_user_${sessionId}/sessions/${sessionId}`, {
        method: 'POST'
      });
    } catch (e) {
      console.error("Session creation warning:", e);
    }
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage = { role: 'user', text: input };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);

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
            parts: [{ text: userMessage.text }]
          }
        })
      });

      if (!response.ok) throw new Error('Network response was not ok');

      const data = await response.json();

      // Extract model response (adjust based on actual ADK response structure)
      // Assuming straightforward text response for now, but might need parsing if it returns complex objects

      let botText = "I apologize, I didn't catch that. Could you rephrase?";
      if (data && data.content) {
        // If ADK returns content directly
        botText = typeof data.content === 'string' ? data.content : JSON.stringify(data.content);
      } else if (data && data.candidates && data.candidates[0] && data.candidates[0].content && data.candidates[0].content.parts) {
        // Standard Gemini response structure
        botText = data.candidates[0].content.parts.map(p => p.text).join(' ');
      }

      if (data.error) {
        botText = `System Error: ${data.error}`;
      } else if (data.text) {
        botText = data.text;
      }

      const botMessage = { role: 'model', text: botText };
      setMessages(prev => [...prev, botMessage]);

    } catch (error) {
      console.error('Error sending message:', error);
      setMessages(prev => [...prev, { role: 'model', text: "I'm having trouble connecting to the home base right now. Please try again or call us at {{BUSINESS_PHONE}}." }]);
    } finally {
      setIsLoading(false);
    }
  };

  // Handle quick action button clicks
  const handleQuickAction = async (message) => {
    setIsLoading(true);

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
            parts: [{ text: message }]
          }
        })
      });

      if (!response.ok) throw new Error('Network response was not ok');

      const data = await response.json();

      let botText = "I apologize, I didn't catch that. Could you rephrase?";
      if (data.error) {
        botText = `System Error: ${data.error}`;
      } else if (data.text) {
        botText = data.text;
      }

      const botMessage = { role: 'model', text: botText };
      setMessages(prev => [...prev, botMessage]);

    } catch (error) {
      console.error('Error sending quick action:', error);
      setMessages(prev => [...prev, { role: 'model', text: "I'm having trouble connecting right now. Please try again or call us at {{BUSINESS_PHONE}}." }]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleApplyFilters = (filters) => {
    setActiveFilters(filters);

    // Build filter message
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
        if (prev.length >= 3) {
          // Could act as a notification here
          return prev;
        }
        return [...prev, property];
      }
    });
  };

  const handleRemoveFromCompare = (id) => {
    setComparisonList(prev => prev.filter(p => p.id !== id));
  };

  if (showAnalytics) {
    return (
      <div>
        <button
          onClick={() => setShowAnalytics(false)}
          className="fixed bottom-4 right-4 z-50 bg-gray-800 text-white px-4 py-2 rounded-full shadow-lg"
        >
          Exit Analytics
        </button>
        <Analytics />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50 font-sans text-gray-900">
      {/* Header */}
      <header className="bg-blue-900 text-white shadow-md z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <Home className="h-8 w-8 text-red-500" />
            <h1 className="text-xl font-bold tracking-tight">{{BUSINESS_NAME}}</h1>
          </div>

          <div className="flex items-center gap-4">
            <div className="hidden md:flex space-x-6 text-sm font-medium">
              <a href="#" className="hover:text-red-400 transition">Inventory</a>
              <a href="#" className="hover:text-red-400 transition">Financing</a>
              <a href="#" className="hover:text-red-400 transition">Contact</a>
            </div>

            {/* Search Filters */}
            <SearchFilters
              onApplyFilters={handleApplyFilters}
              onClear={handleClearFilters}
            />

            {/* Mobile menu button */}
            <button className="md:hidden" onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}>
              {isMobileMenuOpen ? <X /> : <Menu />}
            </button>
          </div>
        </div>
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
                    <img src="/tex-icon.svg" alt="Tex" className="h-8 w-8" />
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
                        const fakeEvent = { preventDefault: () => { } };
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
              <div className="flex flex-row items-center ml-12 space-x-2 bg-gray-50 p-3 rounded-xl">
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
              placeholder="Ask about homes, pricing, or financing..."
              className="w-full pl-4 pr-12 py-3 border border-gray-300 rounded-full focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition shadow-sm"
              disabled={isLoading}
            />
            <button
              type="submit"
              disabled={!input.trim() || isLoading}
              className="absolute right-2 p-2 bg-blue-600 text-white rounded-full hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
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
          <span className="flex items-center"><MapPin size={12} className="mr-1" /> {{BUSINESS_ADDRESS}} East, {{BUSINESS_CITY}}</span>
          <span className="flex items-center"><Phone size={12} className="mr-1" /> {{BUSINESS_PHONE}}</span>
          <span>Mon-Fri 9-6, Sat 9-5</span>
          <button onClick={() => setShowAnalytics(true)} className="hover:text-blue-600 transition-colors">Admin</button>
        </div>
      </footer>
    </div>
  );
}

export default App;
