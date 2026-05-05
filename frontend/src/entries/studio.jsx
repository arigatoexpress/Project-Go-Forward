/**
 * Ad Studio — Standalone entry point.
 * Builds as a separate HTML page (studio.html) for independent deployment.
 * Shares components with the main app but has its own routing.
 */

import React, { useState, Suspense, lazy, useEffect } from 'react';
import ReactDOM from 'react-dom/client';
import { Video, Loader2, Moon, Sun, ArrowLeft } from 'lucide-react';
import { useDarkMode } from '../hooks/useDarkMode';
import '../index.css';
import {
  BUSINESS_NAME
} from '../constants';

const AdStudio = lazy(() => import('../pages/AdStudio'));

function StudioApp() {
  const [darkMode, setDarkMode] = useDarkMode();
  const [adminAuthed, setAdminAuthed] = useState(false);
  const [showPin, setShowPin] = useState(true);
  const [pin, setPin] = useState('');
  const [pinError, setPinError] = useState('');

  const verifyPin = async (e) => {
    e.preventDefault();
    try {
      const res = await fetch('/api/admin/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.success) {
          setAdminAuthed(true);
          setShowPin(false);
        }
      } else {
        setPinError('Invalid PIN');
      }
    } catch {
      setPinError('Connection error');
    }
  };

  // Check existing session cookie
  useEffect(() => {
    fetch('/api/admin/check')
      .then(r => r.json())
      .then(d => { if (d.valid) { setAdminAuthed(true); setShowPin(false); } })
      .catch(() => {});
  }, []);

  if (showPin && !adminAuthed) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <form onSubmit={verifyPin} className="bg-white dark:bg-gray-800 p-8 rounded-2xl shadow-xl w-80">
          <div className="flex items-center gap-3 mb-6">
            <Video className="h-8 w-8 text-blue-600" />
            <h1 className="text-xl font-bold dark:text-white">Ad Studio</h1>
          </div>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">Enter admin PIN to access</p>
          <input
            type="password"
            value={pin}
            onChange={e => { setPin(e.target.value); setPinError(''); }}
            placeholder="Admin PIN"
            className="w-full px-4 py-3 border rounded-lg mb-3 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
            autoFocus
          />
          {pinError && <p className="text-red-500 text-sm mb-3">{pinError}</p>}
          <button type="submit" className="w-full py-3 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700">
            Unlock
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <header className="bg-blue-900 text-white shadow-md sticky top-0 z-30">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Video className="h-5 w-5" />
            <span className="font-bold text-lg">{BUSINESS_NAME} — Ad Studio</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setDarkMode(prev => !prev)}
              className="p-2 text-blue-300 hover:text-white hover:bg-white/10 rounded-md transition-colors"
            >
              {darkMode ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <a href="/" className="text-sm text-blue-200 hover:text-white flex items-center gap-1">
              <ArrowLeft size={14} /> Main App
            </a>
          </div>
        </div>
      </header>
      <Suspense fallback={
        <div className="flex items-center justify-center min-h-[50vh]">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      }>
        <AdStudio standalone={true} />
      </Suspense>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <StudioApp />
  </React.StrictMode>
);
