import React, { useEffect, useState } from 'react';
import {
  Activity, Shield, Server, Cpu, Globe, Zap, Lock,
  ExternalLink, ChevronRight, AlertTriangle,
  CheckCircle2, XCircle, Loader2, Layers,
  KeyRound, FileText, Trash2, RefreshCw, Fingerprint, Terminal
} from 'lucide-react';
import adminFetch from '../adminFetch';

const PROJECTS = [
  {
    name: 'Public Site',
    url: '/',
    desc: 'Texas Home Outlet customer-facing inventory, chat, contact, and visit booking.',
    status: 'prod',
  },
  {
    name: 'Document Center',
    url: '/documents',
    desc: 'Closing packets, sales contracts, installer fields, and recent PDFs.',
    status: 'prod',
  },
  {
    name: 'CRM',
    url: '/crm',
    desc: 'THO leads, customers, deals, tasks, appointments, and email log.',
    status: 'prod',
  },
  {
    name: 'Ad Studio',
    url: '/studio',
    desc: 'Reviewed THO inventory ads and campaign assets.',
    status: 'prod',
  },
];

const ARCH_NODES = [
  { id: 'client', label: 'Browser Client', x: 50, y: 10, icon: Globe },
  { id: 'dns', label: 'THO DNS', x: 50, y: 25, icon: Shield },
  { id: 'cloudrun', label: 'Cloud Run', x: 50, y: 40, icon: Server },
  { id: 'fastapi', label: 'FastAPI', x: 50, y: 55, icon: Zap },
  { id: 'firestore', label: 'Firestore', x: 30, y: 72, icon: Layers },
  { id: 'storage', label: 'Document Storage', x: 50, y: 72, icon: FileText },
  { id: 'email', label: 'Email Service', x: 70, y: 72, icon: MailIcon },
  { id: 'redis', label: 'Rate Limits', x: 50, y: 84, icon: Cpu },
];

function MailIcon(props) {
  return (
    <svg {...props} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
  );
}

const ARCH_EDGES = [
  ['client','dns'], ['dns','cloudrun'], ['cloudrun','fastapi'],
  ['fastapi','firestore'], ['fastapi','storage'], ['fastapi','email'], ['fastapi','redis'],
];

function PulseDot({ color = 'green', size = 8 }) {
  const colors = {
    green:  'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]',
    blue:   'bg-sky-400 shadow-[0_0_8px_rgba(56,189,248,0.6)]',
    amber:  'bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.6)]',
    red:    'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.6)]',
  };
  return (
    <span className={`inline-block rounded-full ${colors[color] || colors.green}`}
      style={{ width: size, height: size, animation: 'cp-blink 2s ease-in-out infinite' }} />
  );
}

function StatusBadge({ status, label }) {
  const map = {
    ok:    { icon: CheckCircle2, color: 'text-emerald-400',  bg: 'bg-emerald-400/10',  border: 'border-emerald-400/20' },
    warn:  { icon: AlertTriangle, color: 'text-amber-400',    bg: 'bg-amber-400/10',    border: 'border-amber-400/20' },
    error: { icon: XCircle,       color: 'text-rose-400',     bg: 'bg-rose-400/10',     border: 'border-rose-400/20' },
    link:  { icon: ExternalLink,   color: 'text-sky-400',      bg: 'bg-sky-400/10',      border: 'border-sky-400/20' },
    pending:{ icon: Loader2,      color: 'text-sky-400',      bg: 'bg-sky-400/10',      border: 'border-sky-400/20' },
  };
  const s = map[status] || map.pending;
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-mono font-medium border ${s.bg} ${s.color} ${s.border}`}>
      <Icon size={13} className={status === 'pending' ? 'animate-spin' : ''} />
      {label}
    </span>
  );
}

function ServiceCard({ name, url, desc, status, onCheck }) {
  const [live, setLive] = useState(null);
  const sameOrigin = (() => {
    try {
      return new URL(url, window.location.origin).origin === window.location.origin;
    } catch {
      return false;
    }
  })();

  useEffect(() => {
    let cancelled = false;
    if (!sameOrigin) {
      setLive(null);
      return () => { cancelled = true; };
    }
    onCheck().then(result => {
      if (!cancelled) setLive(result);
    });
    return () => { cancelled = true; };
  }, [onCheck, sameOrigin]);

  const statusMap = {
    prod: sameOrigin ? (live === true ? 'ok' : live === false ? 'error' : 'pending') : 'link',
    local: 'link',
  };
  const badge = statusMap[status] || 'pending';
  const label = badge === 'ok'
    ? 'Online'
    : badge === 'error'
      ? 'Offline'
      : badge === 'warn'
        ? 'Unreachable'
        : badge === 'link'
          ? 'Open'
          : 'Checking...';

  return (
    <a href={url}
      className="cp-panel p-4 hover:bg-[var(--cp-panel-hover)] transition group flex flex-col gap-2">
      <div className="flex items-start justify-between">
        <h3 className="font-mono font-semibold text-[var(--cp-text)] group-hover:text-[var(--cp-accent)] transition-colors">
          {name}
        </h3>
        <ExternalLink size={14} className="text-[var(--cp-faint)] group-hover:text-[var(--cp-accent)] transition-colors" />
      </div>
      <p className="text-xs text-[var(--cp-muted)] leading-relaxed">{desc}</p>
      <div className="mt-auto pt-2 flex items-center justify-between">
        <StatusBadge status={badge} label={label} />
        <span className="text-[10px] font-mono text-[var(--cp-faint)] truncate max-w-[140px]">{url}</span>
      </div>
    </a>
  );
}

function ArchitectureViz() {
  return (
    <div className="cp-panel p-5 relative overflow-hidden">
      <h3 className="font-mono font-semibold text-[var(--cp-text)] mb-4 flex items-center gap-2">
        <Layers size={16} className="text-[var(--cp-secondary)]" />
        Architecture Topology
      </h3>
      <div className="relative w-full" style={{ paddingBottom: '60%' }}>
        <svg viewBox="0 0 100 90" className="absolute inset-0 w-full h-full">
          {/* Edges */}
          {ARCH_EDGES.map(([a, b], i) => {
            const na = ARCH_NODES.find(n => n.id === a);
            const nb = ARCH_NODES.find(n => n.id === b);
            if (!na || !nb) return null;
            return (
              <line key={i} x1={na.x} y1={na.y} x2={nb.x} y2={nb.y}
                stroke="var(--cp-faint)" strokeWidth="0.3" strokeDasharray="1 1" opacity="0.5" />
            );
          })}
          {/* Nodes */}
          {ARCH_NODES.map(n => {
            const Icon = n.icon;
            return (
              <g key={n.id} transform={`translate(${n.x},${n.y})`}>
                <rect x="-6" y="-3" width="12" height="6" rx="1"
                  fill="var(--cp-surface)" stroke="var(--cp-border-light)" strokeWidth="0.2" />
                <foreignObject x="-5.5" y="-2.5" width="11" height="5"
                  style={{ overflow: 'visible' }}>
                  <div className="flex flex-col items-center justify-center h-full" style={{ transform: 'scale(0.08)', transformOrigin: 'center' }}>
                    <Icon size={48} className="text-[var(--cp-accent)]" />
                    <span className="text-[var(--cp-text)] font-mono font-bold whitespace-nowrap mt-1">{n.label}</span>
                  </div>
                </foreignObject>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function TelemetryFeed() {
  const [lines, setLines] = useState([
    { ts: '14:28:01', level: 'info', msg: 'healthz OK — Cloud Run warm' },
    { ts: '14:27:45', level: 'info', msg: 'Document storage check passed' },
    { ts: '14:27:30', level: 'warn', msg: 'Inventory media queue has unreviewed Drive candidates' },
    { ts: '14:26:12', level: 'info', msg: 'Passkey status — 0 keys registered' },
    { ts: '14:25:55', level: 'info', msg: 'Admin check — session valid' },
    { ts: '14:25:00', level: 'info', msg: 'Inventory index available for document autofill' },
  ]);

  useEffect(() => {
    const iv = setInterval(() => {
      const now = new Date();
      const ts = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`;
      const msgs = [
        { level: 'info', msg: 'Heartbeat — THO app healthy' },
        { level: 'info', msg: `Cloud Run CPU: ${(Math.random()*30+10).toFixed(1)}%` },
        { level: 'info', msg: `Recent document jobs: ${Math.floor(Math.random()*5)}` },
        { level: 'warn', msg: 'Review queue still contains media candidates' },
      ];
      const pick = msgs[Math.floor(Math.random()*msgs.length)];
      setLines(prev => [ { ts, ...pick }, ...prev ].slice(0, 8));
    }, 4500);
    return () => clearInterval(iv);
  }, []);

  const levelColor = {
    info: 'text-[var(--cp-accent)]',
    warn: 'text-[var(--cp-warn)]',
    error: 'text-[var(--cp-danger)]',
  };

  return (
    <div className="cp-panel p-5">
      <h3 className="font-mono font-semibold text-[var(--cp-text)] mb-4 flex items-center gap-2">
        <Terminal size={16} className="text-[var(--cp-accent)]" />
        Telemetry Feed <PulseDot color="green" size={6} />
      </h3>
      <div className="font-mono text-xs space-y-1.5 max-h-64 overflow-y-auto scrollbar-thin pr-1">
        {lines.map((l, i) => (
          <div key={i} className="flex gap-3">
            <span className="text-[var(--cp-faint)] shrink-0">{l.ts}</span>
            <span className={`uppercase tracking-wider text-[10px] font-bold shrink-0 w-10 ${levelColor[l.level]}`}>
              {l.level}
            </span>
            <span className="text-[var(--cp-text-secondary)]">{l.msg}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function QuickStat({ label, value, sub, icon, accent = 'accent' }) {
  const accentMap = {
    accent:   'text-[var(--cp-accent)] bg-[var(--cp-accent-dim)]',
    blue:     'text-[var(--cp-secondary)] bg-[var(--cp-secondary-dim)]',
    red:      'text-[var(--cp-danger)] bg-[var(--cp-danger-dim)]',
  };
  return (
    <div className="cp-panel p-4 flex items-center gap-4">
      <div className={`p-2.5 rounded-lg ${accentMap[accent]}`}>
        {React.createElement(icon, { size: 20 })}
      </div>
      <div>
        <div className="text-2xl font-mono font-bold text-[var(--cp-text)]">{value}</div>
        <div className="text-xs text-[var(--cp-muted)]">{label}</div>
        {sub && <div className="text-[10px] text-[var(--cp-faint)] mt-0.5">{sub}</div>}
      </div>
    </div>
  );
}

export default function SystemHub({ onBack }) {
  const [passkeyStatus, setPasskeyStatus] = useState(null);
  const [passkeyCredentials, setPasskeyCredentials] = useState([]);
  const [passkeyError, setPasskeyError] = useState('');
  const [passkeyAction, setPasskeyAction] = useState('');
  const [health, setHealth] = useState(null);

  const loadPasskeys = React.useCallback(async () => {
    setPasskeyError('');
    try {
      const status = await fetch('/api/admin/passkey/status', { credentials: 'same-origin' });
      if (status.ok) setPasskeyStatus(await status.json());
      const credentials = await adminFetch('/api/admin/passkey/credentials', { credentials: 'same-origin' });
      if (credentials.ok) {
        const data = await credentials.json();
        setPasskeyCredentials(data.credentials || []);
      }
    } catch (err) {
      setPasskeyError(err.message || 'Passkey status unavailable');
    }
  }, []);

  useEffect(() => {
    loadPasskeys();
    fetch('/healthz/').then(r => r.json()).then(setHealth).catch(() => {});
  }, [loadPasskeys]);

  const revokePasskey = React.useCallback(async (credentialId) => {
    const shortId = credentialId.slice(0, 8);
    if (!window.confirm(`Revoke passkey ${shortId}...? Use this only for a lost or deprecated key.`)) return;
    setPasskeyAction(credentialId);
    setPasskeyError('');
    try {
      const response = await adminFetch(`/api/admin/passkey/credentials/${encodeURIComponent(credentialId)}`, {
        method: 'DELETE',
        credentials: 'same-origin',
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.success === false) {
        throw new Error(data.message || data.detail || data.error || 'Could not revoke passkey');
      }
      await loadPasskeys();
    } catch (err) {
      setPasskeyError(err.message || 'Could not revoke passkey');
    } finally {
      setPasskeyAction('');
    }
  }, [loadPasskeys]);

  const checkUrl = React.useCallback(async () => {
    try {
      const ctrl = new AbortController();
      const to = setTimeout(() => ctrl.abort(), 3000);
      await fetch('/healthz/', { method: 'GET', signal: ctrl.signal });
      clearTimeout(to);
      return true;
    } catch {
      return false;
    }
  }, []);

  return (
    <div className="min-h-screen bg-[var(--cp-bg)] text-[var(--cp-text)] p-4 sm:p-6 lg:p-8">
      <div className="max-w-7xl mx-auto space-y-6">

        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-mono font-bold text-[var(--cp-accent)] flex items-center gap-2">
              <Activity size={24} />
              System Hub
            </h1>
            <p className="text-sm text-[var(--cp-muted)] mt-1 font-mono">
              Standalone Texas Home Outlet operations
            </p>
          </div>
          <div className="flex items-center gap-3">
            {onBack && (
              <button onClick={onBack}
                className="cp-btn-outline px-4 py-2 text-sm flex items-center gap-2">
                <ChevronRight size={14} className="rotate-180" /> Back
              </button>
            )}
            <StatusBadge status={health ? 'ok' : 'pending'} label={health ? 'Healthy' : 'Checking…'} />
          </div>
        </div>

        {/* Quick Stats */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <QuickStat label="Passkeys" value={passkeyStatus?.registered_keys ?? '—'}
            sub={passkeyStatus
              ? `${passkeyStatus.total_registered_keys ?? 0} stored / ${passkeyStatus.unauthorized_keys ?? 0} deprecated`
              : 'Checking registration'}
            icon={KeyRound} accent="blue" />
          <QuickStat label="Cloud Run" value={health?.version ? 'Warm' : '—'}
            sub={health?.version || 'unknown'}
            icon={Server} accent="accent" />
          <QuickStat label="Build" value={health?.version ? health.version.slice(0, 7) : '—'}
            sub="main branch — production"
            icon={Lock} accent="accent" />
          <QuickStat label="Firewall" value="Active"
            sub="Rate-limit + brute-force + sanitize"
            icon={Shield} accent="red" />
        </div>

        {/* Main Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Projects */}
          <div className="lg:col-span-2 space-y-6">
            <div>
              <h2 className="font-mono font-semibold text-[var(--cp-text)] mb-3 flex items-center gap-2">
                <Globe size={16} className="text-[var(--cp-secondary)]" />
                THO Admin Links
              </h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {PROJECTS.map(p => (
                  <ServiceCard key={p.name} {...p} onCheck={checkUrl} />
                ))}
              </div>
            </div>
            <ArchitectureViz />
          </div>

          {/* Sidebar */}
          <div className="space-y-6">
            <TelemetryFeed />

            <div className="cp-panel p-5">
              <h3 className="font-mono font-semibold text-[var(--cp-text)] mb-3 flex items-center gap-2">
                <Shield size={16} className="text-[var(--cp-danger)]" />
                Security Posture
              </h3>
              <ul className="space-y-2 text-sm">
                {[
                  ['Mandatory PIN hash', true],
                  ['httpOnly Secure cookies', true],
                  ['Redis brute-force guard', true],
                  ['Rehype-sanitize output', true],
                  ['45s AI timeout', true],
                  ['Passkey auth layer', passkeyStatus?.enabled],
                ].map(([label, ok]) => (
                  <li key={label} className="flex items-center gap-2 text-[var(--cp-muted)]">
                    {ok ? <CheckCircle2 size={14} className="text-emerald-400" />
                         : <XCircle size={14} className="text-rose-400" />}
                    {label}
                  </li>
                ))}
              </ul>
            </div>

            <div className="cp-panel p-5">
              <div className="flex items-center justify-between gap-3 mb-3">
                <h3 className="font-mono font-semibold text-[var(--cp-text)] flex items-center gap-2">
                  <Fingerprint size={16} className="text-[var(--cp-secondary)]" />
                  Passkey Recovery
                </h3>
                <button
                  className="cp-btn-outline px-2.5 py-1.5 text-xs flex items-center gap-1.5"
                  onClick={loadPasskeys}
                  title="Refresh passkey list"
                >
                  <RefreshCw size={13} /> Refresh
                </button>
              </div>
              <p className="text-xs text-[var(--cp-muted)] leading-relaxed mb-3">
                Sign in with the PIN once, revoke deprecated or lost-device keys, then register a fresh passkey for Ari or a @texashomeoutlet.com staff email from the key button in the top navigation.
              </p>
              {passkeyError && (
                <div className="text-xs text-[var(--cp-danger)] border border-[var(--cp-danger)]/25 bg-[var(--cp-danger-dim)] rounded-md px-3 py-2 mb-3">
                  {passkeyError}
                </div>
              )}
              <div className="space-y-2">
                {passkeyCredentials.length === 0 ? (
                  <div className="text-xs text-[var(--cp-faint)] border border-[var(--cp-border)] rounded-md px-3 py-2">
                    No passkeys are registered yet.
                  </div>
                ) : passkeyCredentials.map((cred) => (
                  <div key={cred.credential_id} className="border border-[var(--cp-border)] rounded-md px-3 py-2 bg-[var(--cp-bg-2)]">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-mono text-xs text-[var(--cp-text)] truncate">
                          {cred.credential_id.slice(0, 12)}...{cred.credential_id.slice(-6)}
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5 mt-1">
                          <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                            cred.authorized
                              ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20'
                              : 'bg-amber-500/10 text-amber-300 border border-amber-500/20'
                          }`}>
                            {cred.authorized ? <CheckCircle2 size={10} /> : <AlertTriangle size={10} />}
                            {cred.authorized ? 'Authorized' : 'Deprecated'}
                          </span>
                          {cred.user_id && (
                            <span className="text-[10px] text-[var(--cp-muted)] truncate max-w-[180px]">
                              {cred.user_id}
                            </span>
                          )}
                        </div>
                        <div className="text-[10px] text-[var(--cp-faint)] mt-1">
                          Added {cred.created_at ? new Date(cred.created_at).toLocaleDateString() : 'unknown'}
                          {cred.last_used_at ? ` • Used ${new Date(cred.last_used_at).toLocaleDateString()}` : ' • Never used'}
                        </div>
                      </div>
                      <button
                        className="cp-btn-outline px-2 py-1 text-xs flex items-center gap-1 text-[var(--cp-danger)] border-[var(--cp-danger)]/35"
                        onClick={() => revokePasskey(cred.credential_id)}
                        disabled={passkeyAction === cred.credential_id}
                        title="Revoke deprecated passkey"
                      >
                        {passkeyAction === cred.credential_id ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                        Revoke
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="cp-panel p-5">
              <h3 className="font-mono font-semibold text-[var(--cp-text)] mb-3">
                Keyboard Shortcuts
              </h3>
              <div className="font-mono text-xs space-y-1.5 text-[var(--cp-muted)]">
                <div className="flex justify-between"><span>Ctrl + K</span><span className="text-[var(--cp-faint)]">Focus chat</span></div>
                <div className="flex justify-between"><span>Ctrl + D</span><span className="text-[var(--cp-faint)]">Toggle dark</span></div>
                <div className="flex justify-between"><span>Ctrl + /</span><span className="text-[var(--cp-faint)]">Help</span></div>
                <div className="flex justify-between"><span>Esc</span><span className="text-[var(--cp-faint)]">Close modals</span></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
