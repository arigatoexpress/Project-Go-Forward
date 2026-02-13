import React, { useState, useEffect } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, PieChart, Pie, Cell } from 'recharts';
import { Users, Search, MessageSquare, TrendingUp, ArrowUp, Phone, Calendar, DollarSign, Loader2, RefreshCw, AlertCircle } from 'lucide-react';

const PIE_COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6'];

const MetricCard = ({ title, value, icon: Icon, trend, accent }) => (
    <div className={`bg-white p-6 rounded-xl shadow-sm border ${accent ? 'border-blue-200 bg-blue-50' : 'border-gray-100'} flex items-start justify-between`}>
        <div>
            <p className="text-sm font-medium text-gray-500 mb-1">{title}</p>
            <h3 className="text-2xl font-bold text-gray-900">{value}</h3>
            {trend && (
                <div className="flex items-center mt-2 text-sm text-green-600">
                    <ArrowUp size={14} className="mr-1" />
                    <span>{trend}</span>
                </div>
            )}
        </div>
        <div className={`p-3 ${accent ? 'bg-blue-100' : 'bg-blue-50'} rounded-lg text-blue-600`}>
            <Icon size={24} />
        </div>
    </div>
);

export default function Analytics() {
    const [leadStats, setLeadStats] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const fetchData = async () => {
        setLoading(true);
        setError('');
        try {
            const resp = await fetch('/leads/stats');
            if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            setLeadStats(data);
        } catch (e) {
            console.error('Analytics fetch failed:', e);
            setError('Unable to load analytics data. Please try again.');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchData();
    }, []);

    // Derive chart data from real stats
    const statusChartData = leadStats
        ? Object.entries(leadStats.by_status).map(([status, count]) => ({
            name: status.charAt(0).toUpperCase() + status.slice(1),
            value: count
        }))
        : [];

    const engagementData = leadStats
        ? [
            { label: 'Contact Info Shared', count: leadStats.with_contact_info },
            { label: 'Appointment Requested', count: leadStats.appointment_requested },
            { label: 'Financing Discussed', count: leadStats.financing_discussed },
            { label: 'No Engagement Yet', count: Math.max(0, leadStats.total - leadStats.with_contact_info - leadStats.appointment_requested) },
        ].filter(d => d.count > 0)
        : [];

    const conversionRate = leadStats && leadStats.total > 0
        ? ((leadStats.with_contact_info / leadStats.total) * 100).toFixed(1)
        : '0.0';

    if (loading) {
        return (
            <div className="min-h-screen bg-gray-50 flex items-center justify-center">
                <div className="text-center">
                    <Loader2 className="h-10 w-10 animate-spin text-blue-600 mx-auto mb-3" />
                    <p className="text-gray-500">Loading analytics...</p>
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
                <div className="flex justify-between items-center">
                    <div>
                        <h1 className="text-3xl font-bold text-gray-900">Analytics Dashboard</h1>
                        <p className="text-sm text-gray-500 mt-1">Live data from Firestore leads</p>
                    </div>
                    <button
                        onClick={fetchData}
                        className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition text-sm text-gray-600"
                        aria-label="Refresh analytics data"
                    >
                        <RefreshCw size={16} />
                        Refresh
                    </button>
                </div>

                {/* Metrics Grid */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                    <MetricCard
                        title="Total Leads"
                        value={leadStats.total}
                        icon={Users}
                        accent
                    />
                    <MetricCard
                        title="With Contact Info"
                        value={leadStats.with_contact_info}
                        icon={Phone}
                    />
                    <MetricCard
                        title="Appointments Requested"
                        value={leadStats.appointment_requested}
                        icon={Calendar}
                    />
                    <MetricCard
                        title="Financing Discussed"
                        value={leadStats.financing_discussed}
                        icon={DollarSign}
                    />
                </div>

                {/* Conversion + Lead Quality */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 text-center">
                        <p className="text-sm text-gray-500 mb-2">Lead Conversion Rate</p>
                        <p className="text-4xl font-bold text-blue-600">{conversionRate}%</p>
                        <p className="text-xs text-gray-400 mt-1">Leads that shared contact info</p>
                    </div>
                    <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 text-center">
                        <p className="text-sm text-gray-500 mb-2">Appointment Intent Rate</p>
                        <p className="text-4xl font-bold text-green-600">
                            {leadStats.total > 0 ? ((leadStats.appointment_requested / leadStats.total) * 100).toFixed(1) : '0.0'}%
                        </p>
                        <p className="text-xs text-gray-400 mt-1">Leads requesting showroom visits</p>
                    </div>
                    <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 text-center">
                        <p className="text-sm text-gray-500 mb-2">Financing Interest Rate</p>
                        <p className="text-4xl font-bold text-amber-600">
                            {leadStats.total > 0 ? ((leadStats.financing_discussed / leadStats.total) * 100).toFixed(1) : '0.0'}%
                        </p>
                        <p className="text-xs text-gray-400 mt-1">Leads asking about financing</p>
                    </div>
                </div>

                {/* Charts Area */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">

                    {/* Engagement Breakdown */}
                    <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                        <h3 className="text-lg font-bold text-gray-900 mb-6">Lead Engagement Breakdown</h3>
                        <div className="h-72 w-full">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={engagementData} layout="vertical">
                                    <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                                    <XAxis type="number" axisLine={false} tickLine={false} />
                                    <YAxis type="category" dataKey="label" axisLine={false} tickLine={false} width={140} tick={{ fontSize: 12 }} />
                                    <Tooltip
                                        contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)' }}
                                    />
                                    <Bar dataKey="count" fill="#3B82F6" radius={[0, 4, 4, 0]} />
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    </div>

                    {/* Status Distribution */}
                    <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                        <h3 className="text-lg font-bold text-gray-900 mb-6">Lead Status Distribution</h3>
                        {statusChartData.length > 0 ? (
                            <div className="h-72 w-full flex items-center justify-center">
                                <ResponsiveContainer width="100%" height="100%">
                                    <PieChart>
                                        <Pie
                                            data={statusChartData}
                                            cx="50%"
                                            cy="50%"
                                            labelLine={false}
                                            outerRadius={100}
                                            dataKey="value"
                                            label={({ name, value, percent }) => `${name}: ${value} (${(percent * 100).toFixed(0)}%)`}
                                        >
                                            {statusChartData.map((entry, index) => (
                                                <Cell key={`cell-${index}`} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                                            ))}
                                        </Pie>
                                        <Tooltip />
                                    </PieChart>
                                </ResponsiveContainer>
                            </div>
                        ) : (
                            <div className="h-72 flex items-center justify-center text-gray-400">
                                <p>No status data available</p>
                            </div>
                        )}
                    </div>
                </div>

                {/* Quick Stats Table */}
                <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                    <h3 className="text-lg font-bold text-gray-900 mb-4">Lead Pipeline Summary</h3>
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="text-left text-gray-500 border-b border-gray-100">
                                    <th className="pb-3 font-medium">Status</th>
                                    <th className="pb-3 font-medium text-right">Count</th>
                                    <th className="pb-3 font-medium text-right">% of Total</th>
                                </tr>
                            </thead>
                            <tbody>
                                {Object.entries(leadStats.by_status).map(([status, count]) => (
                                    <tr key={status} className="border-b border-gray-50 hover:bg-gray-50 transition">
                                        <td className="py-3">
                                            <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
                                                status === 'new' ? 'bg-blue-100 text-blue-700' :
                                                status === 'contacted' ? 'bg-yellow-100 text-yellow-700' :
                                                status === 'qualified' ? 'bg-green-100 text-green-700' :
                                                status === 'converted' ? 'bg-purple-100 text-purple-700' :
                                                'bg-gray-100 text-gray-700'
                                            }`}>
                                                {status.charAt(0).toUpperCase() + status.slice(1)}
                                            </span>
                                        </td>
                                        <td className="py-3 text-right font-medium">{count}</td>
                                        <td className="py-3 text-right text-gray-500">
                                            {leadStats.total > 0 ? ((count / leadStats.total) * 100).toFixed(1) : '0.0'}%
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>

            </div>
        </div>
    );
}
