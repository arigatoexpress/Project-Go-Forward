import React, { useState, useEffect } from 'react';
import {
    Sparkles, Film, TrendingUp, Calendar, BarChart3, X,
    ArrowLeft, Copy, Check, ChevronDown, Loader2,
    Play, Clock, Hash, Zap, Home, MessageCircle,
    Flame, DollarSign, AlertTriangle, Clapperboard,
    BookOpen, Users, RefreshCw, Send, Eye
} from 'lucide-react';
import './AdStudio.css';

/* ─────────────────── constants ─────────────────── */
const PLATFORMS = [
    {
        id: 'tiktok',
        name: 'TikTok',
        icon: '♪',
        specs: '9:16 • 15-60s',
        color: '#000',
        border: '#25f4ee',
        gradient: 'linear-gradient(135deg, #010101 0%, #161823 100%)'
    },
    {
        id: 'instagram_reels',
        name: 'Instagram Reels',
        icon: '◎',
        specs: '9:16 • 15-90s',
        color: '#833AB4',
        border: '#E1306C',
        gradient: 'linear-gradient(135deg, #833AB4 0%, #E1306C 50%, #F77737 100%)'
    },
    {
        id: 'facebook',
        name: 'Facebook',
        icon: 'f',
        specs: '16:9 or 9:16 • 30-240s',
        color: '#1877F2',
        border: '#1877F2',
        gradient: 'linear-gradient(135deg, #1877F2 0%, #0b5fcc 100%)'
    }
];

const THEMES = [
    { id: 'home_tour', label: 'Home Tour', icon: <Home size={14} /> },
    { id: 'myth_busting', label: 'Myth Busting', icon: <Zap size={14} /> },
    { id: 'financing_tips', label: 'Financing Tips', icon: <DollarSign size={14} /> },
    { id: 'clearance_alert', label: 'Clearance Alert', icon: <AlertTriangle size={14} /> },
    { id: 'behind_scenes', label: 'Behind Scenes', icon: <Clapperboard size={14} /> },
    { id: 'customer_story', label: 'Customer Story', icon: <Users size={14} /> },
    { id: 'comparison', label: 'Comparison', icon: <BarChart3 size={14} /> },
    { id: 'faq', label: 'FAQ', icon: <MessageCircle size={14} /> }
];

const TABS = [
    { id: 'create', label: 'Create Ad', icon: <Film size={18} /> },
    { id: 'ideas', label: 'Content Ideas', icon: <TrendingUp size={18} /> },
    { id: 'scheduled', label: 'Scheduled', icon: <Calendar size={18} /> },
    { id: 'analytics', label: 'Analytics', icon: <BarChart3 size={18} /> }
];

const AVATARS = [
    { id: 'tex_classic', name: 'Classic Tex', icon: '🤠', description: 'Friendly & Traditional' },
    { id: 'tex_modern', name: 'Modern Tex', icon: '✨', description: 'Sleek & Professional' },
    { id: 'tex_custom', name: 'Custom Avatar', icon: '🎨', description: 'Prompt your own style' }
];

const LANGUAGES = [
    { id: 'en', name: 'English', flag: '🇺🇸' },
    { id: 'es', name: 'Spanish', flag: '🇲🇽' }
];

/* ─────────────────── API helpers ─────────────────── */
async function apiGenerateScript(params) {
    const resp = await fetch('/api/marketing/generate-script', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params)
    });
    if (!resp.ok) throw new Error('Script generation failed');
    return resp.json();
}

async function apiGetIdeas() {
    const resp = await fetch('/api/marketing/trending-ideas');
    if (!resp.ok) throw new Error('Failed to load ideas');
    return resp.json();
}

async function apiSchedulePost(params) {
    const resp = await fetch('/api/marketing/schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params)
    });
    if (!resp.ok) throw new Error('Scheduling failed');
    return resp.json();
}

async function apiGetAnalytics() {
    const resp = await fetch('/api/marketing/analytics');
    if (!resp.ok) throw new Error('Analytics load failed');
    return resp.json();
}

/* ─────────────────── Component ─────────────────── */
export default function AdStudio({ onBack }) {
    // Tabs
    const [activeTab, setActiveTab] = useState('create');

    // Create tab state
    const [platform, setPlatform] = useState('tiktok');
    const [theme, setTheme] = useState('home_tour');
    const [homeName, setHomeName] = useState('');
    const [homePrice, setHomePrice] = useState('');
    const [customHook, setCustomHook] = useState('');
    const [language, setLanguage] = useState('en');
    const [avatar, setAvatar] = useState('tex_classic');
    const [customAvatarPrompt, setCustomAvatarPrompt] = useState('');
    const [showPreview, setShowPreview] = useState(false);
    const [generating, setGenerating] = useState(false);
    const [script, setScript] = useState(null);
    const [copied, setCopied] = useState(false);

    // Ideas tab
    const [ideas, setIdeas] = useState(null);
    const [loadingIdeas, setLoadingIdeas] = useState(false);

    // Scheduled tab
    const [scheduledPosts, setScheduledPosts] = useState([]);
    const [scheduling, setScheduling] = useState(false);

    // Analytics tab
    const [analytics, setAnalytics] = useState(null);
    const [loadingAnalytics, setLoadingAnalytics] = useState(false);

    /* ─── handlers ─── */
    const handleGenerate = async () => {
        setGenerating(true);
        setScript(null);
        setShowPreview(false);
        try {
            const result = await apiGenerateScript({
                platform,
                content_theme: theme,
                home_name: homeName || undefined,
                home_price: homePrice || undefined,
                custom_hook: customHook || undefined,
                language: language,
                avatar: avatar,
                custom_avatar_prompt: avatar === 'tex_custom' ? customAvatarPrompt : undefined
            });
            // Check for API-level error (200 response but success: false)
            if (result.error || result.success === false) {
                setScript({ error: result.error || 'Script generation failed. Please try again.' });
            } else {
                setScript(result);
                setShowPreview(true);
            }
        } catch (err) {
            setScript({ error: err.message });
        } finally {
            setGenerating(false);
        }
    };

    const handleCopyScript = () => {
        if (!script?.script) return;
        const text = `${script.script.hook}\n\n${script.script.body}\n\n${script.script.cta}\n\n${(script.hashtags || []).join(' ')}`;
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const handleLoadIdeas = async () => {
        setLoadingIdeas(true);
        try {
            const result = await apiGetIdeas();
            setIdeas(result);
        } catch (err) {
            setIdeas({ error: err.message });
        } finally {
            setLoadingIdeas(false);
        }
    };

    const handleSchedule = async () => {
        if (!script) return;
        setScheduling(true);
        try {
            const result = await apiSchedulePost({
                platform: script.platform,
                content_type: 'video',
                script_id: script.script_id,
                caption: script.script?.cta,
                hashtags: script.hashtags
            });
            setScheduledPosts(prev => [result, ...prev]);
        } catch (err) {
            console.error('Schedule failed:', err);
        } finally {
            setScheduling(false);
        }
    };

    const handleLoadAnalytics = async () => {
        setLoadingAnalytics(true);
        try {
            const result = await apiGetAnalytics();
            setAnalytics(result);
        } catch (err) {
            setAnalytics({ error: err.message });
        } finally {
            setLoadingAnalytics(false);
        }
    };

    // Auto-load data when switching tabs
    useEffect(() => {
        if (activeTab === 'ideas' && !ideas) handleLoadIdeas();
        if (activeTab === 'analytics' && !analytics) handleLoadAnalytics();
    }, [activeTab]);

    /* ─── render helpers ─── */
    const selectedPlatform = PLATFORMS.find(p => p.id === platform);

    const renderPreview = () => (
        <div className="tho-preview-layer animate-in fade-in zoom-in duration-300">
            <div className="tho-preview-header">
                <h3 className="tho-preview-title">Ad Content Preview</h3>
                <button className="tho-close-preview" onClick={() => setShowPreview(false)}>
                    <X size={20} />
                </button>
            </div>

            <div className="tho-preview-body">
                <div className="tho-preview-phone">
                    <div className="tho-phone-screen">
                        <div className="tho-phone-content">
                            {/* Avatar Visualization */}
                            <div className="tho-preview-avatar-overlay">
                                <div className="tho-preview-avatar-circle">
                                    <span className="tho-avatar-emoji">
                                        {avatar === 'tex_classic' ? '🤠' : avatar === 'tex_modern' ? '✨' : '🎨'}
                                    </span>
                                </div>
                                <div className="tho-preview-avatar-badge">Tex's AI</div>
                            </div>

                            {/* Script Overlay */}
                            <div className="tho-preview-text-overlay">
                                <div className="tho-hook-badge">HOOK</div>
                                <p className="tho-preview-hook">{script?.script?.hook}</p>
                                <div className="tho-body-scroll">
                                    <p className="tho-preview-body-text">{script?.script?.body}</p>
                                </div>
                                <div className="tho-cta-box">
                                    <span className="tho-preview-cta">{script?.script?.cta}</span>
                                </div>
                            </div>

                            {/* Platform specific elements */}
                            <div className="tho-platform-side-icons">
                                <div className="tho-side-icon">❤️</div>
                                <div className="tho-side-icon">💬</div>
                                <div className="tho-side-icon">🔖</div>
                                <div className="tho-side-icon">↪️</div>
                            </div>
                        </div>
                        <div className="tho-phone-home-bar" />
                    </div>
                </div>

                <div className="tho-preview-controls">
                    <div className="tho-ai-feedback-box">
                        <h4>💡 Prompt Improvement</h4>
                        <p className="text-xs text-gray-400 mb-2">Want to tweak something? Ask Tex to rewrite parts of the script below.</p>
                        <textarea
                            className="tho-input tho-textarea"
                            placeholder="e.g., Make the hook catchier for first-time buyers..."
                            rows={3}
                        />
                        <button className="tho-btn tho-btn-secondary w-full mt-2" onClick={() => handleGenerate()}>
                            Regenerate with Feedback
                        </button>
                    </div>

                    <div className="tho-post-actions mt-6">
                        <button className="tho-btn tho-btn-primary w-full flex items-center justify-center gap-2" onClick={() => setActiveTab('scheduled')}>
                            <Send size={18} />
                            Looks Good! Continue to Post
                        </button>
                        <button className="tho-btn tho-btn-ghost w-full mt-2" onClick={() => setShowPreview(false)}>
                            Back to Editor
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );

    const renderCreate = () => (
        <div className="tho-create-layout">
            {showPreview && script && script.script && !script.error && renderPreview()}
            <div className="tho-create-form">
                {/* Step 0: Avatar & Language (New Features) */}
                <div className="tho-feature-row">
                    <div className="tho-card tho-flex-1">
                        <div className="tho-step-label">AVATAR</div>
                        <h3 className="tho-card-title">Select Presenter</h3>
                        <div className="tho-avatar-grid">
                            {AVATARS.map(a => (
                                <button
                                    key={a.id}
                                    className={`tho-avatar-btn ${avatar === a.id ? 'active' : ''}`}
                                    onClick={() => setAvatar(a.id)}
                                >
                                    <span className="tho-avatar-icon">{a.icon}</span>
                                    <div className="tho-avatar-info">
                                        <span className="tho-avatar-name">{a.name}</span>
                                        <span className="tho-avatar-desc">{a.description}</span>
                                    </div>
                                </button>
                            ))}
                        </div>
                        {avatar === 'tex_custom' && (
                            <div className="tho-custom-avatar-box">
                                <textarea
                                    placeholder="Describe your custom avatar (e.g. 'A futuristic robot in a cowboy hat')..."
                                    value={customAvatarPrompt}
                                    onChange={e => setCustomAvatarPrompt(e.target.value)}
                                    className="tho-textarea tho-small"
                                    rows={2}
                                />
                            </div>
                        )}
                    </div>

                    <div className="tho-card tho-lang-card">
                        <div className="tho-step-label">LANGUAGE</div>
                        <h3 className="tho-card-title">Ad Language</h3>
                        <div className="tho-lang-pills">
                            {LANGUAGES.map(l => (
                                <button
                                    key={l.id}
                                    className={`tho-lang-pill ${language === l.id ? 'active' : ''}`}
                                    onClick={() => setLanguage(l.id)}
                                >
                                    <span className="tho-lang-flag">{l.flag}</span>
                                    <span>{l.name}</span>
                                </button>
                            ))}
                        </div>
                    </div>
                </div>

                {/* Step 1: Platform */}
                <div className="tho-card">
                    <div className="tho-step-label">STEP 1</div>
                    <h3 className="tho-card-title">Choose Platform</h3>
                    <div className="tho-platform-grid">
                        {PLATFORMS.map(p => (
                            <button
                                key={p.id}
                                className={`tho-platform-btn ${platform === p.id ? 'active' : ''}`}
                                onClick={() => setPlatform(p.id)}
                                style={{
                                    '--platform-gradient': p.gradient,
                                    '--platform-border': p.border
                                }}
                            >
                                <span className="tho-platform-icon">{p.icon}</span>
                                <span className="tho-platform-name">{p.name}</span>
                                <span className="tho-platform-specs">{p.specs}</span>
                            </button>
                        ))}
                    </div>
                </div>

                {/* Step 2: Theme */}
                <div className="tho-card">
                    <div className="tho-step-label">STEP 2</div>
                    <h3 className="tho-card-title">Content Theme</h3>
                    <div className="tho-theme-pills">
                        {THEMES.map(t => (
                            <button
                                key={t.id}
                                className={`tho-theme-pill ${theme === t.id ? 'active' : ''}`}
                                onClick={() => setTheme(t.id)}
                            >
                                {t.icon}
                                <span>{t.label}</span>
                            </button>
                        ))}
                    </div>
                </div>

                {/* Step 3: Home details */}
                <div className="tho-card">
                    <div className="tho-step-label">STEP 3</div>
                    <h3 className="tho-card-title">Feature a Home <span className="tho-optional">(Optional)</span></h3>
                    <div className="tho-input-group">
                        <input
                            type="text"
                            placeholder="Home model name (e.g. The Nassau)"
                            value={homeName}
                            onChange={e => setHomeName(e.target.value)}
                            className="tho-input"
                        />
                        <input
                            type="text"
                            placeholder="Price (e.g. $89,900)"
                            value={homePrice}
                            onChange={e => setHomePrice(e.target.value)}
                            className="tho-input"
                        />
                    </div>
                </div>

                {/* Step 4: Custom hook */}
                <div className="tho-card">
                    <div className="tho-step-label">STEP 4</div>
                    <h3 className="tho-card-title">Custom Hook <span className="tho-optional">(Optional)</span></h3>
                    <textarea
                        placeholder="Write a custom opening hook, or leave blank for Tex to pick one..."
                        value={customHook}
                        onChange={e => setCustomHook(e.target.value)}
                        className="tho-textarea"
                        rows={2}
                    />
                </div>

                {/* Generate button */}
                <button
                    className="tho-generate-btn"
                    onClick={handleGenerate}
                    disabled={generating}
                >
                    {generating ? (
                        <><Loader2 size={18} className="spin" /> Generating...</>
                    ) : (
                        <><Sparkles size={18} /> Generate Script</>
                    )}
                </button>
            </div>

            {/* Right: Preview */}
            <div className="tho-preview-panel">
                <div className="tho-phone-frame">
                    <div className="tho-phone-notch" />
                    <div className="tho-phone-screen">
                        {script && !script.error ? (
                            <div className="tho-script-preview">
                                <div className="tho-preview-platform">
                                    <span className="tho-preview-platform-icon">{selectedPlatform?.icon}</span>
                                    <span>{selectedPlatform?.name}</span>
                                </div>

                                <div className="tho-script-section">
                                    <div className="tho-script-label">
                                        <Flame size={12} /> HOOK
                                    </div>
                                    <p className="tho-script-hook">{script?.script?.hook}</p>
                                </div>

                                <div className="tho-script-section">
                                    <div className="tho-script-label">
                                        <Play size={12} /> SCRIPT
                                    </div>
                                    <pre className="tho-script-body">{script?.script?.body}</pre>
                                </div>

                                <div className="tho-script-section">
                                    <div className="tho-script-label">
                                        <Send size={12} /> CTA
                                    </div>
                                    <p className="tho-script-cta">{script?.script?.cta}</p>
                                </div>

                                <div className="tho-script-section">
                                    <div className="tho-script-label">
                                        <Hash size={12} /> HASHTAGS
                                    </div>
                                    <div className="tho-hashtags">
                                        {(script?.hashtags || []).map((tag, i) => (
                                            <span key={i} className="tho-hashtag">{tag}</span>
                                        ))}
                                    </div>
                                </div>

                                <div className="tho-script-meta">
                                    <span><Clock size={12} /> {script?.script?.duration_estimate}</span>
                                    <span className="tho-script-id">{script?.script_id}</span>
                                </div>
                            </div>
                        ) : script?.error ? (
                            <div className="tho-preview-empty tho-preview-error">
                                <AlertTriangle size={32} />
                                <p>{script.error}</p>
                                <p className="tho-preview-hint">Check your connection and try again</p>
                            </div>
                        ) : (
                            <div className="tho-preview-empty">
                                <Film size={48} />
                                <p>Your script will appear here</p>
                                <p className="tho-preview-hint">Choose your settings and hit Generate</p>
                            </div>
                        )}
                    </div>
                </div>

                {/* Action buttons below phone */}
                {script && !script.error && (
                    <div className="tho-preview-actions">
                        <button className="tho-action-btn" onClick={handleCopyScript}>
                            {copied ? <><Check size={14} /> Copied!</> : <><Copy size={14} /> Copy Script</>}
                        </button>
                        <button
                            className="tho-action-btn tho-action-primary"
                            onClick={handleSchedule}
                            disabled={scheduling}
                        >
                            {scheduling ? <><Loader2 size={14} className="spin" /> Scheduling...</> : <><Calendar size={14} /> Schedule Post</>}
                        </button>
                        <button className="tho-action-btn" onClick={handleGenerate}>
                            <RefreshCw size={14} /> Regenerate
                        </button>
                    </div>
                )}
            </div>
        </div>
    );

    const renderIdeas = () => (
        <div className="tho-ideas-page">
            <div className="tho-ideas-header">
                <div>
                    <h2 className="tho-page-title">🔥 Trending Content Ideas</h2>
                    <p className="tho-page-subtitle">AI-generated ideas based on your inventory and trends</p>
                </div>
                <button className="tho-refresh-btn" onClick={handleLoadIdeas} disabled={loadingIdeas}>
                    {loadingIdeas ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
                    Refresh
                </button>
            </div>

            {loadingIdeas && !ideas && (
                <div className="tho-loading">
                    <Loader2 size={32} className="spin" />
                    <p>Tex is analyzing trends...</p>
                </div>
            )}

            {ideas && !ideas.error && (
                <>
                    {/* Schedule info */}
                    {ideas.recommended_posting_schedule && (
                        <div className="tho-schedule-banner">
                            <Calendar size={16} />
                            <div>
                                <strong>Recommended Posting:</strong>
                                <span> TikTok: {ideas.recommended_posting_schedule.tiktok}</span>
                                <span> • IG: {ideas.recommended_posting_schedule.instagram_reels}</span>
                                <span> • FB: {ideas.recommended_posting_schedule.facebook}</span>
                            </div>
                        </div>
                    )}

                    <div className="tho-ideas-grid">
                        {(ideas.content_ideas || []).map((idea, i) => (
                            <div key={i} className="tho-idea-card">
                                <div className="tho-idea-header">
                                    <span className={`tho-idea-badge ${idea.trending_potential?.replace(/\s/g, '-')}`}>
                                        {idea.trending_potential === 'very high' ? '🔥' : idea.trending_potential === 'high' ? '⬆️' : '•'} {idea.trending_potential}
                                    </span>
                                    <span className="tho-idea-type">{idea.type}</span>
                                </div>
                                <h4 className="tho-idea-title">{idea.title}</h4>
                                <p className="tho-idea-notes">{idea.notes}</p>
                                <div className="tho-idea-platforms">
                                    {(idea.platform_priority || []).map(p => (
                                        <span key={p} className="tho-idea-platform">{p}</span>
                                    ))}
                                </div>
                                <button
                                    className="tho-idea-use-btn"
                                    onClick={() => {
                                        setTheme(idea.type);
                                        setActiveTab('create');
                                    }}
                                >
                                    <Play size={12} /> Use This Idea
                                </button>
                            </div>
                        ))}
                    </div>
                </>
            )}

            {ideas?.error && (
                <div className="tho-error-state">
                    <AlertTriangle size={24} />
                    <p>{ideas.error}</p>
                </div>
            )}
        </div>
    );

    const renderScheduled = () => (
        <div className="tho-scheduled-page">
            <h2 className="tho-page-title">📅 Scheduled Posts</h2>
            <p className="tho-page-subtitle">Posts queued for publishing</p>

            {scheduledPosts.length === 0 ? (
                <div className="tho-empty-state">
                    <Calendar size={48} />
                    <p>No posts scheduled yet</p>
                    <p className="tho-preview-hint">Generate a script and hit "Schedule Post"</p>
                </div>
            ) : (
                <div className="tho-scheduled-list">
                    {scheduledPosts.map((post, i) => (
                        <div key={i} className="tho-scheduled-card">
                            <div className="tho-scheduled-left">
                                <span className="tho-scheduled-platform">{post.platform}</span>
                                <span className="tho-scheduled-type">{post.content_type}</span>
                            </div>
                            <div className="tho-scheduled-center">
                                <span className="tho-scheduled-id">{post.post_id}</span>
                                <span className="tho-scheduled-ref">Script: {post.script_reference}</span>
                            </div>
                            <div className="tho-scheduled-right">
                                <span className={`tho-scheduled-status status-${post.status}`}>{post.status}</span>
                                <span className="tho-scheduled-tip">{post.tip}</span>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );

    const renderAnalytics = () => (
        <div className="tho-analytics-page">
            <div className="tho-ideas-header">
                <div>
                    <h2 className="tho-page-title">📊 Content Performance</h2>
                    <p className="tho-page-subtitle">How your Tex content is performing</p>
                </div>
                <button className="tho-refresh-btn" onClick={handleLoadAnalytics} disabled={loadingAnalytics}>
                    {loadingAnalytics ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
                    Refresh
                </button>
            </div>

            {loadingAnalytics && !analytics && (
                <div className="tho-loading">
                    <Loader2 size={32} className="spin" />
                    <p>Loading performance data...</p>
                </div>
            )}

            {analytics && !analytics.error && analytics.summary && (
                <>
                    {/* KPI cards */}
                    <div className="tho-kpi-grid">
                        <div className="tho-kpi-card">
                            <Eye size={20} />
                            <span className="tho-kpi-value">{analytics.summary.total_views}</span>
                            <span className="tho-kpi-label">Total Views</span>
                        </div>
                        <div className="tho-kpi-card">
                            <Flame size={20} />
                            <span className="tho-kpi-value">{analytics.summary.total_engagement}</span>
                            <span className="tho-kpi-label">Engagements</span>
                        </div>
                        <div className="tho-kpi-card">
                            <Users size={20} />
                            <span className="tho-kpi-value">{analytics.summary.new_followers}</span>
                            <span className="tho-kpi-label">New Followers</span>
                        </div>
                        <div className="tho-kpi-card">
                            <MessageCircle size={20} />
                            <span className="tho-kpi-value">{analytics.summary.dms_received}</span>
                            <span className="tho-kpi-label">DMs Received</span>
                        </div>
                        <div className="tho-kpi-card accent">
                            <Zap size={20} />
                            <span className="tho-kpi-value">{analytics.summary.leads_generated}</span>
                            <span className="tho-kpi-label">Leads Generated</span>
                        </div>
                    </div>

                    {/* Top content */}
                    <div className="tho-card" style={{ marginTop: '1.5rem' }}>
                        <h3 className="tho-card-title">Top Performing Content</h3>
                        <div className="tho-top-content-list">
                            {(analytics.top_performing_content || []).map((item, i) => (
                                <div key={i} className="tho-top-content-row">
                                    <span className="tho-top-rank">#{i + 1}</span>
                                    <span className="tho-top-type">{item.type.replace('_', ' ')}</span>
                                    <span className="tho-top-views">{item.views} views</span>
                                    <span className="tho-top-rate">{item.engagement_rate} engagement</span>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Recommendations */}
                    <div className="tho-card" style={{ marginTop: '1rem' }}>
                        <h3 className="tho-card-title">💡 Recommendations</h3>
                        <ul className="tho-recs-list">
                            {(analytics.recommendations || []).map((rec, i) => (
                                <li key={i}>{rec}</li>
                            ))}
                        </ul>
                    </div>
                </>
            )}

            {analytics?.error && (
                <div className="tho-error-state">
                    <AlertTriangle size={24} />
                    <p>{analytics.error}</p>
                </div>
            )}
        </div>
    );

    /* ─── main render ─── */
    return (
        <div className="tho-studio">
            {/* Sidebar */}
            <aside className="tho-sidebar">
                <div className="tho-sidebar-top">
                    <div className="tho-avatar">
                        <div className="tho-avatar-glow" />
                        <span className="tho-avatar-emoji">🤠</span>
                    </div>
                    <h2 className="tho-sidebar-brand">Tex's Ad Studio</h2>
                    <p className="tho-sidebar-sub">AI-Powered Content Creator</p>
                </div>

                <nav className="tho-nav">
                    {TABS.map(tab => (
                        <button
                            key={tab.id}
                            className={`tho-nav-item ${activeTab === tab.id ? 'active' : ''}`}
                            onClick={() => setActiveTab(tab.id)}
                        >
                            {tab.icon}
                            <span>{tab.label}</span>
                        </button>
                    ))}
                </nav>

                <button className="tho-back-btn" onClick={onBack}>
                    <ArrowLeft size={16} />
                    <span>Back to Chat</span>
                </button>
            </aside>

            {/* Main content */}
            <main className="tho-main">
                {activeTab === 'create' && renderCreate()}
                {activeTab === 'ideas' && renderIdeas()}
                {activeTab === 'scheduled' && renderScheduled()}
                {activeTab === 'analytics' && renderAnalytics()}
            </main>
        </div>
    );
}
