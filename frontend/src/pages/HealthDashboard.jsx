import React, { useEffect, useState, useCallback } from 'react';
import {
  Activity, Server, Clock, AlertTriangle, CheckCircle2, XCircle,
  TrendingUp, Users, Zap, RefreshCw, Loader2, BarChart3, PieChart as PieIcon
} from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell } from 'recharts';
import adminFetch from '../adminFetch';

const LATENCY_COLORS = {
  good: '#34d399',   // emerald-400
  warn: '#fbbf24',   // amber-400
  bad:  '#f87171',   // red-400
};

function PulseDot({ color = 'green', size = 8 }) {
  const colors = {
    green: 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]',
    amber: 'bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.6)]',
    red:   'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.6)]',
  };
  return (
    <span className={`inline-block rounded-full ${colors[color] || colors.green}`}
      style={{ width: size, height: size, animation: 'cp-blink 2s ease-in-out infinite' }} />
  );
}

function StatCard({ label, value, sub, icon, accent = 'accent' }) {
  const accentMap = {
    accent: 'text-[var(--cp-accent)] bg-[var(--cp-accent-dim)]',
    blue:   'text-[var(--cp-secondary)] bg-[var(--cp-secondary-dim)]',
    red:    'text-[var(--cp-danger)] bg-[var(--cp-danger-dim)]',
    green:  'text-emerald-400 bg-emerald-400/10',
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

function StatusBadge({ status, label }) {
  const map = {
    ok:    { icon: CheckCircle2, color: 'text-emerald-400',  bg: 'bg-emerald-400/10',  border: 'border-emerald-400/20' },
    warn:  { icon: AlertTriangle, color: 'text-amber-400',    bg: 'bg-amber-400/10',    border: 'border-amber-400/20' },
    error: { icon: XCircle,       color: 'text-rose-400',     bg: 'bg-rose-400/10',     border: 'border-rose-400/20' },
  };
  const s = map[status] || map.warn;
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-mono font-medium border ${s.bg} ${s.color} ${s.border}`}>
      <Icon size={13} />
      {label}
    </span>
  );
}

export default function HealthDashboard({ onBack }) {
  const [metrics, setMetrics] = useState(null);
  const [detailedHealth, setDetailedHealth] = useState(null);
  const [userActivity, setUserActivity] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [metricsRes, healthRes, activityRes] = await Promise.all([
        adminFetch('/api/metrics'),
        adminFetch('/healthz/detailed'),
        adminFetch('/api/admin/user-activity?limit=20'),
      ]);

      if (metricsRes?.ok) setMetrics(await metricsRes.json());
      if (healthRes?.ok) setDetailedHealth(await healthRes.json());
      if (activityRes?.ok) setUserActivity(await activityRes.json());

      setLastUpdated(new Date());
    } catch (err) {
      setError(err.message || 'Failed to load health data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    const iv = setInterval(loadData, 15000);
    return () => clearInterval(iv);
  }, [loadData]);

  const overall = metrics?.overall || {};
  const endpoints = metrics?.endpoints || {};

  const chartData = Object.entries(endpoints)
    .map(([key, data]) => ({
      name: key.replace(/^GET |^POST |^PUT |^DELETE /, '').slice(0, 30),
      fullName: key,
      p50: data.p50 || 0,
      p95: data.p95 || 0,
      p99: data.p99 || 0,
      count: data.count || 0,
      errorRate: data.error_rate || 0,
    }))
    .sort((a, b) => b.p95 - a.p95)
    .slice(0, 10);

  const activityEntries = userActivity?.entries || [];
  const activityCounts = activityEntries.reduce((acc, entry) => {
    const action = entry.action || 'unknown';
    acc[action] = (acc[action] || 0) + 1;
    return acc;
  }, {});

  const activityPieData = Object.entries(activityCounts)
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 6);

  const formatUptime = (seconds) => {
    if (!seconds && seconds !== 0) return '—';
    const m = Math.floor(seconds / 60);
    const h = Math.floor(m / 60);
    const d = Math.floor(h / 24);
    if (d > 0) return `${d}d ${h % 24}h`;
    if (h > 0) return `${h}h ${m % 60}m`;
    return `${m}m`;
  };

  const latencyColor = (ms) => {
    if (ms > 500) return LATENCY_COLORS.bad;
    if (ms > 200) return LATENCY_COLORS.warn;
    return LATENCY_COLORS.good;
  };

  if (loading && !metrics) {
    return (
      <div className="min-h-screen bg-[var(--cp-bg)] flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="h-10 w-10 animate-spin text-[var(--cp-accent)] mx-auto mb-3" />
          <p className="text-[var(--cp-muted)]">Loading health dashboard...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--cp-bg)] text-[var(--cp-text)] p-4 sm:p-6 lg:p-8">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-mono font-bold text-[var(--cp-accent)] flex items-center gap-2">
              <Activity size={24} />
              Health Dashboard
            </h1>
            <p className="text-sm text-[var(--cp-muted)] mt-1 font-mono">
              Real-time system performance and user activity
            </p>
          </div>
          <div className="flex items-center gap-3">
            {onBack && (
              <button onClick={onBack}
                className="cp-btn-outline px-4 py-2 text-sm flex items-center gap-2">
                ← Back
              </button>
            )}
            <button
              onClick={loadData}
              className="cp-btn-outline px-3 py-2 text-sm flex items-center gap-2"
              title="Refresh data"
            >
              <RefreshCw size={14} /> Refresh
            </button>
            <StatusBadge status={detailedHealth ? 'ok' : 'warn'} label={detailedHealth ? 'Healthy' : 'Unknown'} />
          </div>
        </div>

        {error && (
          <div className="cp-panel p-4 border-l-4 border-[var(--cp-danger)] bg-[var(--cp-danger-dim)]">
            <p className="text-sm text-[var(--cp-danger)] flex items-center gap-2">
              <AlertTriangle size={16} /> {error}
            </p>
          </div>
        )}

        {/* Quick Stats */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            label="Total Requests"
            value={overall.count?.toLocaleString() ?? '—'}
            sub={lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : '—'}
            icon={BarChart3}
            accent="blue"
          />
          <StatCard
            label="p95 Latency"
            value={overall.p95 != null ? `${overall.p95}ms` : '—'}
            sub={overall.p50 != null ? `p50: ${overall.p50}ms` : '—'}
            icon={Zap}
            accent={overall.p95 > 500 ? 'red' : overall.p95 > 200 ? 'warn' : 'green'}
          />
          <StatCard
            label="Error Rate"
            value={overall.error_rate != null ? `${(overall.error_rate * 100).toFixed(2)}%` : '—'}
            sub={overall.count > 0 ? `${overall.count} total` : '—'}
            icon={AlertTriangle}
            accent={overall.error_rate > 0.05 ? 'red' : 'green'}
          />
          <StatCard
            label="Uptime"
            value={detailedHealth?.uptime_s ? formatUptime(detailedHealth.uptime_s) : '—'}
            sub={detailedHealth?.version ? `Build ${detailedHealth.version.slice(0, 7)}` : '—'}
            icon={Clock}
            accent="accent"
          />
        </div>

        {/* Charts Row */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Endpoint Latency Chart */}
          <div className="cp-panel p-5">
            <h3 className="font-mono font-semibold text-[var(--cp-text)] mb-4 flex items-center gap-2">
              <BarChart3 size={16} className="text-[var(--cp-accent)]" />
              Top Endpoints by p95 Latency
              <PulseDot color="green" size={6} />
            </h3>
            {chartData.length > 0 ? (
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} layout="vertical" margin={{ left: 20, right: 20, top: 5, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--cp-border)" opacity={0.3} />
                    <XAxis type="number" stroke="var(--cp-muted)" fontSize={10} tickFormatter={(v) => `${v}ms`} />
                    <YAxis type="category" dataKey="name" stroke="var(--cp-muted)" fontSize={10} width={120} />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: 'var(--cp-panel)',
                        border: '1px solid var(--cp-border)',
                        borderRadius: '6px',
                        fontSize: '12px',
                      }}
                      labelStyle={{ color: 'var(--cp-text)' }}
                      itemStyle={{ color: 'var(--cp-muted)' }}
                      formatter={(value, name) => [`${value}ms`, name]}
                    />
                    <Bar dataKey="p95" radius={[0, 4, 4, 0]}>
                      {chartData.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={latencyColor(entry.p95)} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="h-64 flex items-center justify-center text-sm text-[var(--cp-muted)]">
                No endpoint data yet
              </div>
            )}
          </div>

          {/* User Activity Breakdown */}
          <div className="cp-panel p-5">
            <h3 className="font-mono font-semibold text-[var(--cp-text)] mb-4 flex items-center gap-2">
              <Users size={16} className="text-[var(--cp-secondary)]" />
              Recent User Activity
            </h3>
            {activityEntries.length > 0 ? (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-2 text-xs font-mono text-[var(--cp-muted)] uppercase tracking-wider">
                  <span>Action</span>
                  <span className="text-right">Count</span>
                </div>
                {activityPieData.map(({ name, value }) => (
                  <div key={name} className="flex items-center justify-between text-xs font-mono">
                    <span className="text-[var(--cp-text)]">{name}</span>
                    <span className="text-[var(--cp-accent)]">{value}</span>
                  </div>
                ))}
                <div className="border-t border-[var(--cp-border)] pt-2 mt-2">
                  <div className="flex items-center justify-between text-xs font-mono">
                    <span className="text-[var(--cp-muted)]">Total (last 20)</span>
                    <span className="text-[var(--cp-accent)] font-bold">{activityEntries.length}</span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="h-40 flex items-center justify-center text-sm text-[var(--cp-muted)]">
                No user activity recorded yet
              </div>
            )}
          </div>
        </div>

        {/* System Health & Dependencies */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Dependency Status */}
          <div className="cp-panel p-5">
            <h3 className="font-mono font-semibold text-[var(--cp-text)] mb-3 flex items-center gap-2">
              <Server size={16} className="text-[var(--cp-secondary)]" />
              Dependencies
            </h3>
            {detailedHealth?.dependencies ? (
              <div className="flex flex-wrap gap-2">
                {Object.entries(detailedHealth.dependencies).map(([name, status]) => (
                  <span
                    key={name}
                    className={`inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-semibold ${
                      status === 'configured' || status === 'ok'
                        ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20'
                        : 'bg-amber-500/10 text-amber-300 border border-amber-500/20'
                    }`}
                  >
                    {status === 'configured' || status === 'ok' ? (
                      <CheckCircle2 size={10} />
                    ) : (
                      <AlertTriangle size={10} />
                    )}
                    {name}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-xs text-[var(--cp-muted)]">No dependency data available</p>
            )}
            {detailedHealth?.warnings?.length > 0 && (
              <div className="mt-3 space-y-1">
                {detailedHealth.warnings.map((w, i) => (
                  <div key={i} className="text-xs text-[var(--cp-warn)] flex items-center gap-1">
                    <AlertTriangle size={10} />
                    {w}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Slowest Endpoints Table */}
          <div className="lg:col-span-2 cp-panel p-5">
            <h3 className="font-mono font-semibold text-[var(--cp-text)] mb-3 flex items-center gap-2">
              <TrendingUp size={16} className="text-[var(--cp-accent)]" />
              Endpoint Performance Detail
            </h3>
            {chartData.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-xs font-mono">
                  <thead>
                    <tr className="text-[var(--cp-muted)] border-b border-[var(--cp-border)]">
                      <th className="text-left py-2 pr-4">Endpoint</th>
                      <th className="text-right py-2 px-2">Count</th>
                      <th className="text-right py-2 px-2">p50</th>
                      <th className="text-right py-2 px-2">p95</th>
                      <th className="text-right py-2 px-2">p99</th>
                      <th className="text-right py-2 pl-2">Errors</th>
                    </tr>
                  </thead>
                  <tbody>
                    {chartData.map((row) => (
                      <tr key={row.fullName} className="border-b border-[var(--cp-border)]/50 hover:bg-[var(--cp-surface)]/30">
                        <td className="py-2 pr-4 text-[var(--cp-text)] truncate max-w-[200px]" title={row.fullName}>
                          {row.fullName}
                        </td>
                        <td className="text-right py-2 px-2 text-[var(--cp-muted)]">{row.count}</td>
                        <td className="text-right py-2 px-2 text-[var(--cp-accent)]">{row.p50}ms</td>
                        <td className={`text-right py-2 px-2 ${row.p95 > 500 ? 'text-[var(--cp-danger)]' : row.p95 > 200 ? 'text-[var(--cp-warn)]' : 'text-emerald-400'}`}>
                          {row.p95}ms
                        </td>
                        <td className="text-right py-2 px-2 text-[var(--cp-muted)]">{row.p99}ms</td>
                        <td className="text-right py-2 pl-2">
                          {row.errorRate > 0 ? (
                            <span className="text-[var(--cp-danger)]">{(row.errorRate * 100).toFixed(1)}%</span>
                          ) : (
                            <span className="text-emerald-400">0%</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-[var(--cp-muted)]">No endpoint data yet</p>
            )}
          </div>
        </div>

        {/* Recent User Activity Log */}
        <div className="cp-panel p-5">
          <h3 className="font-mono font-semibold text-[var(--cp-text)] mb-3 flex items-center gap-2">
            <PieIcon size={16} className="text-[var(--cp-secondary)]" />
            Recent User Activity Log
          </h3>
          {activityEntries.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-mono">
                <thead>
                  <tr className="text-[var(--cp-muted)] border-b border-[var(--cp-border)]">
                    <th className="text-left py-2 pr-4">Time</th>
                    <th className="text-left py-2 px-2">Action</th>
                    <th className="text-left py-2 px-2">Session</th>
                    <th className="text-left py-2 px-2">IP</th>
                    <th className="text-left py-2 pl-2">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {activityEntries.slice(0, 10).map((entry, i) => (
                    <tr key={entry.id || i} className="border-b border-[var(--cp-border)]/50 hover:bg-[var(--cp-surface)]/30">
                      <td className="py-2 pr-4 text-[var(--cp-faint)] whitespace-nowrap">
                        {entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : '—'}
                      </td>
                      <td className="py-2 px-2">
                        <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold bg-[var(--cp-accent-dim)] text-[var(--cp-accent)] border border-[var(--cp-accent)]/20">
                          {entry.action}
                        </span>
                      </td>
                      <td className="py-2 px-2 text-[var(--cp-muted)] truncate max-w-[120px]" title={entry.session_id}>
                        {entry.session_id?.slice(0, 12) ?? '—'}
                      </td>
                      <td className="py-2 px-2 text-[var(--cp-faint)]">
                        {entry.ip?.slice(0, 20) ?? '—'}
                      </td>
                      <td className="py-2 pl-2 text-[var(--cp-muted)] truncate max-w-[200px]">
                        {entry.details ? JSON.stringify(entry.details).slice(0, 60) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-[var(--cp-muted)]">No user activity recorded yet</p>
          )}
        </div>
      </div>
    </div>
  );
}
