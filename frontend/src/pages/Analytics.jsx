import React, { useState, useEffect } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { Users, Search, MessageSquare, TrendingUp, ArrowUp } from 'lucide-react';

// Mock data (replace with API call later)
const MOCK_DATA = {
    overview: {
        total_sessions: 1243,
        avg_duration: "4m 12s",
        search_count: 892,
        lead_conversion_rate: "3.8%"
    },
    top_searches: [
        { query: "3 bedroom double wide", count: 145 },
        { query: "land home packages", count: 98 },
        { query: "under $100k", count: 76 },
        { query: "The Nassau", count: 42 },
        { query: "financing options", count: 35 }
    ],
    daily_activity: [
        { date: "Mon", sessions: 24 },
        { date: "Tue", sessions: 35 },
        { date: "Wed", sessions: 18 },
        { date: "Thu", sessions: 42 },
        { date: "Fri", sessions: 55 },
        { date: "Sat", sessions: 67 },
        { date: "Sun", sessions: 48 },
    ]
};

const MetricCard = ({ title, value, icon: Icon, trend }) => (
    <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex items-start justify-between">
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
        <div className="p-3 bg-blue-50 rounded-lg text-blue-600">
            <Icon size={24} />
        </div>
    </div>
);

export default function Analytics() {
    const [data, setData] = useState(MOCK_DATA);

    // In a real implementation, fetch data here
    // useEffect(() => { fetch('/api/analytics').then... }, []);

    return (
        <div className="min-h-screen bg-gray-50 p-8">
            <div className="max-w-7xl mx-auto space-y-8">

                {/* Header */}
                <div className="flex justify-between items-center">
                    <h1 className="text-3xl font-bold text-gray-900">Analytics Dashboard</h1>
                    <div className="text-sm text-gray-500">Last updated: Just now</div>
                </div>

                {/* Metrics Grid */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                    <MetricCard
                        title="Total Sessions"
                        value={data.overview.total_sessions}
                        icon={Users}
                        trend="12% vs last week"
                    />
                    <MetricCard
                        title="Avg Session Duration"
                        value={data.overview.avg_duration}
                        icon={TrendingUp}
                    />
                    <MetricCard
                        title="Total Searches"
                        value={data.overview.search_count}
                        icon={Search}
                        trend="5% vs last week"
                    />
                    <MetricCard
                        title="Lead Conversion"
                        value={data.overview.lead_conversion_rate}
                        icon={MessageSquare}
                        trend="0.5% vs last week"
                    />
                </div>

                {/* Charts Area */}
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">

                    {/* Activity Chart */}
                    <div className="lg:col-span-2 bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                        <h3 className="text-lg font-bold text-gray-900 mb-6">Weekly Activity</h3>
                        <div className="h-80 w-full">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={data.daily_activity}>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                                    <XAxis dataKey="date" axisLine={false} tickLine={false} />
                                    <YAxis axisLine={false} tickLine={false} />
                                    <Tooltip
                                        cursor={{ fill: '#F9FAFB' }}
                                        contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)' }}
                                    />
                                    <Bar dataKey="sessions" fill="#3B82F6" radius={[4, 4, 0, 0]} />
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    </div>

                    {/* Top Searches */}
                    <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                        <h3 className="text-lg font-bold text-gray-900 mb-6">Top Searches</h3>
                        <div className="space-y-4">
                            {data.top_searches.map((item, index) => (
                                <div key={index} className="flex items-center justify-between p-2 hover:bg-gray-50 rounded-lg transition-colors">
                                    <span className="text-gray-700 font-medium">{item.query}</span>
                                    <span className="text-sm text-gray-500 bg-gray-100 px-2 py-1 rounded-full">
                                        {item.count}
                                    </span>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>

            </div>
        </div>
    );
}
