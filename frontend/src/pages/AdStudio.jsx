import React, { useState, useEffect } from 'react';
import {
    Sparkles, Film, TrendingUp, Calendar, BarChart3, X,
    ArrowLeft, Copy, Check, ChevronDown, Loader2,
    Play, Clock, Hash, Zap, Home, MessageCircle,
    Flame, DollarSign, AlertTriangle, Clapperboard,
    BookOpen, Users, RefreshCw, Send, Eye, Image,
    Download, Layers, Search, ChevronRight, Camera,
    Box, Star, ExternalLink, ChevronLeft, Volume2,
    Pause
} from 'lucide-react';
import adminFetch from '../adminFetch';
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

const IMAGE_STYLES = [
    { id: 'photorealistic', label: 'Photo Real', icon: '📷' },
    { id: 'modern', label: 'Modern', icon: '🏗️' },
    { id: 'luxury', label: 'Luxury', icon: '✨' },
    { id: 'cozy', label: 'Cozy', icon: '🏡' },
    { id: 'aerial', label: 'Aerial', icon: '🚁' },
    { id: 'twilight', label: 'Twilight', icon: '🌅' }
];

/* ─────────────────── API helpers ─────────────────── */
async function apiGenerateScript(params) {
    const resp = await adminFetch('/api/marketing/generate-script', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params)
    });
    if (!resp.ok) throw new Error('Script generation failed');
    return resp.json();
}

async function apiGetIdeas() {
    const resp = await adminFetch('/api/marketing/trending-ideas');
    if (!resp.ok) throw new Error('Failed to load ideas');
    return resp.json();
}

async function apiSchedulePost(params) {
    const resp = await adminFetch('/api/marketing/schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params)
    });
    if (!resp.ok) throw new Error('Scheduling failed');
    return resp.json();
}

async function apiGetAnalytics() {
    const resp = await adminFetch('/api/marketing/analytics');
    if (!resp.ok) throw new Error('Analytics load failed');
    return resp.json();
}

async function apiGetInventory() {
    const resp = await adminFetch('/api/marketing/inventory-context');
    if (!resp.ok) throw new Error('Failed to load inventory');
    return resp.json();
}

async function apiGenerateImage(params) {
    const resp = await adminFetch('/api/marketing/generate-image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params)
    });
    if (!resp.ok) throw new Error('Image generation failed');
    return resp.json();
}

async function apiGenerateVideo(params) {
    const resp = await adminFetch('/api/marketing/generate-video', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params)
    });
    if (!resp.ok) throw new Error('Video generation failed');
    return resp.json();
}

async function apiGenerateVoiceover(params) {
    const resp = await adminFetch('/api/marketing/generate-voiceover', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params)
    });
    if (!resp.ok) throw new Error('Voiceover generation failed');
    return resp.json();
}

async function apiGetVoices() {
    const resp = await adminFetch('/api/marketing/voiceover-voices');
    if (!resp.ok) throw new Error('Failed to load voices');
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
    const [homeSpecs, setHomeSpecs] = useState(null);
    const [customHook, setCustomHook] = useState('');
    const [language, setLanguage] = useState('en');
    const [avatar, setAvatar] = useState('tex_classic');
    const [customAvatarPrompt, setCustomAvatarPrompt] = useState('');
    const [showPreview, setShowPreview] = useState(false);
    const [generating, setGenerating] = useState(false);
    const [script, setScript] = useState(null);
    const [copied, setCopied] = useState(false);
    const [variations, setVariations] = useState(1);
    const [activeVariation, setActiveVariation] = useState(0);

    // Inventory picker
    const [inventoryHomes, setInventoryHomes] = useState([]);
    const [loadingInventory, setLoadingInventory] = useState(false);
    const [showInventoryPicker, setShowInventoryPicker] = useState(false);
    const [selectedHome, setSelectedHome] = useState(null);

    // Real property photos & Matterport
    const [realPhotos, setRealPhotos] = useState([]);
    const [imageCategories, setImageCategories] = useState({});
    const [activeCategory, setActiveCategory] = useState('all');
    const [matterportUrl, setMatterportUrl] = useState(null);
    const [matterportId, setMatterportId] = useState(null);
    const [showMatterport, setShowMatterport] = useState(false);
    const [selectedPhotoIdx, setSelectedPhotoIdx] = useState(0);
    const [imageMode, setImageMode] = useState('real'); // 'real' or 'ai'

    // Image generation
    const [imagePrompt, setImagePrompt] = useState('');
    const [imageStyle, setImageStyle] = useState('photorealistic');
    const [generatingImage, setGeneratingImage] = useState(false);
    const [generatedImages, setGeneratedImages] = useState([]);
    const [expandedImage, setExpandedImage] = useState(null);
    const [imageError, setImageError] = useState(null);

    // Voiceover generation
    const [voices, setVoices] = useState([]);
    const [selectedVoice, setSelectedVoice] = useState('alloy');
    const [generatingVoiceover, setGeneratingVoiceover] = useState(false);
    const [voiceover, setVoiceover] = useState(null);
    const [voiceoverError, setVoiceoverError] = useState(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const audioRef = React.useRef(null);

    // Video generation
    const [generatingVideo, setGeneratingVideo] = useState(false);
    const [generatedVideo, setGeneratedVideo] = useState(null);
    const [videoError, setVideoError] = useState(null);

    // Ideas tab
    const [ideas, setIdeas] = useState(null);
    const [loadingIdeas, setLoadingIdeas] = useState(false);

    // Scheduled tab
    const [scheduledPosts, setScheduledPosts] = useState([]);
    const [scheduling, setScheduling] = useState(false);
    const [matterportCopied, setMatterportCopied] = useState(false);

    // Analytics tab
    const [analytics, setAnalytics] = useState(null);
    const [loadingAnalytics, setLoadingAnalytics] = useState(false);

    /* ─── get current script (handles variations) ─── */
    const getCurrentScript = () => {
        if (!script) return null;
        if (script.scripts && script.scripts.length > 0) {
            return script.scripts[activeVariation] || script.scripts[0];
        }
        return script.script;
    };

    /* ─── handlers ─── */
    const handleGenerate = async () => {
        setGenerating(true);
        setScript(null);
        setShowPreview(false);
        setActiveVariation(0);
        setGeneratedImages([]);
        try {
            const result = await apiGenerateScript({
                platform,
                content_theme: theme,
                home_name: homeName || undefined,
                home_price: homePrice || undefined,
                home_specs: homeSpecs || undefined,
                custom_hook: customHook || undefined,
                language,
                avatar,
                custom_avatar_prompt: avatar === 'tex_custom' ? customAvatarPrompt : undefined,
                variations
            });
            if (result.error || result.success === false) {
                setScript({ error: result.error || 'Script generation failed. Please try again.' });
            } else {
                setScript(result);
                setShowPreview(true);
                // Capture real photos and Matterport from response
                if (result.real_photos?.length > 0) {
                    setRealPhotos(result.real_photos);
                    setSelectedPhotoIdx(0);
                }
                if (result.image_categories) {
                    setImageCategories(result.image_categories);
                }
                if (result.matterport_url) {
                    setMatterportUrl(result.matterport_url);
                    setMatterportId(result.matterport_id);
                }
                // Pre-fill image prompt from first suggestion
                const currentScr = result.scripts?.[0] || result.script;
                if (currentScr?.suggested_image_prompts?.length > 0) {
                    setImagePrompt(currentScr.suggested_image_prompts[0]);
                }
            }
        } catch (err) {
            setScript({ error: err.message });
        } finally {
            setGenerating(false);
        }
    };

    const handleCopyScript = () => {
        const s = getCurrentScript();
        if (!s) return;
        const text = `${s.hook}\n\n${s.body}\n\n${s.cta}\n\n${(script?.hashtags || []).join(' ')}`;
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
                caption: getCurrentScript()?.cta,
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

    const handleLoadInventory = async () => {
        setLoadingInventory(true);
        try {
            const result = await apiGetInventory();
            if (result.success && result.homes) {
                setInventoryHomes(result.homes);
                setShowInventoryPicker(true);
            }
        } catch (err) {
            console.error('Inventory load failed:', err);
        } finally {
            setLoadingInventory(false);
        }
    };

    const handleSelectHome = (home) => {
        setSelectedHome(home);
        setHomeName(home.model_name);
        setHomePrice(home.display_price);
        setHomeSpecs(home.specs);
        setShowInventoryPicker(false);
        // Set real photos and Matterport from inventory data
        setRealPhotos(home.real_photos || []);
        setImageCategories(home.image_categories || {});
        setActiveCategory('all');
        setMatterportUrl(home.matterport_url || null);
        setMatterportId(home.matterport_id || null);
        setSelectedPhotoIdx(0);
    };

    const handleGenerateImage = async () => {
        if (!imagePrompt.trim()) return;
        setGeneratingImage(true);
        setImageError(null);
        try {
            const result = await apiGenerateImage({
                prompt: imagePrompt,
                home_name: homeName || undefined,
                platform,
                style: imageStyle,
            });
            if (result.success) {
                setGeneratedImages(prev => [result, ...prev]);
            } else {
                setImageError(result.error || 'Image generation failed. Try a different prompt.');
            }
        } catch (err) {
            setImageError('Image generation failed: ' + err.message);
        } finally {
            setGeneratingImage(false);
        }
    };

    const handleDownloadImage = (img) => {
        const link = document.createElement('a');
        link.href = `data:image/png;base64,${img.image_base64}`;
        link.download = img.filename || 'ad-image.png';
        link.click();
    };

    // Voiceover handlers
    const handleLoadVoices = async () => {
        try {
            const result = await apiGetVoices();
            if (result.success) {
                setVoices(result.voices);
            }
        } catch (err) {
            console.error('Failed to load voices:', err);
        }
    };

    const handleGenerateVoiceover = async () => {
        const s = getCurrentScript();
        if (!s) return;
        
        const fullScript = `${s.hook}\n\n${s.body}\n\n${s.cta}`;
        setGeneratingVoiceover(true);
        setVoiceoverError(null);
        
        try {
            const result = await apiGenerateVoiceover({
                script_text: fullScript,
                voice: selectedVoice,
                model: 'tts-1'
            });
            
            if (result.success) {
                setVoiceover(result);
                // Auto-play the new voiceover
                setTimeout(() => {
                    if (audioRef.current) {
                        audioRef.current.play();
                        setIsPlaying(true);
                    }
                }, 100);
            } else {
                setVoiceoverError(result.error || 'Voiceover generation failed');
            }
        } catch (err) {
            setVoiceoverError('Voiceover generation failed: ' + err.message);
        } finally {
            setGeneratingVoiceover(false);
        }
    };

    const handleDownloadVoiceover = () => {
        if (!voiceover?.audio_base64) return;
        const link = document.createElement('a');
        link.href = `data:audio/mp3;base64,${voiceover.audio_base64}`;
        link.download = voiceover.filename || 'voiceover.mp3';
        link.click();
    };

    const togglePlayback = () => {
        if (!audioRef.current) return;
        if (isPlaying) {
            audioRef.current.pause();
        } else {
            audioRef.current.play();
        }
        setIsPlaying(!isPlaying);
    };

    const handleGenerateVideo = async () => {
        if (!voiceover?.audio_base64 || !realPhotos.length) {
            setVideoError('Need voiceover and photos to generate video');
            return;
        }
        
        const s = getCurrentScript();
        const fullScript = s ? `${s.hook}\n\n${s.body}\n\n${s.cta}` : '';
        
        setGeneratingVideo(true);
        setVideoError(null);
        setGeneratedVideo(null);
        
        try {
            const result = await apiGenerateVideo({
                photos: realPhotos,
                voiceover_base64: voiceover.audio_base64,
                script_text: fullScript,
                home_name: homeName || 'ad',
                platform: platform,
                duration_per_photo: 3.0
            });
            
            if (result.success) {
                setGeneratedVideo(result);
            } else {
                setVideoError(result.error || 'Video generation failed');
            }
        } catch (err) {
            setVideoError('Video generation failed: ' + err.message);
        } finally {
            setGeneratingVideo(false);
        }
    };

    const handleDownloadVideo = () => {
        if (!generatedVideo?.download_url) return;
        // Open in new tab for download
        window.open(`https://tho-ai-agent.web.app${generatedVideo.download_url}`, '_blank');
    };

    // Load voices on mount
    useEffect(() => {
        handleLoadVoices();
    }, []);

    // Auto-load data when switching tabs
    useEffect(() => {
        if (activeTab === 'ideas' && !ideas) handleLoadIdeas();
        if (activeTab === 'analytics' && !analytics) handleLoadAnalytics();
    }, [activeTab]);

    // Update image prompt when switching variations
    useEffect(() => {
        const s = getCurrentScript();
        if (s?.suggested_image_prompts?.length > 0) {
            setImagePrompt(s.suggested_image_prompts[0]);
        }
    }, [activeVariation]);

    /* ─── render helpers ─── */
    const selectedPlatform = PLATFORMS.find(p => p.id === platform);
    const currentScript = getCurrentScript();

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
                            {/* Image mode toggle */}
                            {realPhotos.length > 0 && (
                                <div className="tho-image-mode-toggle">
                                    <button
                                        className={`tho-mode-btn ${imageMode === 'real' ? 'active' : ''}`}
                                        onClick={() => setImageMode('real')}
                                    >
                                        <Camera size={12} /> Real Photo
                                    </button>
                                    <button
                                        className={`tho-mode-btn ${imageMode === 'ai' ? 'active' : ''}`}
                                        onClick={() => setImageMode('ai')}
                                    >
                                        <Sparkles size={12} /> AI Image
                                    </button>
                                </div>
                            )}

                            {/* Background image — real photo or AI generated */}
                            {imageMode === 'real' && realPhotos.length > 0 ? (
                                <div className="tho-preview-bg-image" style={{
                                    backgroundImage: `url(${realPhotos[selectedPhotoIdx] || realPhotos[0]})`,
                                    backgroundSize: 'cover',
                                    backgroundPosition: 'center',
                                    position: 'absolute',
                                    inset: 0,
                                    opacity: 0.4
                                }} />
                            ) : generatedImages.length > 0 && (
                                <div className="tho-preview-bg-image" style={{
                                    backgroundImage: `url(data:image/png;base64,${generatedImages[0].image_base64})`,
                                    backgroundSize: 'cover',
                                    backgroundPosition: 'center',
                                    position: 'absolute',
                                    inset: 0,
                                    opacity: 0.3
                                }} />
                            )}

                            {/* Real photo navigation in preview */}
                            {imageMode === 'real' && realPhotos.length > 1 && (
                                <div className="tho-preview-photo-nav">
                                    <button
                                        className="tho-photo-nav-btn"
                                        onClick={() => setSelectedPhotoIdx(Math.max(0, selectedPhotoIdx - 1))}
                                        disabled={selectedPhotoIdx === 0}
                                    >
                                        <ChevronLeft size={16} />
                                    </button>
                                    <span className="tho-photo-counter">
                                        {selectedPhotoIdx + 1} / {realPhotos.length}
                                    </span>
                                    <button
                                        className="tho-photo-nav-btn"
                                        onClick={() => setSelectedPhotoIdx(Math.min(realPhotos.length - 1, selectedPhotoIdx + 1))}
                                        disabled={selectedPhotoIdx === realPhotos.length - 1}
                                    >
                                        <ChevronRight size={16} />
                                    </button>
                                </div>
                            )}
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
                                <p className="tho-preview-hook">{currentScript?.hook}</p>
                                <div className="tho-body-scroll">
                                    <p className="tho-preview-body-text">{currentScript?.body}</p>
                                </div>
                                <div className="tho-cta-box">
                                    <span className="tho-preview-cta">{currentScript?.cta}</span>
                                </div>
                            </div>

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
                    {/* Image Generation Section */}
                    <div className="tho-ai-feedback-box">
                        <h4><Image size={16} /> Generate Ad Image</h4>
                        <p className="text-xs text-gray-400 mb-2">Create a visual for your ad with AI (Imagen)</p>

                        {/* Image style selector */}
                        <div className="tho-image-styles">
                            {IMAGE_STYLES.map(s => (
                                <button
                                    key={s.id}
                                    className={`tho-image-style-btn ${imageStyle === s.id ? 'active' : ''}`}
                                    onClick={() => setImageStyle(s.id)}
                                    title={s.label}
                                >
                                    <span>{s.icon}</span>
                                    <span className="tho-style-label">{s.label}</span>
                                </button>
                            ))}
                        </div>

                        {/* Suggested prompts from script */}
                        {currentScript?.suggested_image_prompts?.length > 0 && (
                            <div className="tho-suggested-prompts">
                                <span className="text-xs text-gray-500">Suggested:</span>
                                {currentScript.suggested_image_prompts.map((p, i) => (
                                    <button
                                        key={i}
                                        className={`tho-prompt-chip ${imagePrompt === p ? 'active' : ''}`}
                                        onClick={() => setImagePrompt(p)}
                                    >
                                        {p.length > 60 ? p.substring(0, 60) + '...' : p}
                                    </button>
                                ))}
                            </div>
                        )}

                        <textarea
                            className="tho-input tho-textarea"
                            placeholder="Describe the image you want to generate..."
                            value={imagePrompt}
                            onChange={e => setImagePrompt(e.target.value)}
                            rows={2}
                        />
                        <button
                            className="tho-btn tho-btn-secondary w-full mt-2 flex items-center justify-center gap-2"
                            onClick={handleGenerateImage}
                            disabled={generatingImage || !imagePrompt.trim()}
                        >
                            {generatingImage ? (
                                <><Loader2 size={14} className="spin" /> Generating Image...</>
                            ) : (
                                <><Image size={14} /> Generate Image</>
                            )}
                        </button>
                        {imageError && (
                            <div className="tho-image-error">
                                <AlertTriangle size={12} />
                                <span>{imageError}</span>
                                <button onClick={() => setImageError(null)} className="tho-error-dismiss"><X size={12} /></button>
                            </div>
                        )}
                    </div>

                    {/* Generated Images Gallery */}
                    {generatedImages.length > 0 && (
                        <div className="tho-image-gallery">
                            <h4 className="text-sm font-medium text-gray-300 mb-2">Generated Images</h4>
                            <div className="tho-gallery-grid">
                                {generatedImages.map((img, i) => (
                                    <div key={i} className="tho-gallery-item">
                                        <img
                                            src={`data:image/png;base64,${img.image_base64}`}
                                            alt={`Generated ad ${i + 1}`}
                                            className="tho-gallery-img"
                                            onClick={() => setExpandedImage(img)}
                                        />
                                        <button
                                            className="tho-gallery-download"
                                            onClick={() => handleDownloadImage(img)}
                                            title="Download"
                                        >
                                            <Download size={12} />
                                        </button>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Voiceover Generation Section */}
                    <div className="tho-ai-feedback-box mt-4" style={{borderTop: '1px solid #374151', paddingTop: '1rem'}}>
                        <h4><Volume2 size={16} /> Generate Voiceover</h4>
                        <p className="text-xs text-gray-400 mb-2">Create AI voiceover audio from your script (OpenAI TTS)</p>
                        
                        {/* Voice selector */}
                        <div className="tho-voice-selector">
                            <span className="text-xs text-gray-500">Voice:</span>
                            <select 
                                className="tho-input tho-select"
                                value={selectedVoice}
                                onChange={e => setSelectedVoice(e.target.value)}
                            >
                                {voices.map(v => (
                                    <option key={v.id} value={v.id}>
                                        {v.name} — {v.description} ({v.style})
                                    </option>
                                ))}
                            </select>
                        </div>
                        
                        {/* Generate button */}
                        <button
                            className="tho-btn tho-btn-secondary w-full mt-2 flex items-center justify-center gap-2"
                            onClick={handleGenerateVoiceover}
                            disabled={generatingVoiceover || !currentScript}
                        >
                            {generatingVoiceover ? (
                                <><Loader2 size={14} className="spin" /> Generating Voice...</>
                            ) : (
                                <><Volume2 size={14} /> Generate Voiceover</>
                            )}
                        </button>
                        
                        {voiceoverError && (
                            <div className="tho-image-error">
                                <AlertTriangle size={12} />
                                <span>{voiceoverError}</span>
                                <button onClick={() => setVoiceoverError(null)} className="tho-error-dismiss"><X size={12} /></button>
                            </div>
                        )}
                        
                        {/* Voiceover player */}
                        {voiceover?.success && (
                            <div className="tho-voiceover-player">
                                <audio 
                                    ref={audioRef}
                                    src={`data:audio/mp3;base64,${voiceover.audio_base64}`}
                                    onEnded={() => setIsPlaying(false)}
                                    onPause={() => setIsPlaying(false)}
                                    onPlay={() => setIsPlaying(true)}
                                />
                                <div className="tho-voiceover-controls">
                                    <button 
                                        className="tho-play-btn"
                                        onClick={togglePlayback}
                                    >
                                        {isPlaying ? <Pause size={16} /> : <Play size={16} />}
                                    </button>
                                    <div className="tho-voiceover-info">
                                        <span className="tho-voiceover-voice">{voiceover.voice}</span>
                                        <span className="tho-voiceover-duration">~{voiceover.estimated_duration_seconds}s</span>
                                    </div>
                                    <button 
                                        className="tho-download-btn"
                                        onClick={handleDownloadVoiceover}
                                        title="Download MP3"
                                    >
                                        <Download size={14} />
                                    </button>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Video Generation Section */}
                    <div className="tho-ai-feedback-box mt-4" style={{borderTop: '1px solid #374151', paddingTop: '1rem'}}>
                        <h4><Film size={16} /> Generate Video</h4>
                        <p className="text-xs text-gray-400 mb-2">
                            Create MP4 video from photos + voiceover (slideshow with transitions)
                        </p>
                        
                        <div className="tho-video-info text-xs text-gray-500 mb-2">
                            {realPhotos.length > 0 ? (
                                <span>✓ {realPhotos.length} photos ready</span>
                            ) : (
                                <span>⚠ Select a home with photos first</span>
                            )}
                            {voiceover?.success ? (
                                <span className="ml-3">✓ Voiceover ready (~{voiceover.estimated_duration_seconds}s)</span>
                            ) : (
                                <span className="ml-3">⚠ Generate voiceover first</span>
                            )}
                        </div>
                        
                        <button
                            className="tho-btn tho-btn-primary w-full mt-2 flex items-center justify-center gap-2"
                            onClick={handleGenerateVideo}
                            disabled={generatingVideo || !voiceover?.success || realPhotos.length === 0}
                        >
                            {generatingVideo ? (
                                <><Loader2 size={14} className="spin" /> Creating Video...</>
                            ) : (
                                <><Film size={14} /> Generate MP4 Video</>
                            )}
                        </button>
                        
                        {videoError && (
                            <div className="tho-image-error">
                                <AlertTriangle size={12} />
                                <span>{videoError}</span>
                                <button onClick={() => setVideoError(null)} className="tho-error-dismiss"><X size={12} /></button>
                            </div>
                        )}
                        
                        {generatedVideo?.success && (
                            <div className="tho-video-result mt-3 p-3 bg-green-900/20 border border-green-700/30 rounded-lg">
                                <div className="flex items-center justify-between">
                                    <div>
                                        <div className="text-sm font-medium text-green-400">✓ Video Created!</div>
                                        <div className="text-xs text-gray-400">
                                            {generatedVideo.resolution} • {generatedVideo.duration_seconds}s • {generatedVideo.file_size_mb}MB
                                        </div>
                                    </div>
                                    <button 
                                        className="tho-btn tho-btn-primary flex items-center gap-2"
                                        onClick={handleDownloadVideo}
                                    >
                                        <Download size={14} /> Download MP4
                                    </button>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Quality Score Display */}
                    {script?.quality && (
                        <div className="tho-quality-panel">
                            <h4><Star size={16} /> Script Quality Score</h4>
                            <div className="tho-quality-overall">
                                <span className={`tho-quality-number ${script.quality.average >= 7 ? 'good' : script.quality.average >= 5 ? 'fair' : 'poor'}`}>
                                    {script.quality.average}/10
                                </span>
                                <span className="tho-quality-status">
                                    {script.quality.passed ? '✅ Passed' : '⚠️ Refined'}
                                </span>
                            </div>
                            <div className="tho-quality-breakdown">
                                {Object.entries(script.quality.scores || {}).map(([key, val]) => (
                                    <div key={key} className="tho-quality-row">
                                        <span className="tho-quality-label">{key.replace('_', ' ')}</span>
                                        <div className="tho-quality-bar-bg">
                                            <div
                                                className={`tho-quality-bar-fill ${val >= 7 ? 'good' : val >= 5 ? 'fair' : 'poor'}`}
                                                style={{ width: `${val * 10}%` }}
                                            />
                                        </div>
                                        <span className="tho-quality-val">{val}</span>
                                    </div>
                                ))}
                            </div>
                            {script.quality.issues?.length > 0 && (
                                <div className="tho-quality-issues">
                                    {script.quality.issues.map((issue, i) => (
                                        <span key={i} className="tho-quality-issue">⚠ {issue}</span>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Matterport CTA */}
                    {matterportUrl && (
                        <div className="tho-ai-feedback-box mt-4">
                            <h4><Box size={16} /> 3D Tour Available</h4>
                            <p className="text-xs text-gray-400 mb-2">This home has a Matterport 3D tour. Include the link in your CTA!</p>
                            <div className="tho-matterport-link-row">
                                <input
                                    type="text"
                                    className="tho-input"
                                    value={matterportUrl}
                                    readOnly
                                    style={{ fontSize: '0.7rem' }}
                                />
                                <button
                                    className="tho-btn tho-btn-secondary"
                                    onClick={() => {
                                        navigator.clipboard.writeText(matterportUrl);
                                        setMatterportCopied(true);
                                        setTimeout(() => setMatterportCopied(false), 2000);
                                    }}
                                    title="Copy tour link"
                                >
                                    {matterportCopied ? <Check size={12} /> : <Copy size={12} />}
                                </button>
                            </div>
                        </div>
                    )}

                    {/* Prompt Improvement */}
                    <div className="tho-ai-feedback-box mt-4">
                        <h4>💡 Tweak Script</h4>
                        <p className="text-xs text-gray-400 mb-2">Want to refine? Adjust your settings and regenerate.</p>
                        <button className="tho-btn tho-btn-secondary w-full" onClick={() => { setShowPreview(false); }}>
                            Back to Editor
                        </button>
                    </div>

                    <div className="tho-post-actions mt-4">
                        <button
                            className="tho-btn tho-btn-primary w-full flex items-center justify-center gap-2"
                            onClick={async () => {
                                await handleSchedule();
                                setShowPreview(false);
                                setActiveTab('scheduled');
                            }}
                            disabled={scheduling}
                        >
                            {scheduling ? (
                                <><Loader2 size={18} className="spin" /> Scheduling...</>
                            ) : (
                                <><Send size={18} /> Schedule Post</>
                            )}
                        </button>
                    </div>
                </div>
            </div>

            {/* Expanded image overlay */}
            {expandedImage && (
                <div className="tho-image-overlay" onClick={() => setExpandedImage(null)}>
                    <div className="tho-image-overlay-content" onClick={e => e.stopPropagation()}>
                        <img
                            src={`data:image/png;base64,${expandedImage.image_base64}`}
                            alt="Full size"
                            className="tho-overlay-img"
                        />
                        <div className="tho-overlay-actions">
                            <button className="tho-btn tho-btn-secondary" onClick={() => handleDownloadImage(expandedImage)}>
                                <Download size={14} /> Download
                            </button>
                            <button className="tho-btn tho-btn-ghost" onClick={() => setExpandedImage(null)}>
                                <X size={14} /> Close
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );

    const renderInventoryPicker = () => (
        <div className="tho-inventory-modal" onClick={() => setShowInventoryPicker(false)}>
            <div className="tho-inventory-panel" onClick={e => e.stopPropagation()}>
                <div className="tho-inventory-header">
                    <h3>Select a Home from Inventory</h3>
                    <button className="tho-close-preview" onClick={() => setShowInventoryPicker(false)}>
                        <X size={18} />
                    </button>
                </div>
                <div className="tho-inventory-list">
                    {inventoryHomes.map((home, i) => (
                        <button
                            key={home.id || i}
                            className={`tho-inventory-item ${selectedHome?.id === home.id ? 'selected' : ''}`}
                            onClick={() => handleSelectHome(home)}
                        >
                            {(home.real_photos?.length > 0 ? (
                                <img src={home.real_photos[0]} alt={home.model_name} className="tho-inv-thumb" />
                            ) : home.image_url ? (
                                <img src={home.image_url} alt={home.model_name} className="tho-inv-thumb" />
                            ) : (
                                <div className="tho-inv-thumb tho-inv-thumb-placeholder"><Home size={20} /></div>
                            ))}
                            <div className="tho-inv-details">
                                <span className="tho-inv-name">{home.model_name}</span>
                                <span className="tho-inv-mfr">{home.manufacturer}</span>
                                <span className="tho-inv-specs">
                                    {home.specs?.beds}BR / {home.specs?.baths}BA • {home.specs?.sq_ft} sqft
                                </span>
                                <div className="tho-inv-badges">
                                    {home.real_photos?.length > 0 && (
                                        <span className="tho-inv-badge tho-badge-photos">
                                            <Camera size={10} /> {home.real_photos.length} Photos
                                        </span>
                                    )}
                                    {home.image_categories?.bedroom && (
                                        <span className="tho-inv-badge tho-badge-bedroom">Bed</span>
                                    )}
                                    {home.image_categories?.kitchen && (
                                        <span className="tho-inv-badge tho-badge-kitchen">Kit</span>
                                    )}
                                    {home.image_categories?.bathroom && (
                                        <span className="tho-inv-badge tho-badge-bath">Bath</span>
                                    )}
                                    {home.matterport_id && (
                                        <span className="tho-inv-badge tho-badge-3d">
                                            <Box size={10} /> 3D Tour
                                        </span>
                                    )}
                                </div>
                            </div>
                            <div className="tho-inv-price">
                                <span className="tho-inv-price-val">{home.display_price}</span>
                                <span className={`tho-inv-status ${home.status?.toLowerCase()?.includes('pre') ? 'preowned' : ''}`}>
                                    {home.status}
                                </span>
                            </div>
                            <ChevronRight size={16} className="tho-inv-arrow" />
                        </button>
                    ))}
                    {inventoryHomes.length === 0 && (
                        <div className="tho-empty-state" style={{ padding: '2rem' }}>
                            <p>No inventory loaded</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );

    const renderCreate = () => (
        <div className="tho-create-layout">
            {showPreview && script && currentScript && !script.error && renderPreview()}
            {showInventoryPicker && renderInventoryPicker()}
            <div className="tho-create-form">
                {/* Step 0: Avatar & Language */}
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
                                    placeholder="Describe your custom avatar..."
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

                {/* Step 3: Feature a Home (with Inventory Picker) */}
                <div className="tho-card">
                    <div className="tho-step-label">STEP 3</div>
                    <h3 className="tho-card-title">Feature a Home <span className="tho-optional">(Optional)</span></h3>

                    {selectedHome && (
                        <div className="tho-selected-home-section">
                            <div className="tho-selected-home">
                                {realPhotos.length > 0 ? (
                                    <img src={realPhotos[0]} alt={selectedHome.model_name} className="tho-selected-thumb" />
                                ) : selectedHome.image_url ? (
                                    <img src={selectedHome.image_url} alt={selectedHome.model_name} className="tho-selected-thumb" />
                                ) : null}
                                <div className="tho-selected-info">
                                    <span className="tho-selected-name">{selectedHome.model_name}</span>
                                    <span className="tho-selected-price">{selectedHome.display_price}</span>
                                    <span className="tho-selected-specs">
                                        {selectedHome.specs?.beds}BR / {selectedHome.specs?.baths}BA • {selectedHome.specs?.sq_ft} sqft
                                    </span>
                                </div>
                                <button className="tho-clear-home" onClick={() => {
                                    setSelectedHome(null);
                                    setHomeName('');
                                    setHomePrice('');
                                    setHomeSpecs(null);
                                    setRealPhotos([]);
                                    setImageCategories({});
                                    setActiveCategory('all');
                                    setMatterportUrl(null);
                                    setMatterportId(null);
                                }}>
                                    <X size={14} />
                                </button>
                            </div>

                            {/* Real photo gallery strip with category filters */}
                            {realPhotos.length > 1 && (
                                <div className="tho-photo-strip">
                                    <div className="tho-photo-strip-header">
                                        <span className="tho-photo-strip-label">
                                            <Camera size={12} /> {realPhotos.length} real photos
                                        </span>
                                        {Object.keys(imageCategories).length > 1 && (
                                            <div className="tho-category-tabs">
                                                <button
                                                    className={`tho-cat-tab ${activeCategory === 'all' ? 'active' : ''}`}
                                                    onClick={() => { setActiveCategory('all'); setSelectedPhotoIdx(0); }}
                                                >All</button>
                                                {Object.entries(imageCategories).map(([cat, files]) => (
                                                    <button
                                                        key={cat}
                                                        className={`tho-cat-tab ${activeCategory === cat ? 'active' : ''}`}
                                                        onClick={() => { setActiveCategory(cat); setSelectedPhotoIdx(0); }}
                                                    >
                                                        {cat.charAt(0).toUpperCase() + cat.slice(1)} ({files.length})
                                                    </button>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                    <div className="tho-photo-strip-scroll">
                                        {(activeCategory === 'all' ? realPhotos : (imageCategories[activeCategory] || []).map(f => {
                                            const asset = selectedHome && realPhotos.find(url => url.includes(f.replace(/%20/g, '%20')));
                                            return asset || `https://d132mt2yijm03y.cloudfront.net/manufacturer/3335/floorplan/${selectedHome?.plan_id || ''}/${f}`;
                                        })).map((url, i) => (
                                            <img
                                                key={i}
                                                src={url}
                                                alt={`${activeCategory !== 'all' ? activeCategory + ' ' : ''}Photo ${i + 1}`}
                                                className={`tho-photo-strip-img ${selectedPhotoIdx === i ? 'active' : ''}`}
                                                onClick={() => setSelectedPhotoIdx(i)}
                                            />
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Matterport 3D tour button */}
                            {matterportUrl && (
                                <button
                                    className="tho-matterport-btn"
                                    onClick={() => setShowMatterport(!showMatterport)}
                                >
                                    <Box size={14} />
                                    {showMatterport ? 'Hide 3D Tour' : 'Preview 3D Tour'}
                                    <ExternalLink size={12} />
                                </button>
                            )}

                            {/* Matterport embed */}
                            {showMatterport && matterportUrl && (
                                <div className="tho-matterport-embed">
                                    <iframe
                                        src={matterportUrl}
                                        title="Matterport 3D Tour"
                                        className="tho-matterport-iframe"
                                        allowFullScreen
                                    />
                                    <a
                                        href={matterportUrl}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="tho-matterport-link"
                                    >
                                        Open full 3D tour <ExternalLink size={12} />
                                    </a>
                                </div>
                            )}
                        </div>
                    )}

                    <div className="tho-home-actions">
                        <button
                            className="tho-btn tho-btn-secondary flex items-center gap-2"
                            onClick={handleLoadInventory}
                            disabled={loadingInventory}
                        >
                            {loadingInventory ? (
                                <><Loader2 size={14} className="spin" /> Loading...</>
                            ) : (
                                <><Search size={14} /> Browse Inventory</>
                            )}
                        </button>
                        <span className="tho-or-divider">or enter manually:</span>
                    </div>

                    <div className="tho-input-group">
                        <input
                            type="text"
                            placeholder="Home model name (e.g. The Nassau)"
                            value={homeName}
                            onChange={e => { setHomeName(e.target.value); setSelectedHome(null); }}
                            className="tho-input"
                        />
                        <input
                            type="text"
                            placeholder="Price (e.g. $89,900)"
                            value={homePrice}
                            onChange={e => { setHomePrice(e.target.value); setSelectedHome(null); }}
                            className="tho-input"
                        />
                    </div>
                </div>

                {/* Step 4: Custom hook + Variations */}
                <div className="tho-card">
                    <div className="tho-step-label">STEP 4</div>
                    <h3 className="tho-card-title">Custom Hook & Variations</h3>
                    <textarea
                        placeholder="Write a custom opening hook, or leave blank for Tex to generate one..."
                        value={customHook}
                        onChange={e => setCustomHook(e.target.value)}
                        className="tho-textarea"
                        rows={2}
                    />
                    <div className="tho-variations-row">
                        <span className="tho-variations-label"><Layers size={14} /> Script Variations:</span>
                        <div className="tho-variations-btns">
                            {[1, 2, 3].map(n => (
                                <button
                                    key={n}
                                    className={`tho-variation-btn ${variations === n ? 'active' : ''}`}
                                    onClick={() => setVariations(n)}
                                >
                                    {n}
                                </button>
                            ))}
                        </div>
                        {variations > 1 && (
                            <span className="tho-variations-hint">A/B test {variations} different hooks</span>
                        )}
                    </div>
                </div>

                {/* Generate button */}
                <button
                    className="tho-generate-btn"
                    onClick={handleGenerate}
                    disabled={generating}
                >
                    {generating ? (
                        <><Loader2 size={18} className="spin" /> Generating{variations > 1 ? ` ${variations} Variations` : ''}...</>
                    ) : (
                        <><Sparkles size={18} /> Generate Script{variations > 1 ? `s (${variations})` : ''}</>
                    )}
                </button>
            </div>

            {/* Right: Preview */}
            <div className="tho-preview-panel">
                <div className="tho-phone-frame">
                    <div className="tho-phone-notch" />
                    <div className="tho-phone-screen">
                        {currentScript && !script?.error ? (
                            <div className="tho-script-preview">
                                {/* Real photo background in phone preview */}
                                {realPhotos.length > 0 && (
                                    <div className="tho-preview-real-bg" style={{
                                        backgroundImage: `url(${realPhotos[selectedPhotoIdx] || realPhotos[0]})`,
                                        backgroundSize: 'cover',
                                        backgroundPosition: 'center',
                                        position: 'absolute',
                                        inset: 0,
                                        opacity: 0.15,
                                        borderRadius: 'inherit',
                                    }} />
                                )}
                                {/* Model + quality badges */}
                                <div className="tho-preview-badges">
                                    {script?.model_used && (
                                        <div className="tho-model-badge">
                                            <Sparkles size={10} /> {script.model_used.includes('2.5') ? 'Gemini 2.5 Flash' : 'Gemini 2.0 Flash'}
                                        </div>
                                    )}
                                    {(currentScript?.quality_score || script?.quality?.average) && (
                                        <div className={`tho-quality-badge ${(currentScript?.quality_score || script?.quality?.average) >= 7 ? 'good' : 'fair'}`}>
                                            <Star size={10} /> {currentScript?.quality_score || script?.quality?.average}/10
                                        </div>
                                    )}
                                </div>

                                {/* Variation tabs */}
                                {script?.scripts?.length > 1 && (
                                    <div className="tho-variation-tabs">
                                        {script.scripts.map((s, i) => (
                                            <button
                                                key={i}
                                                className={`tho-var-tab ${activeVariation === i ? 'active' : ''}`}
                                                onClick={() => setActiveVariation(i)}
                                            >
                                                {s.tone ? `${s.tone}` : `V${i + 1}`}
                                            </button>
                                        ))}
                                    </div>
                                )}

                                <div className="tho-preview-platform">
                                    <span className="tho-preview-platform-icon">{selectedPlatform?.icon}</span>
                                    <span>{selectedPlatform?.name}</span>
                                </div>

                                <div className="tho-script-section">
                                    <div className="tho-script-label">
                                        <Flame size={12} /> HOOK
                                    </div>
                                    <p className="tho-script-hook">{currentScript?.hook}</p>
                                </div>

                                <div className="tho-script-section">
                                    <div className="tho-script-label">
                                        <Play size={12} /> SCRIPT
                                    </div>
                                    <pre className="tho-script-body">{currentScript?.body}</pre>
                                </div>

                                <div className="tho-script-section">
                                    <div className="tho-script-label">
                                        <Send size={12} /> CTA
                                    </div>
                                    <p className="tho-script-cta">{currentScript?.cta}</p>
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
                                    <span><Clock size={12} /> {currentScript?.duration_estimate}</span>
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
                {currentScript && !script?.error && (
                    <div className="tho-preview-actions">
                        <button className="tho-action-btn" onClick={handleCopyScript}>
                            {copied ? <><Check size={14} /> Copied!</> : <><Copy size={14} /> Copy Script</>}
                        </button>
                        <button
                            className="tho-action-btn tho-action-primary"
                            onClick={() => setShowPreview(true)}
                        >
                            <Eye size={14} /> Full Preview & Images
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
                    <p className="tho-page-subtitle">
                        AI-generated ideas based on your {ideas?.inventory_count > 0 ? `${ideas.inventory_count} homes in stock` : 'inventory and trends'}
                    </p>
                </div>
                <button className="tho-refresh-btn" onClick={handleLoadIdeas} disabled={loadingIdeas}>
                    {loadingIdeas ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
                    Refresh
                </button>
            </div>

            {loadingIdeas && !ideas && (
                <div className="tho-loading">
                    <Loader2 size={32} className="spin" />
                    <p>Tex is analyzing your inventory and trends...</p>
                </div>
            )}

            {ideas && !ideas.error && (
                <>
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
                                        if (idea.home_name) {
                                            setHomeName(idea.home_name);
                                        }
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
