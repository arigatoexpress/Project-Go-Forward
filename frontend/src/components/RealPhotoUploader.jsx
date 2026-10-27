import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, CheckCircle, ImageOff, Loader2, Star, Trash2, UploadCloud } from 'lucide-react';
import adminFetch from '../adminFetch';
import ConfirmDialog from './ConfirmDialog';

const ACCEPT = 'image/jpeg,image/png,image/webp,image/gif';

function photoMessage(data, resOk) {
  const retryNames = Array.isArray(data?.retryable) ? data.retryable.filter(Boolean) : [];
  const retry = retryNames.length > 0 ? ` Try these again: ${retryNames.join(', ')}.` : '';
  if (!resOk) {
    if (data?.storage_unavailable) {
      return {
        type: 'error',
        text: `The photo safe is busy, so nothing new was saved. Refresh this page, then try again.${retry}`,
      };
    }
    return {
      type: 'error',
      text: 'Those photos were not added. Use a JPG or PNG of the actual home, under 15 MB, and try again.',
    };
  }
  const okCount = (data?.uploaded || []).length;
  const errCount = (data?.errors || []).length;
  if (data?.storage_unavailable && okCount > 0) {
    return {
      type: 'error',
      text: `${okCount} photo${okCount === 1 ? ' was' : 's were'} saved, then the photo safe got busy. Refresh before trying the rest.${retry}`,
    };
  }
  if (okCount > 0 && errCount === 0) {
    return {
      type: 'ok',
      text: `${okCount} photo${okCount === 1 ? '' : 's'} added. Customers will see ${okCount === 1 ? 'it' : 'them'} on the website shortly.`,
    };
  }
  if (okCount > 0) {
    return {
      type: 'ok',
      text: `${okCount} photo${okCount === 1 ? '' : 's'} added. ${errCount} file${errCount === 1 ? ' was' : 's were'} skipped because ${errCount === 1 ? 'it was' : 'they were'} not a photo.`,
    };
  }
  return {
    type: 'error',
    text: 'No photos were added. Use a JPG or PNG of the actual home, under 15 MB.',
  };
}

export default function RealPhotoUploader({ homeId, homeName = 'this home', preowned = false }) {
  const [photos, setPhotos] = useState([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const fileInputRef = useRef(null);

  const load = useCallback((id) => {
    if (!id) {
      setPhotos([]);
      return;
    }
    setLoading(true);
    adminFetch(`/api/inventory/${encodeURIComponent(id)}/photos`)
      .then((res) => res.json())
      .then((data) => setPhotos(Array.isArray(data?.photos) ? data.photos : []))
      .catch(() => setPhotos([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(homeId); }, [homeId, load]);

  const uploadFiles = async (fileList) => {
    const files = Array.from(fileList || []);
    if (uploading) return;
    if (!homeId) {
      setMessage({ type: 'error', text: 'Save the home first. You will stay on this page to add photos.' });
      return;
    }
    const images = files.filter((file) => file && String(file.type || '').startsWith('image/'));
    if (files.length === 0) return;
    if (images.length === 0) {
      setMessage({ type: 'error', text: 'That file is not a photo. Use a JPG or PNG of the actual home, not a floor plan or brochure.' });
      return;
    }
    setUploading(true);
    setMessage(null);
    try {
      const form = new FormData();
      images.forEach((file) => form.append('files', file));
      const res = await adminFetch(`/api/inventory/${encodeURIComponent(homeId)}/photos`, {
        method: 'POST',
        body: form,
      });
      const data = await res.json().catch(() => ({}));
      setMessage(photoMessage(data, res.ok));
      if (res.ok) load(homeId);
    } catch {
      setMessage({ type: 'error', text: 'The photos did not upload. Check the internet connection and try again.' });
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const removePhoto = async () => {
    if (!pendingDelete || !homeId) return;
    setDeleting(true);
    try {
      const res = await adminFetch(
        `/api/inventory/${encodeURIComponent(homeId)}/photos/${encodeURIComponent(pendingDelete)}`,
        { method: 'DELETE' },
      );
      if (res.ok) {
        setMessage({ type: 'ok', text: 'Photo removed. Customers will no longer see it.' });
        load(homeId);
      } else {
        setMessage({ type: 'error', text: 'That photo is still on the website. Try removing it again.' });
      }
    } catch {
      setMessage({ type: 'error', text: 'That photo is still on the website. Check the connection and try again.' });
    } finally {
      setDeleting(false);
      setPendingDelete(null);
    }
  };

  const makeMain = async (filename) => {
    const order = [filename, ...photos.map((photo) => photo.filename).filter((name) => name !== filename)];
    try {
      const res = await adminFetch(`/api/inventory/${encodeURIComponent(homeId)}/photos/order`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setPhotos(Array.isArray(data?.photos) ? data.photos : photos);
        setMessage({ type: 'ok', text: 'That photo is now the first one customers see.' });
      } else {
        setMessage({ type: 'error', text: 'The first photo did not change. Try again.' });
      }
    } catch {
      setMessage({ type: 'error', text: 'The first photo did not change. Check the connection and try again.' });
    }
  };

  return (
    <section className="mt-4 rounded-xl border border-[var(--cp-border)] bg-[var(--cp-panel)] p-4" aria-labelledby="real-photo-heading">
      <h3 id="real-photo-heading" className="text-lg font-semibold">Photos of the real home</h3>
      <p className="mt-1 text-base leading-relaxed text-[var(--cp-muted)]">
        Add pictures taken of {homeName} on the lot. Floor plans and brochure pictures are not used here.
        The first photo is the one customers see first.
      </p>
      {preowned && (
        <p className="mt-2 text-base leading-relaxed">
          This is a pre-owned home. Upload real photos taken on the lot. A floor plan shows as a floor plan, not as a photo of the home. If a photo does not show on the website, tell the owner. Photo upload on a pre-owned home has not been checked on the live site yet.
        </p>
      )}

      {!homeId ? (
        <p className="mt-3 text-base leading-relaxed">Save the home first. You will stay on this page to add the photos.</p>
      ) : (
        <>
          <label className="mt-4 flex min-h-24 cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-[var(--cp-border)] px-4 py-6 text-center hover:border-[var(--cp-accent)]">
            <UploadCloud size={32} aria-hidden="true" />
            <span className="text-lg font-semibold">{uploading ? 'Adding photos…' : 'Choose photos'}</span>
            <span className="text-base text-[var(--cp-muted)]">JPG or PNG, up to 15 MB each</span>
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPT}
              multiple
              disabled={uploading}
              className="sr-only"
              aria-label="Photos of the real home"
              onChange={(event) => uploadFiles(event.target.files)}
            />
          </label>

          {loading && (
            <p className="mt-3 flex items-center gap-2 text-base"><Loader2 size={18} className="animate-spin" /> Loading photos…</p>
          )}

          {!loading && photos.length === 0 && (
            <p role="status" className="mt-3 flex items-center gap-2 text-base text-[var(--cp-muted)]">
              <ImageOff size={18} aria-hidden="true" /> No real photos yet. Choose photos of the home on the lot.
            </p>
          )}

          {photos.length > 0 && (
            <ul className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
              {photos.map((photo, index) => (
                <li key={photo.filename} className="overflow-hidden rounded-lg border border-[var(--cp-border)]">
                  <img src={photo.url} alt={`${homeName} photo ${index + 1}`} className="h-32 w-full object-cover" />
                  <div className="flex flex-col gap-2 p-2">
                    {index === 0 ? (
                      <span className="inline-flex min-h-10 items-center justify-center gap-1 text-base font-semibold">
                        <Star size={16} aria-hidden="true" /> First photo
                      </span>
                    ) : (
                      <button type="button" onClick={() => makeMain(photo.filename)} className="min-h-12 rounded-lg border border-[var(--cp-border)] px-3 text-base font-semibold">
                        Show this first
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => setPendingDelete(photo.filename)}
                      className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg border border-red-500/50 px-3 text-base font-semibold text-red-400"
                    >
                      <Trash2 size={18} aria-hidden="true" /> Remove photo
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {message && (
        <p
          role={message.type === 'error' ? 'alert' : 'status'}
          className={`mt-3 flex items-start gap-2 rounded-lg px-3 py-3 text-base leading-relaxed ${
            message.type === 'ok' ? 'bg-green-700/20 text-green-200' : 'bg-red-700/20 text-red-200'
          }`}
        >
          {message.type === 'ok' ? <CheckCircle size={20} className="mt-0.5 shrink-0" /> : <AlertCircle size={20} className="mt-0.5 shrink-0" />}
          {message.text}
        </p>
      )}

      <ConfirmDialog
        open={!!pendingDelete}
        title="Remove this photo from the website?"
        message="Customers will stop seeing it. The home stays listed."
        confirmLabel="Yes, remove the photo"
        cancelLabel="No, keep the photo"
        danger
        busy={deleting}
        onConfirm={removePhoto}
        onCancel={() => setPendingDelete(null)}
      />
    </section>
  );
}
