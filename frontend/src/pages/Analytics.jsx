import React, { useState, useEffect, useCallback } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, PieChart, Pie, Cell, LineChart, Line, AreaChart, Area } from 'recharts';
import { Users, Search, MessageSquare, TrendingUp, ArrowUp, ArrowDown, Phone, Calendar, DollarSign, Loader2, RefreshCw, AlertCircle, FileText, Home, Clock, Target, Zap } from 'lucide-react';
import adminFetch from '../adminFetch';
import { generateSrcSet, getImageSizes } from '../utils/imageOptimization';

const PIE_COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899'];
const AREA_COLORS = ['#3B82F6', '#10B981', '#F59E0B'];

const MetricCard = ({ title, value, icon, trend, trendUp, accent, subtitle }) => (
    <div className={`bg-white p-6 rounded-xl shadow-sm border ${accent ? 'border-blue-200 bg-gradient-to-br from-blue-50 to-white' : 'border-gray-200'} flex items-start justify-between hover:shadow-md transition-shadow`}>
        <div>
            <p className="text-sm font-medium text-gray-700 mb-1">{title}</p>
            <h3 className="text-2xl font-bold text-gray-900">{value}</h3>
            {trend && (
                <div className={`flex items-center mt-2 text-sm ${trendUp ? 'text-green-600' : 'text-red-600'}`}>
                    {trendUp ? <ArrowUp size={14} className="mr-1" /> : <ArrowDown size={14} className="mr-1" />}
                    <span>{trend}</span>
                </div>
            )}
            {subtitle && <p className="text-xs text-gray-600 mt-1">{subtitle}</p>}
        </div>
        <div className={`p-3 ${accent ? 'bg-blue-100' : 'bg-gray-50'} rounded-lg ${accent ? 'text-blue-600' : 'text-gray-600'}`}>
            {React.createElement(icon, { size: 24 })}
        </div>
    </div>
);

// Distinct from an empty/"no data yet" state: this section's fetch failed, so we
// genuinely don't know whether there's data. Offer a retry.
const SectionError = ({ onRetry, compact }) => (
    <div className={`flex flex-col items-center justify-center text-center ${compact ? 'py-6' : 'h-80'}`}>
        <AlertCircle size={compact ? 20 : 32} className="text-amber-500 mb-2" />
        <p className="text-sm text-gray-700 mb-3">Couldn&apos;t load this section</p>
        <button
            onClick={onRetry}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-blue-600 border border-blue-200 rounded-md hover:bg-blue-50 transition"
        >
            <RefreshCw size={14} />
            Retry
        </button>
    </div>
);

const TimeRangeSelector = ({ value, onChange }) => (
    <div className="flex bg-gray-50 rounded-lg p-1">
        {['7d', '30d', '90d', 'all'].map((range) => (
            <button
                key={range}
                onClick={() => onChange(range)}
                className={`px-3 py-1.5 rounded-md text-sm font-medium transition-all ${
                    value === range 
                        ? 'bg-white text-gray-900 shadow-sm' 
                        : 'text-gray-700 hover:text-gray-700'
                }`}
            >
                {range === '7d' ? '7 Days' : range === '30d' ? '30 Days' : range === '90d' ? '90 Days' : 'All Time'}
            </button>
        ))}
    </div>
);

export default function Analytics() {
    const [leadStats, setLeadStats] = useState(null);
    const [documentStats, setDocumentStats] = useState(null);
    const [inventoryStats, setInventoryStats] = useState(null);
    const [chatStats, setChatStats] = useState(null);
    const [eventStats, setEventStats] = useState(null);
    const [timeSeriesData, setTimeSeriesData] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    // Per-section load failures, so a failed section reads "couldn't load"
    // rather than silently looking like "no data yet".
    const [sectionErrors, setSectionErrors] = useState({});
    const [timeRange, setTimeRange] = useState('30d');
    const [activeTab, setActiveTab] = useState('overview');

    const fetchData = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            // Fetch all stats in parallel
            const [leadsResp, docsResp, invResp, chatResp, eventsResp] = await Promise.all([
                adminFetch(`/api/analytics/leads?range=${timeRange}`).catch(() => null),
                adminFetch(`/api/analytics/documents?range=${timeRange}`).catch(() => null),
                adminFetch('/api/analytics/inventory').catch(() => null),
                adminFetch(`/api/analytics/chat?range=${timeRange}`).catch(() => null),
                adminFetch(`/api/analytics/events?range=${timeRange}`).catch(() => null),
            ]);

            // A null response = the fetch threw; a non-ok response = the server
            // rejected it. Either way the section failed to load (distinct from
            // a successful response that simply has no data yet).
            const failed = {
                leads: !leadsResp?.ok,
                documents: !docsResp?.ok,
                inventory: !invResp?.ok,
                chat: !chatResp?.ok,
                events: !eventsResp?.ok,
            };

            if (leadsResp?.ok) {
                const data = await leadsResp.json();
                setLeadStats(data);
                setTimeSeriesData(data.time_series || []);
            }
            if (docsResp?.ok) {
                const data = await docsResp.json();
                setDocumentStats(data);
            }
            if (invResp?.ok) {
                const data = await invResp.json();
                setInventoryStats(data);
            }
            if (chatResp?.ok) {
                const data = await chatResp.json();
                setChatStats(data);
            }
            if (eventsResp?.ok) {
                const data = await eventsResp.json();
                setEventStats(data);
            }

            setSectionErrors(failed);

            // Only hard-fail the whole page if every section failed; otherwise
            // surface the failures inline so the loaded sections still render.
            if (Object.values(failed).every(Boolean)) {
                setError('Unable to load analytics data. Please try again.');
            }
        } catch (e) {
            console.error('Analytics fetch failed:', e);
            setError('Unable to load analytics data. Please try again.');
        } finally {
            setLoading(false);
        }
    }, [timeRange]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    // Calculate metrics
    const conversionRate = leadStats?.total > 0
        ? ((leadStats.with_contact_info / leadStats.total) * 100).toFixed(1)
        : '0.0';
    
    const appointmentRate = leadStats?.total > 0
        ? ((leadStats.appointment_requested / leadStats.total) * 100).toFixed(1)
        : '0.0';
    
    const financingRate = leadStats?.total > 0
        ? ((leadStats.financing_discussed / leadStats.total) * 100).toFixed(1)
        : '0.0';

    // Chart data preparation
    const statusChartData = leadStats
        ? Object.entries(leadStats.by_status || {}).map(([status, count]) => ({
            name: status.charAt(0).toUpperCase() + status.slice(1),
            value: count
        }))
        : [];

    const engagementData = leadStats
        ? [
            { label: 'Contact Info', count: leadStats.with_contact_info || 0, color: '#3B82F6' },
            { label: 'Appointments', count: leadStats.appointment_requested || 0, color: '#10B981' },
            { label: 'Financing', count: leadStats.financing_discussed || 0, color: '#F59E0B' },
        ].filter(d => d.count > 0)
        : [];

    const documentTypeData = documentStats?.by_type
        ? Object.entries(documentStats.by_type).map(([type, count]) => ({
            name: type.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()),
            value: count
        }))
        : [];

    const topHomesData = inventoryStats?.top_viewed?.slice(0, 5) || [];

    if (loading) {
        return (
            <div className="min-h-screen bg-gray-50 flex items-center justify-center">
                <div className="text-center">
                    <Loader2 className="h-10 w-10 animate-spin text-blue-600 mx-auto mb-3" />
                    <p className="text-gray-700">Loading analytics...</p>
                </div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="min-h-screen bg-gray-50 flex items-center justify-center">
                <div className="text-center">
                    <AlertCircle size={48} className="text-red-400 mx-auto mb-4" />
                    <p className="text-red-600 mb-4">{error}</p>
                    <button
                        onClick={fetchData}
                        className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition"
                    >
                        Retry
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-gray-50 p-8">
            <div className="max-w-7xl mx-auto space-y-8">

                {/* Header */}
                <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                    <div>
                        <h1 className="text-3xl font-bold text-gray-900">Analytics Dashboard</h1>
                        <p className="text-sm text-gray-700 mt-1">Real-time insights into your business</p>
                    </div>
                    <div className="flex items-center gap-3">
                        <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
                        <button
                            onClick={fetchData}
                            className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition text-sm text-gray-600"
                            aria-label="Refresh analytics data"
                        >
                            <RefreshCw size={16} />
                            Refresh
                        </button>
                    </div>
                </div>

                {/* Tab Navigation */}
                <div className="flex gap-2 border-b border-gray-200">
                    {[
                        { id: 'overview', label: 'Overview', icon: TrendingUp },
                        { id: 'leads', label: 'Leads', icon: Users },
                        { id: 'documents', label: 'Documents', icon: FileText },
                        { id: 'inventory', label: 'Inventory', icon: Home },
                    ].map(tab => (
                        <button
                            key={tab.id}
                            onClick={() => setActiveTab(tab.id)}
                            className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                                activeTab === tab.id
                                    ? 'border-blue-600 text-blue-600'
                                    : 'border-transparent text-gray-700 hover:text-gray-700'
                            }`}
                        >
                            <tab.icon size={16} />
                            {tab.label}
                        </button>
                    ))}
                </div>

                {/* OVERVIEW TAB */}
                {activeTab === 'overview' && (
                    <>
                        {/* Section-load failures — distinct from zero/empty data */}
                        {(sectionErrors.leads || sectionErrors.documents || sectionErrors.chat || sectionErrors.inventory) && (
                            <div className="flex items-center justify-between gap-4 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
                                <div className="flex items-center gap-2 text-sm text-amber-800">
                                    <AlertCircle size={18} className="shrink-0" />
                                    <span>
                                        Some sections couldn&apos;t load{' '}
                                        ({[
                                            sectionErrors.leads && 'leads',
                                            sectionErrors.documents && 'documents',
                                            sectionErrors.chat && 'chat',
                                            sectionErrors.inventory && 'inventory',
                                        ].filter(Boolean).join(', ')}). The values below may be incomplete.
                                    </span>
                                </div>
                                <button
                                    onClick={fetchData}
                                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-amber-800 border border-amber-300 rounded-md hover:bg-amber-100 transition shrink-0"
                                >
                                    <RefreshCw size={14} />
                                    Retry
                                </button>
                            </div>
                        )}

                        {/* Key Metrics */}
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                            <MetricCard
                                title="Total Leads"
                                value={leadStats?.total || 0}
                                icon={Users}
                                trend={leadStats?.trend}
                                trendUp={leadStats?.trend_up}
                                accent
                                subtitle={`${leadStats?.new_this_week || 0} new this week`}
                            />
                            <MetricCard
                                title="Documents Generated"
                                value={documentStats?.total_generated || 0}
                                icon={FileText}
                                subtitle={`${documentStats?.this_month || 0} this month`}
                            />
                            <MetricCard
                                title="Chat Conversations"
                                value={chatStats?.total_conversations || 0}
                                icon={MessageSquare}
                                subtitle={`${chatStats?.avg_per_day || 0} avg per day`}
                            />
                            <MetricCard
                                title="Homes in Inventory"
                                value={inventoryStats?.total || 0}
                                icon={Home}
                                subtitle={`${inventoryStats?.with_photos || 0} with photos`}
                            />
                        </div>

                        {/* Conversion Rates */}
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200 text-center">
                                <div className="w-12 h-12 bg-blue-100 rounded-full flex items-center justify-center mx-auto mb-3">
                                    <Target className="text-blue-600" size={24} />
                                </div>
                                <p className="text-sm text-gray-700 mb-1">Lead Conversion Rate</p>
                                <p className="text-4xl font-bold text-blue-600">{conversionRate}%</p>
                                <p className="text-xs text-gray-600 mt-1">Share contact info</p>
                            </div>
                            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200 text-center">
                                <div className="w-12 h-12 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-3">
                                    <Calendar className="text-green-600" size={24} />
                                </div>
                                <p className="text-sm text-gray-700 mb-1">Appointment Rate</p>
                                <p className="text-4xl font-bold text-green-600">{appointmentRate}%</p>
                                <p className="text-xs text-gray-600 mt-1">Request showroom visits</p>
                            </div>
                            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200 text-center">
                                <div className="w-12 h-12 bg-amber-100 rounded-full flex items-center justify-center mx-auto mb-3">
                                    <DollarSign className="text-amber-600" size={24} />
                                </div>
                                <p className="text-sm text-gray-700 mb-1">Financing Interest</p>
                                <p className="text-4xl font-bold text-amber-600">{financingRate}%</p>
                                <p className="text-xs text-gray-600 mt-1">Ask about financing</p>
                            </div>
                        </div>

                        {/* Time Series Chart */}
                        {timeSeriesData.length > 0 && (
                            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
                                <h3 className="text-lg font-bold text-gray-900 mb-6">Lead Activity Over Time</h3>
                                <div className="h-80 w-full">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <AreaChart data={timeSeriesData}>
                                            <defs>
                                                <linearGradient id="colorLeads" x1="0" y1="0" x2="0" y2="1">
                                                    <stop offset="5%" stopColor="#3B82F6" stopOpacity={0.3}/>
                                                    <stop offset="95%" stopColor="#3B82F6" stopOpacity={0}/>
                                                </linearGradient>
                                            </defs>
                                            <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                                            <XAxis 
                                                dataKey="date" 
                                                stroke="#9CA3AF"
                                                fontSize={12}
                                                tickFormatter={(value) => new Date(value).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                                            />
                                            <YAxis stroke="#9CA3AF" fontSize={12} />
                                            <Tooltip 
                                                contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)' }}
                                                formatter={(value) => [value, 'Leads']}
                                                labelFormatter={(label) => new Date(label).toLocaleDateString()}
                                            />
                                            <Area 
                                                type="monotone" 
                                                dataKey="count" 
                                                stroke="#3B82F6" 
                                                fillOpacity={1} 
                                                fill="url(#colorLeads)" 
                                                strokeWidth={2}
                                            />
                                        </AreaChart>
                                    </ResponsiveContainer>
                                </div>
                            </div>
                        )}
                    </>
                )}

                {/* LEADS TAB */}
                {activeTab === 'leads' && (
                    <>
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                            {/* Status Distribution */}
                            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
                                <h3 className="text-lg font-bold text-gray-900 mb-6">Lead Status Distribution</h3>
                                {statusChartData.length > 0 ? (
                                    <div className="h-80 w-full">
                                        <ResponsiveContainer width="100%" height="100%">
                                            <PieChart>
                                                <Pie
                                                    data={statusChartData}
                                                    cx="50%"
                                                    cy="50%"
                                                    labelLine={false}
                                                    outerRadius={120}
                                                    dataKey="value"
                                                    label={({ name, percent }) => `${name}: ${(percent * 100).toFixed(0)}%`}
                                                >
                                                    {statusChartData.map((entry, index) => (
                                                        <Cell key={`cell-${index}`} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                                                    ))}
                                                </Pie>
                                                <Tooltip />
                                            </PieChart>
                                        </ResponsiveContainer>
                                    </div>
                                ) : sectionErrors.leads ? (
                                    <SectionError onRetry={fetchData} />
                                ) : (
                                    <div className="h-80 flex items-center justify-center text-gray-600">
                                        <p>No status data available</p>
                                    </div>
                                )}
                            </div>

                            {/* Engagement Breakdown */}
                            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
                                <h3 className="text-lg font-bold text-gray-900 mb-6">Lead Engagement</h3>
                                {sectionErrors.leads && engagementData.length === 0 ? (
                                    <SectionError onRetry={fetchData} />
                                ) : (
                                <div className="h-80 w-full">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <BarChart data={engagementData} layout="vertical">
                                            <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#E5E7EB" />
                                            <XAxis type="number" axisLine={false} tickLine={false} />
                                            <YAxis 
                                                type="category" 
                                                dataKey="label" 
                                                axisLine={false} 
                                                tickLine={false} 
                                                width={100} 
                                                tick={{ fontSize: 12 }} 
                                            />
                                            <Tooltip 
                                                contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)' }}
                                            />
                                            <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                                                {engagementData.map((entry, index) => (
                                                    <Cell key={`cell-${index}`} fill={entry.color} />
                                                ))}
                                            </Bar>
                                        </BarChart>
                                    </ResponsiveContainer>
                                </div>
                                )}
                            </div>
                        </div>

                        {/* Lead Pipeline Table */}
                        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
                            <h3 className="text-lg font-bold text-gray-900 mb-4">Lead Pipeline Summary</h3>
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead>
                                        <tr className="text-left text-gray-700 border-b border-gray-200">
                                            <th className="pb-3 font-medium">Status</th>
                                            <th className="pb-3 font-medium text-right">Count</th>
                                            <th className="pb-3 font-medium text-right">% of Total</th>
                                            <th className="pb-3 font-medium text-right">Trend</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {Object.entries(leadStats?.by_status || {}).map(([status, count]) => (
                                            <tr key={status} className="border-b border-gray-50 hover:bg-gray-50 transition">
                                                <td className="py-3">
                                                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                                                        status === 'new' ? 'bg-blue-100 text-blue-700' :
                                                        status === 'contacted' ? 'bg-yellow-100 text-yellow-700' :
                                                        status === 'qualified' ? 'bg-green-100 text-green-700' :
                                                        status === 'converted' ? 'bg-purple-100 text-purple-700' :
                                                        'bg-gray-50 text-gray-700'
                                                    }`}>
                                                        <div className={`w-1.5 h-1.5 rounded-full ${
                                                            status === 'new' ? 'bg-blue-500' :
                                                            status === 'contacted' ? 'bg-yellow-500' :
                                                            status === 'qualified' ? 'bg-green-500' :
                                                            status === 'converted' ? 'bg-purple-500' :
                                                            'bg-gray-500'
                                                        }`} />
                                                        {status.charAt(0).toUpperCase() + status.slice(1)}
                                                    </span>
                                                </td>
                                                <td className="py-3 text-right font-medium">{count}</td>
                                                <td className="py-3 text-right text-gray-700">
                                                    {leadStats?.total > 0 ? ((count / leadStats.total) * 100).toFixed(1) : '0.0'}%
                                                </td>
                                                <td className="py-3 text-right">
                                                    {leadStats?.status_trends?.[status] > 0 ? (
                                                        <span className="text-green-600 text-xs">↑ {leadStats.status_trends[status]}</span>
                                                    ) : leadStats?.status_trends?.[status] < 0 ? (
                                                        <span className="text-red-600 text-xs">↓ {Math.abs(leadStats.status_trends[status])}</span>
                                                    ) : (
                                                        <span className="text-gray-600 text-xs">—</span>
                                                    )}
                                                </td>
                                            </tr>
                                        ))}
                                        {sectionErrors.leads && Object.keys(leadStats?.by_status || {}).length === 0 && (
                                            <tr>
                                                <td colSpan="4" className="py-8">
                                                    <SectionError onRetry={fetchData} compact />
                                                </td>
                                            </tr>
                                        )}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        {/* Engagement — the real client event stream (/api/analytics/events) */}
                        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
                            <h3 className="text-lg font-bold text-gray-900 mb-4">Engagement (Site Events)</h3>
                            {eventStats?.total_events > 0 ? (
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                    <div>
                                        <p className="text-sm font-medium text-gray-700 mb-2">By type ({eventStats.total_events} total)</p>
                                        <ul className="space-y-1.5">
                                            {eventStats.by_type.map((t) => (
                                                <li key={t.event} className="flex justify-between text-sm">
                                                    <span className="text-gray-700">{t.event}</span>
                                                    <span className="font-medium">{t.count}</span>
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                    <div>
                                        <p className="text-sm font-medium text-gray-700 mb-2">Top homes by interest</p>
                                        <ul className="space-y-1.5">
                                            {eventStats.top_homes.map((h) => (
                                                <li key={h.home} className="flex justify-between text-sm">
                                                    <span className="text-gray-700 truncate pr-2">{h.home}</span>
                                                    <span className="font-medium">{h.count}</span>
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                </div>
                            ) : sectionErrors.events ? (
                                <SectionError onRetry={fetchData} compact />
                            ) : (
                                <p className="text-sm text-gray-500">No site events captured yet for this period — they accrue as visitors browse homes and open forms.</p>
                            )}
                        </div>
                    </>
                )}

                {/* DOCUMENTS TAB */}
                {activeTab === 'documents' && (
                    <>
                        {sectionErrors.documents && (
                            <div className="flex items-center justify-between gap-4 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
                                <div className="flex items-center gap-2 text-sm text-amber-800">
                                    <AlertCircle size={18} className="shrink-0" />
                                    <span>Couldn&apos;t load document analytics. The values below may be incomplete.</span>
                                </div>
                                <button
                                    onClick={fetchData}
                                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-amber-800 border border-amber-300 rounded-md hover:bg-amber-100 transition shrink-0"
                                >
                                    <RefreshCw size={14} />
                                    Retry
                                </button>
                            </div>
                        )}
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                            <MetricCard
                                title="Total Documents"
                                value={documentStats?.total_generated || 0}
                                icon={FileText}
                                subtitle="All time"
                            />
                            <MetricCard
                                title="This Month"
                                value={documentStats?.this_month || 0}
                                icon={Calendar}
                                subtitle={`${documentStats?.pages_this_month?.toLocaleString() || 0} pages`}
                            />
                            <MetricCard
                                title="Most Popular"
                                value={documentStats?.most_popular?.name || '—'}
                                icon={TrendingUp}
                                subtitle={`${documentStats?.most_popular?.count || 0} generated`}
                            />
                        </div>

                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                            {/* Document Types */}
                            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
                                <h3 className="text-lg font-bold text-gray-900 mb-6">Documents by Type</h3>
                                {documentTypeData.length > 0 ? (
                                    <div className="h-80 w-full">
                                        <ResponsiveContainer width="100%" height="100%">
                                            <BarChart data={documentTypeData}>
                                                <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                                                <XAxis dataKey="name" stroke="#9CA3AF" fontSize={11} interval={0} angle={-45} textAnchor="end" height={80} />
                                                <YAxis stroke="#9CA3AF" fontSize={12} />
                                                <Tooltip contentStyle={{ borderRadius: '8px', border: 'none' }} />
                                                <Bar dataKey="value" fill="#3B82F6" radius={[4, 4, 0, 0]} />
                                            </BarChart>
                                        </ResponsiveContainer>
                                    </div>
                                ) : sectionErrors.documents ? (
                                    <SectionError onRetry={fetchData} />
                                ) : (
                                    <div className="h-80 flex items-center justify-center text-gray-600">
                                        <p>No document data available</p>
                                    </div>
                                )}
                            </div>

                            {/* Recent Activity */}
                            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
                                <h3 className="text-lg font-bold text-gray-900 mb-6">Recent Document Activity</h3>
                                <div className="space-y-4">
                                    {(documentStats?.recent || []).slice(0, 5).map((doc, i) => (
                                        <div key={i} className="flex items-center gap-4 p-3 bg-gray-50 rounded-lg">
                                            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
                                                <FileText size={20} className="text-blue-600" />
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <p className="font-medium text-gray-900 truncate">{doc.template_name}</p>
                                                <p className="text-sm text-gray-700">{doc.buyer_name}</p>
                                            </div>
                                            <span className="text-xs text-gray-600">
                                                {new Date(doc.created_at).toLocaleDateString()}
                                            </span>
                                        </div>
                                    ))}
                                    {(!documentStats?.recent || documentStats.recent.length === 0) && (
                                        sectionErrors.documents ? (
                                            <SectionError onRetry={fetchData} compact />
                                        ) : (
                                            <p className="text-center text-gray-600 py-8">No recent activity</p>
                                        )
                                    )}
                                </div>
                            </div>
                        </div>
                    </>
                )}

                {/* INVENTORY TAB */}
                {activeTab === 'inventory' && (
                    <>
                        {sectionErrors.inventory && (
                            <div className="flex items-center justify-between gap-4 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
                                <div className="flex items-center gap-2 text-sm text-amber-800">
                                    <AlertCircle size={18} className="shrink-0" />
                                    <span>Couldn&apos;t load inventory analytics. The values below may be incomplete.</span>
                                </div>
                                <button
                                    onClick={fetchData}
                                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-amber-800 border border-amber-300 rounded-md hover:bg-amber-100 transition shrink-0"
                                >
                                    <RefreshCw size={14} />
                                    Retry
                                </button>
                            </div>
                        )}
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                            <MetricCard
                                title="Total Homes"
                                value={inventoryStats?.total || 0}
                                icon={Home}
                                accent
                            />
                            <MetricCard
                                title="New Homes"
                                value={inventoryStats?.new_count || 0}
                                icon={Zap}
                                subtitle={`${inventoryStats?.new_with_photos || 0} with photos`}
                            />
                            <MetricCard
                                title="Pre-Owned"
                                value={inventoryStats?.used_count || 0}
                                icon={Clock}
                                subtitle={`${inventoryStats?.used_with_photos || 0} with photos`}
                            />
                            <MetricCard
                                title="With 3D Tours"
                                value={inventoryStats?.with_tours || 0}
                                icon={Target}
                            />
                        </div>

                        {/* Top Viewed Homes */}
                        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
                            <h3 className="text-lg font-bold text-gray-900 mb-4">Most Viewed Homes</h3>
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead>
                                        <tr className="text-left text-gray-700 border-b border-gray-200">
                                            <th className="pb-3 font-medium">Home</th>
                                            <th className="pb-3 font-medium">Manufacturer</th>
                                            <th className="pb-3 font-medium text-right">Views</th>
                                            <th className="pb-3 font-medium text-right">Inquiries</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {topHomesData.map((home, i) => (
                                            <tr key={i} className="border-b border-gray-50 hover:bg-gray-50 transition">
                                                <td className="py-3">
                                                    <div className="flex items-center gap-3">
                                                        {home.image_url ? (
                                                        <img
                                                            src={home.image_url}
                                                            srcSet={generateSrcSet(home.image_url)}
                                                            sizes={getImageSizes('analytics-table')}
                                                            alt=""
                                                            className="w-10 h-10 rounded-lg object-cover"
                                                            loading="lazy"
                                                            decoding="async"
                                                        />
                                                        ) : (
                                                            <div className="w-10 h-10 bg-gray-50 rounded-lg flex items-center justify-center">
                                                                <Home size={16} className="text-gray-600" />
                                                            </div>
                                                        )}
                                                        <span className="font-medium">{home.model_name}</span>
                                                    </div>
                                                </td>
                                                <td className="py-3 text-gray-700">{home.manufacturer}</td>
                                                <td className="py-3 text-right font-medium">{home.view_count?.toLocaleString()}</td>
                                                <td className="py-3 text-right">{home.inquiry_count || 0}</td>
                                            </tr>
                                        ))}
                                        {topHomesData.length === 0 && (
                                            <tr>
                                                <td colSpan="4" className="py-8">
                                                    {sectionErrors.inventory ? (
                                                        <SectionError onRetry={fetchData} compact />
                                                    ) : (
                                                        <p className="text-center text-gray-600">No view data available</p>
                                                    )}
                                                </td>
                                            </tr>
                                        )}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </>
                )}

            </div>
        </div>
    );
}
