import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  ArrowLeft, Camera, UploadCloud, Trash2, CheckCircle, AlertCircle,
  Loader2, Search, ImageOff, Star, ChevronLeft, ChevronRight, Box,
} from 'lucide-react';
import adminFetch from '../adminFetch';
import ConfirmDialog from '../components/ConfirmDialog';
import { isPreOwned } from '../utils/inventoryDisplay';
import { canonicalMatterportId, canonicalMatterportUrl } from '../utils/matterportLink';
import {
  PHOTO_GAP_HINTS,
  checkCustomerPhotos,
  mergeStaffAndPublicHomes,
  blockedPhotoMessage,
  isUploadablePhotoFile,
  photoGapReason,
  photoHomeListMessage,
  photoUrlsToCheck,
  staffPhotoHomeDetails,
  staffPhotoHomeLabel,
  staffPhotoHomeName,
  visiblePhotoHomes,
} from '../utils/photoGap';

// Staff photo uploader. The first screen is the homes that still need a
// picture. Pick one and the phone upload is the next thing on the page.

const ACCEPT = 'image/jpeg,image/png,image/webp,image/gif';
const LOAD_ERROR = 'Could not load the list of homes. Please refresh.';

function readJson(response) {
  if (!response || typeof response.json !== 'function') return Promise.resolve(null);
  return response.json().catch(() => null);
}

function StaffPhotoPreview({ url, alt }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="w-full h-32 flex flex-col items-center justify-center gap-1 text-gray-400 bg-gray-100">
        <ImageOff size={22} />
        <span className="text-xs">Photo unavailable</span>
      </div>
    );
  }
  return (
    <img
      src={url}
      alt={alt}
      loading="lazy"
      className="w-full h-32 object-cover"
      onError={() => setFailed(true)}
    />
  );
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
  const [onlyNeedsPhotos, setOnlyNeedsPhotos] = useState(true);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [failedUrls, setFailedUrls] = useState(() => new Set());
  const [checkingPhotos, setCheckingPhotos] = useState(false);
  const [tourInput, setTourInput] = useState('');
  const [staffTourId, setStaffTourId] = useState('');
  const [loadingTour, setLoadingTour] = useState(false);
  const [savingTour, setSavingTour] = useState(false);
  const [removingTour, setRemovingTour] = useState(false);
  const [confirmRemoveTour, setConfirmRemoveTour] = useState(false);
  const [tourMessage, setTourMessage] = useState(null);
  const [tourLoadFailed, setTourLoadFailed] = useState(false);
  const fileInputRef = useRef(null);
  const cameraInputRef = useRef(null);
  const uploadSectionRef = useRef(null);
  const messageRef = useRef(null);
  const failedRef = useRef(new Set());
  const okRef = useRef(new Set());

  const selectedHome = homes.find(h => String(h.id) === String(selectedId));

  const fetchHomes = useCallback((silent = false) => {
    if (!silent) {
      setLoadingHomes(true);
      setLoadError('');
    }
    Promise.all([
      adminFetch('/api/marketing/inventory-context').then(readJson).catch(() => null),
      adminFetch('/api/inventory?status=&limit=500&include_staff_photos=true').then(readJson).catch(() => null),
    ])
      .then(([publicData, staffData]) => {
        const publicHomes = Array.isArray(publicData?.homes) ? publicData.homes : [];
        const staffFailed = !staffData || staffData.success === false;
        const staffHomes = Array.isArray(staffData?.inventory) ? staffData.inventory : [];
        const failed = publicHomes.length === 0 && (staffFailed || !Array.isArray(staffData?.inventory));
        if (failed) {
          if (!silent) {
            setLoadError(LOAD_ERROR);
            setHomes([]);
          }
          return;
        }
        setLoadError('');
        setHomes(mergeStaffAndPublicHomes(publicHomes, staffHomes));
      })
      .catch(() => {
        if (!silent) {
          setLoadError(LOAD_ERROR);
          setHomes([]);
        }
      })
      .finally(() => {
        if (!silent) setLoadingHomes(false);
      });
  }, []);

  useEffect(() => { fetchHomes(); }, [fetchHomes]);

  useEffect(() => {
    const needsCheck = homes.some((home) => (
      photoUrlsToCheck(home, failedRef.current, okRef.current, new Set()).length > 0
    ));
    if (!needsCheck) return undefined;
    const controller = new AbortController();
    let cancelled = false;
    setCheckingPhotos(true);
    checkCustomerPhotos(homes, {
      failedUrls: failedRef.current,
      okUrls: okRef.current,
      signal: controller.signal,
      isCancelled: () => cancelled,
      onFailed: () => {
        if (!cancelled) setFailedUrls(new Set(failedRef.current));
      },
    }).finally(() => {
      if (!cancelled) setCheckingPhotos(false);
    });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [homes]);

  const fetchPhotos = useCallback((homeId) => {
    if (!homeId) { setPhotos([]); return Promise.resolve([]); }
    setLoadingPhotos(true);
    return adminFetch(`/api/inventory/${encodeURIComponent(homeId)}/photos`)
      .then(r => r.json())
      .then(data => {
        const next = Array.isArray(data?.photos) ? data.photos : [];
        setPhotos(next);
        return next;
      })
      .catch(() => {
        setPhotos([]);
        return null;
      })
      .finally(() => setLoadingPhotos(false));
  }, []);

  useEffect(() => { fetchPhotos(selectedId); }, [selectedId, fetchPhotos]);

  useEffect(() => {
    let active = true;
    if (!selectedId) {
      setStaffTourId('');
      setTourInput('');
      setLoadingTour(false);
      return undefined;
    }
    setLoadingTour(true);
    setTourLoadFailed(false);
    adminFetch(`/api/inventory/${encodeURIComponent(selectedId)}/tour`)
      .then(async (response) => {
        if (!active) return;
        if (!response.ok) {
          setStaffTourId('');
          setTourInput('');
          setTourLoadFailed(true);
          setTourMessage({
            type: 'error',
            text: response.status === 401
              ? 'Please sign in again to see the 3D tour.'
              : 'Could not load the 3D tour. Refresh and try again.',
          });
          return;
        }
        const data = await readJson(response);
        if (!active) return;
        const id = canonicalMatterportId(data?.matterport_id);
        setStaffTourId(id);
        setTourInput(id ? canonicalMatterportUrl(id) : '');
        setTourLoadFailed(false);
      })
      .catch(() => {
        if (!active) return;
        setStaffTourId('');
        setTourInput('');
        setTourLoadFailed(true);
        setTourMessage({
          type: 'error',
          text: 'Could not load the 3D tour. Refresh and try again.',
        });
      })
      .finally(() => {
        if (active) setLoadingTour(false);
      });
    return () => { active = false; };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    const node = uploadSectionRef.current;
    if (node && typeof node.scrollIntoView === 'function') {
      node.scrollIntoView({ block: 'start' });
    }
  }, [selectedId]);

  useEffect(() => {
    if (!message) return;
    const node = messageRef.current;
    if (node && typeof node.scrollIntoView === 'function') {
      node.scrollIntoView({ block: 'nearest' });
    }
  }, [message]);

  const uploadFiles = useCallback(async (fileList) => {
    const picked = Array.from(fileList || []).filter(Boolean);
    const files = picked.filter(isUploadablePhotoFile);
    if (uploading) return;
    if (!selectedId) {
      setMessage({ type: 'error', text: 'Pick a home first, then add photos.' });
      return;
    }
    if (picked.length === 0) return;
    if (files.length === 0) {
      setMessage({ type: 'error', text: blockedPhotoMessage(picked) });
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
      const retryNames = Array.isArray(data?.retryable) ? data.retryable.filter(Boolean) : [];
      const retry = retryNames.length > 0 ? ` Retry: ${retryNames.join(', ')}.` : '';
      if (!res.ok) {
        setMessage({
          type: 'error',
          text: data?.storage_unavailable
            ? `The photo safe is busy, so nothing new was saved. Refresh this page, then try again.${retry}`
            : 'Those photos were not added. Use a JPG or PNG of the actual home and try again.',
        });
      } else {
        const okCount = (data.uploaded || []).length;
        const errCount = (data.errors || []).length;
        if (data.storage_unavailable && okCount > 0) {
          setMessage({
            type: 'error',
            text: `${okCount} photo${okCount === 1 ? ' was' : 's were'} saved, then the photo safe got busy. Refresh before trying the rest.${retry}`,
          });
        } else if (okCount > 0 && errCount === 0) {
          setMessage({ type: 'ok', text: `${okCount} photo${okCount === 1 ? '' : 's'} added. Customers will see ${okCount === 1 ? 'it' : 'them'} on the website shortly.` });
        } else if (okCount > 0) {
          setMessage({ type: 'ok', text: `${okCount} added, ${errCount} skipped (not a valid image).` });
        } else {
          setMessage({ type: 'error', text: 'No photos were added. Make sure they are JPG, PNG, WebP, or GIF under 15 MB.' });
        }
        if (okCount > 0) {
          setHomes((current) => current.map((home) => (
            String(home.id) === String(selectedId) ? { ...home, has_staff_photos: true } : home
          )));
        }
        fetchPhotos(selectedId);
      }
    } catch {
      setMessage({ type: 'error', text: 'Something went wrong uploading. Check your connection and try again.' });
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
      if (cameraInputRef.current) cameraInputRef.current.value = '';
    }
  }, [selectedId, fetchPhotos, uploading]);

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    if (selectedId && !uploading && e.dataTransfer?.files?.length) uploadFiles(e.dataTransfer.files);
  };

  const handleDelete = async () => {
    const filename = pendingDelete;
    if (!filename) return;
    setDeleting(true);
    try {
      const res = await adminFetch(
        `/api/inventory/${encodeURIComponent(selectedId)}/photos/${encodeURIComponent(filename)}`,
        { method: 'DELETE' },
      );
      if (res.ok) {
        setMessage({ type: 'ok', text: 'Photo removed. Customers will no longer see it.' });
        const remaining = await fetchPhotos(selectedId);
        if (Array.isArray(remaining) && remaining.length === 0) {
          setHomes((current) => current.map((home) => {
            if (String(home.id) !== String(selectedId)) return home;
            const real = (Array.isArray(home.real_photos) ? home.real_photos : [])
              .filter((url) => !/\/api\/inventory\/photos\//i.test(String(url)));
            const image = /\/api\/inventory\/photos\//i.test(String(home.image_url || '')) ? (real[0] || '') : home.image_url;
            return { ...home, has_staff_photos: false, real_photos: real, image_url: image };
          }));
        }
      } else {
        setMessage({ type: 'error', text: 'Could not remove that photo.' });
      }
    } catch {
      setMessage({ type: 'error', text: 'That photo is still on the website. Check the connection and try again.' });
    } finally {
      setDeleting(false);
      setPendingDelete(null);
    }
  };

  const applyOrder = async (order, okText) => {
    try {
      const res = await adminFetch(
        `/api/inventory/${encodeURIComponent(selectedId)}/photos/order`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ order }),
        },
      );
      const data = await res.json();
      if (res.ok) {
        setPhotos(Array.isArray(data?.photos) ? data.photos : photos);
        setMessage({ type: 'ok', text: okText });
      } else {
        setMessage({ type: 'error', text: 'Could not change the photo order.' });
      }
    } catch {
      setMessage({ type: 'error', text: 'Could not change the photo order. Try again.' });
    }
  };

  const handleMakeMain = (filename) => {
    const order = [filename, ...photos.map(p => p.filename).filter(f => f !== filename)];
    return applyOrder(order, 'Main photo updated. The website will show it shortly.');
  };

  const handleMove = (idx, delta) => {
    const target = idx + delta;
    if (target < 0 || target >= photos.length) return;
    const order = photos.map(p => p.filename);
    [order[idx], order[target]] = [order[target], order[idx]];
    return applyOrder(order, 'Photo order updated.');
  };

  const selectHome = (homeId) => {
    setSelectedId(String(homeId));
    setMessage(null);
    setTourMessage(null);
    setStaffTourId('');
    setTourInput('');
    setTourLoadFailed(false);
    setConfirmRemoveTour(false);
  };

  const saveTour = async () => {
    const value = tourInput.trim();
    if (!selectedId || savingTour || removingTour) return;
    if (!value) {
      setTourMessage({ type: 'error', text: 'Paste a Matterport link or the tour ID first.' });
      return;
    }
    setSavingTour(true);
    setTourMessage(null);
    try {
      const response = await adminFetch(`/api/inventory/${encodeURIComponent(selectedId)}/tour`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ matterport: value }),
      });
      const data = await readJson(response);
      if (response.status === 401) {
        setTourMessage({ type: 'error', text: 'Please sign in again, then save the 3D tour.' });
        return;
      }
      const id = canonicalMatterportId(data?.matterport_id);
      if (!response.ok || !id) {
        setTourMessage({
          type: 'error',
          text: response.status === 503
            ? 'The 3D tour could not be saved. Please try again.'
            : 'That is not a Matterport tour link. Paste the link from my.matterport.com, or the tour ID.',
        });
        return;
      }
      setStaffTourId(id);
      setTourInput(canonicalMatterportUrl(id));
      setTourMessage({
        type: 'ok',
        text: '3D tour saved. Customers will see it on the website shortly.',
      });
    } catch {
      setTourMessage({
        type: 'error',
        text: 'The 3D tour was not saved. Check your connection and try again.',
      });
    } finally {
      setSavingTour(false);
    }
  };

  const removeTour = async () => {
    if (!selectedId || removingTour) return;
    setRemovingTour(true);
    setTourMessage(null);
    try {
      const response = await adminFetch(`/api/inventory/${encodeURIComponent(selectedId)}/tour`, {
        method: 'DELETE',
      });
      if (response.status === 401) {
        setTourMessage({ type: 'error', text: 'Please sign in again, then remove the 3D tour.' });
        return;
      }
      if (!response.ok) {
        setTourMessage({
          type: 'error',
          text: 'The 3D tour link is still saved. Check your connection and try again.',
        });
        return;
      }
      setStaffTourId('');
      setTourInput('');
      setTourMessage({ type: 'ok', text: '3D tour link removed.' });
    } catch {
      setTourMessage({
        type: 'error',
        text: 'The 3D tour link is still saved. Check your connection and try again.',
      });
    } finally {
      setRemovingTour(false);
      setConfirmRemoveTour(false);
    }
  };

  const listedHomes = visiblePhotoHomes(homes, { includeInactive, failedUrls });
  const filteredHomes = visiblePhotoHomes(homes, {
    query,
    onlyNeedsPhotos,
    includeInactive,
    failedUrls,
  });
  const needsCount = listedHomes.filter((home) => photoGapReason(home, failedUrls)).length;
  const selectedGap = selectedHome ? photoGapReason(selectedHome, failedUrls) : null;
  const listMessage = photoHomeListMessage({
    listedCount: listedHomes.length,
    visibleCount: filteredHomes.length,
    onlyNeedsPhotos,
    checkingPhotos,
    query,
    loadError,
  });

  const uploadButtonClass = `flex flex-col items-center justify-center gap-1 min-h-24 rounded-xl border-2 border-dashed px-4 py-4 text-center cursor-pointer ${
    dragOver ? 'border-blue-500 bg-blue-50' : 'border-gray-300 bg-white hover:border-blue-400 hover:bg-gray-50'
  } ${uploading ? 'opacity-50 pointer-events-none' : ''}`;

  return (
    <div className="min-h-screen bg-gray-50 font-sans">
      <header className="bg-slate-800 text-white px-4 py-4 flex items-center gap-3 sticky top-0 z-30">
        <button
          onClick={onBack}
          className="bg-white/10 hover:bg-white/20 text-white p-2 rounded-lg flex items-center min-h-11 min-w-11 justify-center"
          aria-label="Go back"
        >
          <ArrowLeft size={20} />
        </button>
        <Camera size={22} />
        <h1 className="text-xl font-bold m-0">Add Photos to Homes</h1>
      </header>

      <div className="max-w-3xl mx-auto px-4 py-6">
        <p className="text-gray-700 text-lg leading-relaxed mb-5">
          Add a real photo of the home. Customers will see it on the website.
          You can also paste a 3D tour link.
          Use a picture you took. Do not use a floor plan or a picture from the internet.
        </p>

        <section className="bg-white rounded-2xl shadow-sm border border-gray-200 p-5 mb-5">
          <h2 className="text-base font-bold text-slate-800 mb-1 flex items-center gap-2">
            <span className="bg-slate-800 text-white rounded-full w-6 h-6 inline-flex items-center justify-center text-sm">1</span>
            Pick the home
          </h2>

          {selectedHome ? (
            <div className="mt-3">
              <p className="text-lg font-semibold text-slate-800 break-words">{staffPhotoHomeName(selectedHome)}</p>
              {staffPhotoHomeDetails(selectedHome, failedUrls) && (
                <p className="text-sm text-gray-500 mt-1 break-words">{staffPhotoHomeDetails(selectedHome, failedUrls)}</p>
              )}
              {selectedGap && (
                <p className="text-base text-slate-700 mt-3 leading-relaxed">{PHOTO_GAP_HINTS[selectedGap]}</p>
              )}
              <button
                type="button"
                onClick={() => {
                  setSelectedId('');
                  setMessage(null);
                  setTourMessage(null);
                  setConfirmRemoveTour(false);
                }}
                className="mt-4 min-h-12 px-4 rounded-lg border border-gray-300 text-slate-800 font-semibold"
              >
                Choose a different home
              </button>
            </div>
          ) : (
            <>
              <p className="text-sm text-gray-500 mb-3 ml-8">
                {onlyNeedsPhotos
                  ? 'These homes show a floor plan, or their picture did not load. Pick one and add a photo.'
                  : 'Pick a home, then add a photo you took.'}
              </p>

              {loadError && (
                <div className="flex items-center gap-2 text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm mb-3">
                  <AlertCircle size={16} /> {loadError}
                </div>
              )}

              <div className="flex items-center gap-2 bg-gray-50 border border-gray-200 rounded-lg px-3 py-3 mb-3">
                <Search size={18} className="text-gray-500" />
                <input
                  type="text"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="Search homes…"
                  aria-label="Search for a home"
                  className="bg-transparent outline-none w-full min-w-0 text-base"
                />
              </div>

              {!loadingHomes && (
                <>
                  <div role="radiogroup" aria-label="Which homes to show" className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-3">
                    <button
                      type="button"
                      role="radio"
                      aria-checked={onlyNeedsPhotos}
                      onClick={() => setOnlyNeedsPhotos(true)}
                      className={`min-h-12 rounded-lg px-3 py-3 font-semibold text-base flex items-center justify-center gap-2 ${
                        onlyNeedsPhotos ? 'bg-slate-800 text-white' : 'bg-white border border-gray-300 text-slate-800'
                      }`}
                    >
                      Needs photos
                      <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${
                        onlyNeedsPhotos ? 'bg-amber-200 text-amber-950' : 'bg-amber-100 text-amber-800'
                      }`}>
                        {needsCount}
                      </span>
                    </button>
                    <button
                      type="button"
                      role="radio"
                      aria-checked={!onlyNeedsPhotos}
                      onClick={() => setOnlyNeedsPhotos(false)}
                      className={`min-h-12 rounded-lg px-3 py-3 font-semibold text-base ${
                        !onlyNeedsPhotos ? 'bg-slate-800 text-white' : 'bg-white border border-gray-300 text-slate-800'
                      }`}
                    >
                      All homes
                    </button>
                  </div>
                  <label className="flex items-center gap-2 cursor-pointer select-none text-sm text-slate-700 mb-3">
                    <input
                      type="checkbox"
                      checked={includeInactive}
                      onChange={e => setIncludeInactive(e.target.checked)}
                      className="w-6 h-6 shrink-0"
                    />
                    Include sold/archived homes
                  </label>
                </>
              )}

              {checkingPhotos && onlyNeedsPhotos && !loadingHomes && (
                <p role="status" className="text-sm text-gray-500 mb-3">Checking a few website pictures…</p>
              )}

              {loadingHomes ? (
                <div className="flex items-center gap-2 text-gray-500 text-sm py-3">
                  <Loader2 className="animate-spin" size={18} /> Loading homes…
                </div>
              ) : filteredHomes.length === 0 ? (
                listMessage ? <p role="status" className="text-sm text-gray-500 py-3">{listMessage}</p> : null
              ) : (
                <div
                  role="radiogroup"
                  aria-label="Choose a home"
                  className="max-h-96 overflow-y-auto rounded-lg border border-gray-300 divide-y divide-gray-100 bg-white"
                >
                  {filteredHomes.map(h => {
                    const checked = String(h.id) === String(selectedId);
                    const details = staffPhotoHomeDetails(h, failedUrls);
                    return (
                      <label
                        key={h.id}
                        className={`flex items-start gap-3 px-3 py-4 min-h-14 cursor-pointer ${checked ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                      >
                        <input
                          type="radio"
                          name="photo-home"
                          value={h.id}
                          checked={checked}
                          onChange={() => selectHome(h.id)}
                          aria-label={staffPhotoHomeLabel(h, failedUrls)}
                          className="mt-1 w-6 h-6 shrink-0"
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block font-semibold text-slate-800 text-base break-words">{staffPhotoHomeName(h)}</span>
                          {details && <span className="block text-sm text-gray-500 break-words">{details}</span>}
                        </span>
                      </label>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </section>

        {selectedHome && (
          <section ref={uploadSectionRef} className="bg-white rounded-2xl shadow-sm border border-gray-200 p-5 mb-5">
            <h2 className="text-base font-bold text-slate-800 mb-1 flex items-center gap-2">
              <span className="bg-slate-800 text-white rounded-full w-6 h-6 inline-flex items-center justify-center text-sm">2</span>
              Add the photos
            </h2>
            <p className="text-sm text-gray-500 mb-3 ml-8">
              {`For: ${staffPhotoHomeName(selectedHome)}${staffPhotoHomeDetails(selectedHome, failedUrls) ? ` (${staffPhotoHomeDetails(selectedHome, failedUrls)})` : ''}`}
            </p>
            {isPreOwned(selectedHome) && (
              <p className="text-sm text-slate-700 mb-3 ml-8 leading-relaxed">
                This is a pre-owned home. Add pictures of this home, taken on the lot.
              </p>
            )}

            <div
              onDragOver={e => { e.preventDefault(); if (!uploading) setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
            >
              {uploading ? (
                <div className="flex flex-col items-center gap-2 text-blue-700 py-8" role="status">
                  <Loader2 className="animate-spin" size={36} />
                  <span className="font-semibold">Uploading… please wait</span>
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <label className={uploadButtonClass}>
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept={ACCEPT}
                      multiple
                      aria-label="Choose photos"
                      className="sr-only"
                      onChange={e => uploadFiles(e.target.files)}
                    />
                    <UploadCloud size={32} className="text-blue-500" />
                    <span className="font-semibold text-slate-800 text-base">Choose photos</span>
                    <span className="text-sm text-gray-500">From this phone or computer</span>
                  </label>
                  <label className={uploadButtonClass}>
                    <input
                      ref={cameraInputRef}
                      type="file"
                      accept="image/*"
                      capture="environment"
                      aria-label="Take a photo"
                      className="sr-only"
                      onChange={e => uploadFiles(e.target.files)}
                    />
                    <Camera size={32} className="text-blue-500" />
                    <span className="font-semibold text-slate-800 text-base">Take a photo</span>
                    <span className="text-sm text-gray-500">Opens the camera</span>
                  </label>
                </div>
              )}
              <p className="text-center text-sm text-gray-500 mt-3">
                or drop photos here · up to 20 at a time · JPG, PNG, WebP, or GIF · up to 15 MB each
              </p>
              <p className="text-center text-sm text-slate-700 mt-2 leading-relaxed">
                Use a photo you took. Do not use a picture from the internet.
              </p>
            </div>
          </section>
        )}

        {message && (
          <div
            ref={messageRef}
            role={message.type === 'error' ? 'alert' : 'status'}
            aria-live="polite"
            className={`flex items-start gap-2 rounded-lg px-4 py-4 text-base mb-5 ${
              message.type === 'ok'
                ? 'bg-green-50 border border-green-200 text-green-800'
                : 'bg-red-50 border border-red-200 text-red-800'
            }`}
          >
            {message.type === 'ok' ? <CheckCircle size={22} className="shrink-0 mt-0.5" /> : <AlertCircle size={22} className="shrink-0 mt-0.5" />}
            <span className="font-semibold">{message.text}</span>
          </div>
        )}

        {selectedId && (
          <section className="bg-white rounded-2xl shadow-sm border border-gray-200 p-5">
            <h2 className="text-base font-bold text-slate-800 mb-3 flex items-center gap-2">
              <span className="bg-slate-800 text-white rounded-full w-6 h-6 inline-flex items-center justify-center text-sm">3</span>
              Photos on this home
            </h2>
            {photos.length > 1 && (
              <p className="text-sm text-gray-500 mb-3 ml-8">
                The <strong>Main</strong> photo (first one) is what customers see first. Use
                “Make main” on any photo to feature it.
              </p>
            )}

            {loadingPhotos ? (
              <div className="flex items-center gap-2 text-gray-500 text-sm py-3">
                <Loader2 className="animate-spin" size={18} /> Loading…
              </div>
            ) : photos.length === 0 ? (
              <div role="status" className="flex flex-col items-center gap-2 text-gray-500 py-6">
                <ImageOff size={32} aria-hidden="true" />
                <span className="text-sm text-center">No photos added yet. Add a picture you took of this home.</span>
              </div>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                {photos.map((p, idx) => (
                  <div key={p.filename} className={`relative group rounded-lg overflow-hidden border bg-gray-100 ${idx === 0 ? 'border-blue-500 ring-2 ring-blue-200' : 'border-gray-200'}`}>
                    <StaffPhotoPreview
                      url={p.url}
                      alt={`${selectedHome ? staffPhotoHomeName(selectedHome) : 'Home'} photo ${idx + 1}`}
                    />
                    {idx === 0 ? (
                      <span className="absolute top-1.5 left-1.5 inline-flex items-center gap-1 bg-blue-600 text-white text-xs font-semibold rounded-full px-2 py-0.5 shadow">
                        <Star size={12} fill="currentColor" /> Main
                      </span>
                    ) : (
                      <button
                        onClick={() => handleMakeMain(p.filename)}
                        className="absolute bottom-1.5 left-1.5 inline-flex items-center gap-1 bg-white/90 hover:bg-blue-600 hover:text-white text-blue-700 text-xs font-semibold rounded-full px-2 py-1 shadow min-h-11"
                        title="Make this the main photo"
                      >
                        <Star size={12} /> Make main
                      </button>
                    )}
                    <button
                      onClick={() => setPendingDelete(p.filename)}
                      className="absolute top-1.5 right-1.5 bg-white/90 hover:bg-red-600 hover:text-white text-red-600 rounded-full p-3 shadow min-h-12 min-w-12 flex items-center justify-center"
                      aria-label="Remove photo"
                      title="Remove photo"
                    >
                      <Trash2 size={16} />
                    </button>
                    {photos.length > 1 && (
                      <div className="absolute bottom-1.5 right-1.5 flex gap-1">
                        <button
                          onClick={() => handleMove(idx, -1)}
                          disabled={idx === 0}
                          className="bg-white/90 hover:bg-slate-700 hover:text-white text-slate-700 rounded-full p-1 shadow disabled:opacity-30 disabled:cursor-not-allowed min-h-11 min-w-11 flex items-center justify-center"
                          aria-label="Move photo earlier"
                          title="Move earlier"
                        >
                          <ChevronLeft size={16} />
                        </button>
                        <button
                          onClick={() => handleMove(idx, 1)}
                          disabled={idx === photos.length - 1}
                          className="bg-white/90 hover:bg-slate-700 hover:text-white text-slate-700 rounded-full p-1 shadow disabled:opacity-30 disabled:cursor-not-allowed min-h-11 min-w-11 flex items-center justify-center"
                          aria-label="Move photo later"
                          title="Move later"
                        >
                          <ChevronRight size={16} />
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {selectedHome && (
          <section aria-label="3D tour link" className="bg-white rounded-2xl shadow-sm border border-gray-200 p-5 mt-5">
            <h2 className="text-base font-bold text-slate-800 mb-1 flex items-center gap-2">
              <Box size={18} aria-hidden="true" />
              3D tour link
            </h2>
            <p className="text-sm text-gray-600 mb-3 leading-relaxed">
              Paste the Matterport link for this home, or just the tour ID.
              Customers will see a 3D tour on the website.
            </p>
            {loadingTour ? (
              <p className="flex items-center gap-2 text-gray-500 text-sm" role="status">
                <Loader2 className="animate-spin" size={18} /> Loading 3D tour…
              </p>
            ) : (
              <>
                {!tourLoadFailed && (staffTourId ? (
                  <p className="text-sm text-slate-700 mb-3 break-words">
                    Saved.{' '}
                    <a
                      href={canonicalMatterportUrl(staffTourId)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="font-semibold text-blue-700 underline"
                    >
                      Open 3D tour
                    </a>
                  </p>
                ) : canonicalMatterportUrl(selectedHome.matterport_id) ? (
                  <p className="text-sm text-slate-700 mb-3 leading-relaxed break-words">
                    This home already has a 3D tour. Paste a new link only if you need to replace it.{' '}
                    <a
                      href={canonicalMatterportUrl(selectedHome.matterport_id)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="font-semibold text-blue-700 underline"
                    >
                      Open the current 3D tour
                    </a>
                  </p>
                ) : (
                  <p className="text-sm text-gray-500 mb-3">No 3D tour saved for this home yet.</p>
                ))}
                <label htmlFor="matterport-tour" className="block text-base font-semibold text-slate-800 mb-2">
                  Matterport link
                </label>
                <input
                  id="matterport-tour"
                  type="text"
                  inputMode="url"
                  autoCapitalize="off"
                  autoCorrect="off"
                  autoComplete="off"
                  spellCheck={false}
                  value={tourInput}
                  onChange={(event) => setTourInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault();
                      saveTour();
                    }
                  }}
                  placeholder="https://my.matterport.com/show/?m=..."
                  aria-label="Matterport link"
                  disabled={savingTour || removingTour}
                  className="w-full min-h-12 max-w-full rounded-lg border border-gray-300 px-3 py-2 text-base"
                />
                <div className="flex flex-col sm:flex-row gap-2 mt-3">
                  <button
                    type="button"
                    onClick={saveTour}
                    disabled={savingTour || removingTour}
                    className="min-h-12 rounded-lg bg-slate-800 text-white font-semibold px-4 disabled:opacity-50"
                  >
                    {savingTour ? 'Saving…' : 'Save 3D tour'}
                  </button>
                  {staffTourId && (
                    <button
                      type="button"
                      onClick={() => setConfirmRemoveTour(true)}
                      disabled={savingTour || removingTour}
                      className="min-h-12 rounded-lg border border-gray-300 text-slate-800 font-semibold px-4 disabled:opacity-50"
                    >
                      Remove 3D tour
                    </button>
                  )}
                </div>
              </>
            )}
            {tourMessage && (
              <div
                role={tourMessage.type === 'error' ? 'alert' : 'status'}
                className={`flex items-start gap-2 rounded-lg px-4 py-4 text-base mt-4 ${
                  tourMessage.type === 'ok'
                    ? 'bg-green-50 border border-green-200 text-green-800'
                    : 'bg-red-50 border border-red-200 text-red-800'
                }`}
              >
                {tourMessage.type === 'ok'
                  ? <CheckCircle size={22} className="shrink-0 mt-0.5" />
                  : <AlertCircle size={22} className="shrink-0 mt-0.5" />}
                <span className="font-semibold break-words">{tourMessage.text}</span>
              </div>
            )}
          </section>
        )}
      </div>
      <ConfirmDialog
        open={!!pendingDelete}
        title="Remove this photo from the website?"
        message="Customers will stop seeing it. The home stays listed."
        confirmLabel="Yes, remove the photo"
        cancelLabel="No, keep the photo"
        danger
        busy={deleting}
        onConfirm={handleDelete}
        onCancel={() => { if (!deleting) setPendingDelete(null); }}
      />
      <ConfirmDialog
        open={confirmRemoveTour}
        title="Remove this 3D tour link?"
        message="Customers will stop seeing the tour you added. The home stays listed."
        confirmLabel="Yes, remove the tour"
        cancelLabel="No, keep the tour"
        danger
        busy={removingTour}
        onConfirm={removeTour}
        onCancel={() => { if (!removingTour) setConfirmRemoveTour(false); }}
      />
    </div>
  );
}
