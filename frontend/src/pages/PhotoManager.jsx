import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  ArrowLeft, Camera, UploadCloud, Trash2, CheckCircle, AlertCircle,
  Loader2, Search, Home as HomeIcon, ImageOff,
} from 'lucide-react';
import adminFetch from '../adminFetch';

// Staff photo uploader. Deliberately simple: pick a home, drop in photos, done.
// Photos are stored server-side and appear on the public Inventory page.

const ACCEPT = 'image/jpeg,image/png,image/webp,image/gif';

function homeLabel(home) {
  const name = home.model_name || home.name || 'Home';
  const status = home.status ? ` — ${home.status}` : '';
  return `${name}${status}`;
}

export default function PhotoManager({ onBack }) {
  const [homes, setHomes] = useState([]);
  const [loadingHomes, setLoadingHomes] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState('');

  const [photos, setPhotos] = useState([]);
  const [loadingPhotos, setLoadingPhotos] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState(null); // { type: 'ok'|'error', text }
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef(null);

  const selectedHome = homes.find(h => String(h.id) === String(selectedId));

  // --- Load the list of homes (reuses the public inventory endpoint) ---
  const fetchHomes = useCallback(() => {
    setLoadingHomes(true);
    setLoadError('');
    fetch('/api/marketing/inventory-context')
      .then(r => r.json())
      .then(data => {
        const list = Array.isArray(data?.homes) ? data.homes : [];
        // Only homes with a usable id can receive uploads.
        setHomes(list.filter(h => h && (h.id !== undefined && h.id !== null && `${h.id}` !== '')));
      })
      .catch(() => setLoadError('Could not load the list of homes. Please refresh.'))
      .finally(() => setLoadingHomes(false));
  }, []);

  useEffect(() => { fetchHomes(); }, [fetchHomes]);

  // --- Load existing staff photos for the selected home ---
  const fetchPhotos = useCallback((homeId) => {
    if (!homeId) { setPhotos([]); return; }
    setLoadingPhotos(true);
    adminFetch(`/api/inventory/${encodeURIComponent(homeId)}/photos`)
      .then(r => r.json())
      .then(data => setPhotos(Array.isArray(data?.photos) ? data.photos : []))
      .catch(() => setPhotos([]))
      .finally(() => setLoadingPhotos(false));
  }, []);

  useEffect(() => { fetchPhotos(selectedId); }, [selectedId, fetchPhotos]);

  // --- Upload ---
  const uploadFiles = useCallback(async (fileList) => {
    const files = Array.from(fileList || []).filter(f => f && f.type.startsWith('image/'));
    if (!selectedId) {
      setMessage({ type: 'error', text: 'Pick a home first, then add photos.' });
      return;
    }
    if (files.length === 0) {
      setMessage({ type: 'error', text: 'Those files were not photos. Use JPG, PNG, WebP, or GIF.' });
      return;
    }
    setUploading(true);
    setMessage(null);
    try {
      const form = new FormData();
      files.forEach(f => form.append('files', f));
      // NOTE: do not set Content-Type — the browser adds the multipart boundary.
      const res = await adminFetch(`/api/inventory/${encodeURIComponent(selectedId)}/photos`, {
        method: 'POST',
        body: form,
      });
      const data = await res.json();
      if (!res.ok) {
        setMessage({ type: 'error', text: data?.detail || 'Upload failed. Please try again.' });
      } else {
        const okCount = (data.uploaded || []).length;
        const errCount = (data.errors || []).length;
        if (okCount > 0 && errCount === 0) {
          setMessage({ type: 'ok', text: `${okCount} photo${okCount === 1 ? '' : 's'} added. They will show on the website shortly.` });
        } else if (okCount > 0) {
          setMessage({ type: 'ok', text: `${okCount} added, ${errCount} skipped (not a valid image).` });
        } else {
          setMessage({ type: 'error', text: 'No photos were added. Make sure they are JPG, PNG, WebP, or GIF under 15 MB.' });
        }
        fetchPhotos(selectedId);
      }
    } catch {
      setMessage({ type: 'error', text: 'Something went wrong uploading. Check your connection and try again.' });
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }, [selectedId, fetchPhotos]);

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer?.files?.length) uploadFiles(e.dataTransfer.files);
  };

  const handleDelete = async (filename) => {
    if (!window.confirm('Remove this photo from the website?')) return;
    try {
      const res = await adminFetch(
        `/api/inventory/${encodeURIComponent(selectedId)}/photos/${encodeURIComponent(filename)}`,
        { method: 'DELETE' },
      );
      if (res.ok) {
        setMessage({ type: 'ok', text: 'Photo removed.' });
        fetchPhotos(selectedId);
      } else {
        setMessage({ type: 'error', text: 'Could not remove that photo.' });
      }
    } catch {
      setMessage({ type: 'error', text: 'Could not remove that photo. Try again.' });
    }
  };

  const filteredHomes = homes.filter(h =>
    !query || homeLabel(h).toLowerCase().includes(query.toLowerCase())
  );

  return (
    <div className="min-h-screen bg-gray-50 font-sans">
      <header className="bg-slate-800 text-white px-6 py-4 flex items-center gap-3 sticky top-0 z-30">
        <button
          onClick={onBack}
          className="bg-white/10 hover:bg-white/20 text-white p-2 rounded-lg flex items-center"
          aria-label="Go back"
        >
          <ArrowLeft size={20} />
        </button>
        <Camera size={22} />
        <h1 className="text-xl font-bold m-0">Add Photos to Homes</h1>
      </header>

      <div className="max-w-3xl mx-auto px-4 py-6">
        {/* Plain-language intro */}
        <p className="text-gray-600 text-[15px] mb-5">
          Add pictures to a home in 3 easy steps. The photos you add here show up
          on the public website automatically.
        </p>

        {/* Step 1 — pick a home */}
        <section className="bg-white rounded-2xl shadow-sm border border-gray-200 p-5 mb-5">
          <h2 className="text-base font-bold text-slate-800 mb-1 flex items-center gap-2">
            <span className="bg-slate-800 text-white rounded-full w-6 h-6 inline-flex items-center justify-center text-sm">1</span>
            Pick the home
          </h2>
          <p className="text-sm text-gray-500 mb-3 ml-8">Type part of the name to find it.</p>

          {loadError && (
            <div className="flex items-center gap-2 text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm mb-3">
              <AlertCircle size={16} /> {loadError}
            </div>
          )}

          <div className="flex items-center gap-2 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 mb-3">
            <Search size={18} className="text-gray-400" />
            <input
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search homes…"
              className="bg-transparent outline-none w-full text-[15px]"
            />
          </div>

          {loadingHomes ? (
            <div className="flex items-center gap-2 text-gray-500 text-sm py-3">
              <Loader2 className="animate-spin" size={18} /> Loading homes…
            </div>
          ) : (
            <select
              value={selectedId}
              onChange={e => { setSelectedId(e.target.value); setMessage(null); }}
              className="w-full border border-gray-300 rounded-lg px-3 py-3 text-[15px] bg-white"
              size={Math.min(8, Math.max(3, filteredHomes.length))}
            >
              <option value="">— Choose a home —</option>
              {filteredHomes.map(h => (
                <option key={h.id} value={h.id}>{homeLabel(h)}</option>
              ))}
            </select>
          )}
        </section>

        {/* Step 2 — add photos */}
        <section className={`bg-white rounded-2xl shadow-sm border border-gray-200 p-5 mb-5 ${!selectedId ? 'opacity-50 pointer-events-none' : ''}`}>
          <h2 className="text-base font-bold text-slate-800 mb-1 flex items-center gap-2">
            <span className="bg-slate-800 text-white rounded-full w-6 h-6 inline-flex items-center justify-center text-sm">2</span>
            Add the photos
          </h2>
          <p className="text-sm text-gray-500 mb-3 ml-8">
            {selectedHome ? `For: ${homeLabel(selectedHome)}` : 'Pick a home above first.'}
          </p>

          <div
            onDragOver={e => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`cursor-pointer border-2 border-dashed rounded-xl p-8 text-center transition-colors ${
              dragOver ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-blue-400 hover:bg-gray-50'
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPT}
              multiple
              className="hidden"
              onChange={e => uploadFiles(e.target.files)}
            />
            {uploading ? (
              <div className="flex flex-col items-center gap-2 text-blue-700">
                <Loader2 className="animate-spin" size={36} />
                <span className="font-semibold">Uploading… please wait</span>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-2 text-gray-600">
                <UploadCloud size={40} className="text-blue-500" />
                <span className="font-semibold text-slate-800 text-[15px]">
                  Tap here to choose photos
                </span>
                <span className="text-sm text-gray-500">or drag pictures into this box</span>
                <span className="text-xs text-gray-400 mt-1">JPG, PNG, WebP or GIF · up to 15 MB each</span>
              </div>
            )}
          </div>
        </section>

        {/* Status message */}
        {message && (
          <div className={`flex items-center gap-2 rounded-lg px-4 py-3 text-sm mb-5 ${
            message.type === 'ok'
              ? 'bg-green-50 border border-green-200 text-green-800'
              : 'bg-red-50 border border-red-200 text-red-800'
          }`}>
            {message.type === 'ok' ? <CheckCircle size={18} /> : <AlertCircle size={18} />}
            {message.text}
          </div>
        )}

        {/* Step 3 — current photos */}
        {selectedId && (
          <section className="bg-white rounded-2xl shadow-sm border border-gray-200 p-5">
            <h2 className="text-base font-bold text-slate-800 mb-3 flex items-center gap-2">
              <span className="bg-slate-800 text-white rounded-full w-6 h-6 inline-flex items-center justify-center text-sm">3</span>
              Photos on this home
            </h2>

            {loadingPhotos ? (
              <div className="flex items-center gap-2 text-gray-500 text-sm py-3">
                <Loader2 className="animate-spin" size={18} /> Loading…
              </div>
            ) : photos.length === 0 ? (
              <div className="flex flex-col items-center gap-2 text-gray-400 py-6">
                <ImageOff size={32} />
                <span className="text-sm">No photos added yet. Add some above.</span>
              </div>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                {photos.map(p => (
                  <div key={p.filename} className="relative group rounded-lg overflow-hidden border border-gray-200 bg-gray-100">
                    <img
                      src={p.url}
                      alt="Home"
                      loading="lazy"
                      className="w-full h-32 object-cover"
                    />
                    <button
                      onClick={() => handleDelete(p.filename)}
                      className="absolute top-1.5 right-1.5 bg-white/90 hover:bg-red-600 hover:text-white text-red-600 rounded-full p-1.5 shadow"
                      aria-label="Remove photo"
                      title="Remove photo"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
