/**
 * Chat History Admin Page
 * 
 * View and search through customer chat conversations
 */

import React, { useState, useEffect } from 'react';
import {
  MessageCircle, Search, Clock, User, Filter,
  ChevronRight, ChevronDown, ExternalLink, Loader2,
  AlertCircle, CheckCircle, X
} from 'lucide-react';
import adminFetch from '../adminFetch';

function ChatSessionCard({ session, onSelect, isSelected }) {
  const formatTime = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString('en-US', { 
      month: 'short', 
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit'
    });
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'converted': return 'bg-green-100 text-green-700';
      case 'closed': return 'bg-gray-100 text-gray-700';
      case 'active': return 'bg-blue-100 text-blue-700';
      default: return 'bg-gray-100 text-gray-700';
    }
  };

  return (
    <button
      onClick={() => onSelect(session)}
      className={`w-full text-left p-4 rounded-xl border-2 transition-all ${
        isSelected 
          ? 'border-blue-500 bg-blue-50' 
          : 'border-gray-200 hover:border-gray-300 bg-white'
      }`}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${
            isSelected ? 'bg-blue-200' : 'bg-gray-100'
          }`}>
            <MessageCircle size={20} className={isSelected ? 'text-blue-600' : 'text-gray-500'} />
          </div>
          <div>
            <div className="font-semibold text-gray-800">
              Session #{session.session_id.slice(-8)}
            </div>
            <div className="text-sm text-gray-500 flex items-center gap-2">
              <User size={14} />
              {session.user_id}
            </div>
          </div>
        </div>
        <span className={`px-2 py-1 rounded-full text-xs font-semibold ${getStatusColor(session.status)}`}>
          {session.status}
        </span>
      </div>
      
      <div className="mt-3 pt-3 border-t flex items-center justify-between text-sm">
        <div className="text-gray-500">
          {session.message_count} messages
        </div>
        <div className="text-gray-400 flex items-center gap-1">
          <Clock size={14} />
          {formatTime(session.updated_at)}
        </div>
      </div>
    </button>
  );
}

function MessageBubble({ message }) {
  const isUser = message.role === 'user';
  
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[80%] rounded-2xl px-4 py-3 ${
        isUser 
          ? 'bg-blue-600 text-white rounded-br-none' 
          : 'bg-gray-100 text-gray-800 rounded-bl-none'
      }`}>
        <p className="text-sm whitespace-pre-wrap">{message.text}</p>
        <div className={`text-xs mt-1 ${isUser ? 'text-blue-200' : 'text-gray-400'}`}>
          {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </div>
      </div>
    </div>
  );
}

export default function ChatHistory() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedSession, setSelectedSession] = useState(null);
  const [sessionDetails, setSessionDetails] = useState(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [timeRange, setTimeRange] = useState(24);

  // Load sessions
  useEffect(() => {
    loadSessions();
  }, [timeRange]);

  const loadSessions = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await adminFetch(`/api/chat/sessions?hours=${timeRange}&limit=50`);
      const data = await res.json();
      if (data.success) {
        setSessions(data.sessions || []);
      } else {
        setError(data.error || 'Failed to load sessions');
      }
    } catch (err) {
      setError('Network error. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  // Load session details
  const loadSessionDetails = async (sessionId) => {
    setDetailsLoading(true);
    try {
      const res = await adminFetch(`/api/chat/history/${sessionId}`);
      const data = await res.json();
      if (data.success) {
        setSessionDetails(data.session);
      }
    } catch (err) {
      console.error('Failed to load session details:', err);
    } finally {
      setDetailsLoading(false);
    }
  };

  // Handle session selection
  const handleSelectSession = (session) => {
    setSelectedSession(session);
    loadSessionDetails(session.session_id);
  };

  // Search conversations
  const handleSearch = async (e) => {
    e.preventDefault();
    if (!searchQuery.trim()) {
      setSearchResults(null);
      return;
    }
    
    setSearching(true);
    try {
      const res = await adminFetch('/api/chat/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: searchQuery })
      });
      const data = await res.json();
      if (data.success) {
        setSearchResults(data.results);
      }
    } catch (err) {
      console.error('Search failed:', err);
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-blue-600 rounded-xl flex items-center justify-center">
                <MessageCircle size={24} className="text-white" />
              </div>
              <div>
                <h1 className="text-xl font-bold text-gray-900">Chat History</h1>
                <p className="text-sm text-gray-500">View and search customer conversations</p>
              </div>
            </div>
            
            <div className="flex items-center gap-3">
              <select
                value={timeRange}
                onChange={(e) => setTimeRange(parseInt(e.target.value))}
                className="px-3 py-2 border rounded-lg text-sm"
              >
                <option value={24}>Last 24 hours</option>
                <option value={72}>Last 3 days</option>
                <option value={168}>Last 7 days</option>
                <option value={720}>Last 30 days</option>
              </select>
              <button
                onClick={loadSessions}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
              >
                Refresh
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Search */}
        <form onSubmit={handleSearch} className="mb-6">
          <div className="relative max-w-xl">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400" size={20} />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search conversations..."
              className="w-full pl-12 pr-4 py-3 border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => { setSearchQuery(''); setSearchResults(null); }}
                className="absolute right-3 top-1/2 -translate-y-1/2 p-1 hover:bg-gray-100 rounded"
              >
                <X size={16} className="text-gray-400" />
              </button>
            )}
          </div>
        </form>

        {searching && (
          <div className="flex items-center gap-2 text-gray-500 mb-4">
            <Loader2 size={18} className="animate-spin" />
            Searching...
          </div>
        )}

        {searchResults && (
          <div className="mb-6 bg-blue-50 border border-blue-200 rounded-xl p-4">
            <div className="flex items-center justify-between">
              <span className="text-blue-800 font-medium">
                Found {searchResults.length} results
              </span>
              <button
                onClick={() => { setSearchResults(null); setSearchQuery(''); }}
                className="text-blue-600 hover:text-blue-800"
              >
                Clear
              </button>
            </div>
          </div>
        )}

        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-xl flex items-center gap-3 text-red-700">
            <AlertCircle size={20} />
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Session List */}
          <div className="lg:col-span-1 space-y-3">
            <h2 className="font-semibold text-gray-700 mb-3">
              Conversations ({searchResults?.length || sessions.length})
            </h2>
            
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={32} className="animate-spin text-blue-600" />
              </div>
            ) : (
              <div className="space-y-3 max-h-[calc(100vh-300px)] overflow-y-auto">
                {(searchResults || sessions).map((session) => (
                  <ChatSessionCard
                    key={session.session_id}
                    session={session}
                    onSelect={handleSelectSession}
                    isSelected={selectedSession?.session_id === session.session_id}
                  />
                ))}
                
                {(searchResults || sessions).length === 0 && (
                  <div className="text-center py-12 text-gray-400">
                    <MessageCircle size={48} className="mx-auto mb-3 opacity-30" />
                    <p>No conversations found</p>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Conversation Detail */}
          <div className="lg:col-span-2">
            {selectedSession ? (
              <div className="bg-white rounded-2xl border shadow-sm h-[calc(100vh-280px)] flex flex-col">
                {/* Detail Header */}
                <div className="p-4 border-b flex items-center justify-between">
                  <div>
                    <h3 className="font-semibold text-gray-800">
                      Session #{selectedSession.session_id.slice(-8)}
                    </h3>
                    <p className="text-sm text-gray-500">
                      {sessionDetails?.message_count || selectedSession.message_count} messages
                    </p>
                  </div>
                  {selectedSession.lead_id && (
                    <a
                      href={`/crm?lead=${selectedSession.lead_id}`}
                      className="flex items-center gap-2 px-3 py-1.5 bg-green-100 text-green-700 rounded-lg text-sm font-medium hover:bg-green-200"
                    >
                      <CheckCircle size={16} />
                      View Lead
                      <ExternalLink size={14} />
                    </a>
                  )}
                </div>

                {/* Messages */}
                <div className="flex-1 overflow-y-auto p-4 space-y-4">
                  {detailsLoading ? (
                    <div className="flex items-center justify-center h-full">
                      <Loader2 size={32} className="animate-spin text-blue-600" />
                    </div>
                  ) : sessionDetails?.messages ? (
                    sessionDetails.messages.map((msg, idx) => (
                      <MessageBubble key={idx} message={msg} />
                    ))
                  ) : (
                    <div className="text-center text-gray-400 py-12">
                      Select a conversation to view messages
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="bg-gray-50 rounded-2xl border-2 border-dashed border-gray-200 h-[calc(100vh-280px)] flex items-center justify-center">
                <div className="text-center text-gray-400">
                  <MessageCircle size={64} className="mx-auto mb-4 opacity-30" />
                  <p className="text-lg">Select a conversation to view details</p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
