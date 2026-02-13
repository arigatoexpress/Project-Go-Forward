import React, { useState, useEffect } from 'react';
import { FileText, ArrowLeft, Download, CheckCircle, AlertCircle, Search, Package, Loader2 } from 'lucide-react';
import SmartForm from '../components/SmartForm';

const CATEGORY_ORDER = ['TMHA', 'TDHCA', 'State', 'Internal'];
const CATEGORY_COLORS = {
  TMHA: 'bg-blue-100 text-blue-700',
  TDHCA: 'bg-green-100 text-green-700',
  State: 'bg-yellow-100 text-yellow-700',
  Internal: 'bg-purple-100 text-purple-700',
};

const DocumentCenter = ({ onBack, sessionId }) => {
  const [templates, setTemplates] = useState([]);
  const [packets, setPackets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeCategory, setActiveCategory] = useState('all');
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [selectedPacket, setSelectedPacket] = useState(null);
  const [generationStatus, setGenerationStatus] = useState('idle');
  const [downloadUrl, setDownloadUrl] = useState(null);
  const [errorMessage, setErrorMessage] = useState('');
  const [generationResult, setGenerationResult] = useState(null);
  const [fetchError, setFetchError] = useState('');

  useEffect(() => {
    fetchTemplates();
  }, []);

  const fetchTemplates = async () => {
    setFetchError('');
    try {
      const response = await fetch('/api/documents/templates');
      if (!response.ok) throw new Error(`Server returned ${response.status}`);
      const data = await response.json();
      if (data.error) throw new Error(data.error);
      setTemplates(data.templates || []);
      setPackets(data.packets || []);
    } catch (e) {
      console.error('Failed to load templates:', e);
      setFetchError('Unable to load document templates. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const filteredTemplates = templates.filter(t => {
    const matchesSearch = !searchQuery ||
      t.display_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesCategory = activeCategory === 'all' || t.category === activeCategory;
    return matchesSearch && matchesCategory;
  });

  const groupedTemplates = {};
  filteredTemplates.forEach(t => {
    if (!groupedTemplates[t.category]) groupedTemplates[t.category] = [];
    groupedTemplates[t.category].push(t);
  });

  const handleGenerateDocument = async (formData) => {
    setGenerationStatus('generating');
    setErrorMessage('');
    setDownloadUrl(null);

    try {
      const response = await fetch('/api/documents/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          template_name: selectedTemplate.template_name,
          data: formData,
        }),
      });
      const data = await response.json();

      if (data.success) {
        setGenerationStatus('success');
        setDownloadUrl(data.download_url);
        setGenerationResult(data);
      } else {
        setGenerationStatus('error');
        setErrorMessage(data.error || 'Unknown error occurred');
      }
    } catch (e) {
      setGenerationStatus('error');
      setErrorMessage(e.message);
    }
  };

  const handleGeneratePacket = async (formData) => {
    setGenerationStatus('generating');
    setErrorMessage('');
    setDownloadUrl(null);

    try {
      const response = await fetch('/api/documents/generate-packet', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          packet_name: selectedPacket.packet_name,
          data: formData,
        }),
      });
      const data = await response.json();

      if (data.success) {
        setGenerationStatus('success');
        setDownloadUrl(data.download_url);
        setGenerationResult(data);
      } else {
        setGenerationStatus('error');
        setErrorMessage(data.error || 'Unknown error occurred');
      }
    } catch (e) {
      setGenerationStatus('error');
      setErrorMessage(e.message);
    }
  };

  const resetToDocList = () => {
    setSelectedTemplate(null);
    setSelectedPacket(null);
    setGenerationStatus('idle');
    setDownloadUrl(null);
    setErrorMessage('');
    setGenerationResult(null);
  };

  // --- FORM VIEW (template or packet selected) ---
  if (selectedTemplate || selectedPacket) {
    const templateName = selectedTemplate
      ? selectedTemplate.template_name
      : (selectedPacket.templates?.[0] || '');
    const displayName = selectedTemplate
      ? selectedTemplate.display_name
      : selectedPacket.display_name;

    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <button
          onClick={resetToDocList}
          className="flex items-center text-blue-600 hover:text-blue-800 mb-6 transition-colors"
        >
          <ArrowLeft size={20} className="mr-1" /> Back to Documents
        </button>

        {generationStatus === 'idle' && (
          <SmartForm
            templateName={templateName}
            title={displayName}
            sessionId={sessionId}
            onSubmit={selectedPacket ? handleGeneratePacket : handleGenerateDocument}
            onCancel={resetToDocList}
          />
        )}

        {generationStatus === 'generating' && (
          <div className="flex flex-col items-center justify-center p-12 bg-white rounded-lg shadow-md">
            <Loader2 className="h-12 w-12 animate-spin text-blue-600 mb-4" />
            <p className="text-lg text-gray-700">Generating {displayName}...</p>
          </div>
        )}

        {generationStatus === 'success' && (
          <div className="flex flex-col items-center justify-center p-12 bg-white rounded-lg shadow-md text-center">
            <CheckCircle size={64} className="text-green-500 mb-4" />
            <h2 className="text-2xl font-bold text-gray-800 mb-2">Document Ready!</h2>
            <p className="text-gray-600 mb-2">{generationResult?.message}</p>
            {generationResult?.page_count && (
              <p className="text-sm text-gray-500 mb-4">
                {generationResult.page_count} pages | {generationResult.documents_included?.length || 0} documents
              </p>
            )}
            <div className="flex space-x-4">
              <a
                href={downloadUrl}
                download
                className="flex items-center px-6 py-3 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition"
                target="_blank"
                rel="noreferrer"
              >
                <Download size={20} className="mr-2" /> Download PDF
              </a>
              <button
                onClick={resetToDocList}
                className="px-6 py-3 border border-gray-300 rounded-md hover:bg-gray-50 transition"
              >
                Back to Documents
              </button>
            </div>
          </div>
        )}

        {generationStatus === 'error' && (
          <div className="flex flex-col items-center justify-center p-12 bg-white rounded-lg shadow-md text-center">
            <AlertCircle size={64} className="text-red-500 mb-4" />
            <h2 className="text-2xl font-bold text-gray-800 mb-2">Generation Failed</h2>
            <p className="text-gray-600 mb-6">{errorMessage}</p>
            <button
              onClick={() => setGenerationStatus('idle')}
              className="px-6 py-3 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition"
            >
              Try Again
            </button>
          </div>
        )}
      </div>
    );
  }

  // --- DOCUMENT LIST VIEW ---
  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Document Center</h1>
          <p className="mt-1 text-gray-500">Generate legal contracts, closing packets, and compliance forms.</p>
        </div>
        <button onClick={onBack} className="text-gray-500 hover:text-gray-700">Close</button>
      </div>

      {/* Search + Category Filter */}
      <div className="mb-6 space-y-4">
        <div className="relative">
          <Search size={18} className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            placeholder="Search documents..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setActiveCategory('all')}
            className={`px-3 py-1.5 rounded-full text-sm font-medium transition ${
              activeCategory === 'all' ? 'bg-gray-800 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            All ({templates.length})
          </button>
          {CATEGORY_ORDER.map(cat => {
            const count = templates.filter(t => t.category === cat).length;
            if (count === 0) return null;
            return (
              <button
                key={cat}
                onClick={() => setActiveCategory(cat)}
                className={`px-3 py-1.5 rounded-full text-sm font-medium transition ${
                  activeCategory === cat ? 'bg-gray-800 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {cat} ({count})
              </button>
            );
          })}
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      )}

      {/* Closing Packets */}
      {!loading && packets.length > 0 && activeCategory === 'all' && (
        <div className="mb-8">
          <h2 className="text-lg font-semibold text-gray-800 mb-3 flex items-center">
            <Package size={20} className="mr-2 text-blue-600" /> Closing Packets
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {packets.map(packet => (
              <div
                key={packet.packet_name}
                onClick={() => setSelectedPacket(packet)}
                className="bg-gradient-to-r from-blue-50 to-white p-5 rounded-lg shadow-md hover:shadow-lg transition cursor-pointer border-2 border-blue-200 hover:border-blue-500"
              >
                <div className="flex items-center mb-2">
                  <Package size={20} className="text-blue-600 mr-2" />
                  <h3 className="text-lg font-medium text-gray-900">{packet.display_name}</h3>
                </div>
                <p className="text-sm text-gray-500 mb-2">{packet.description}</p>
                <p className="text-xs text-blue-600 font-medium">{packet.template_count} documents included</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Templates by Category */}
      {!loading && CATEGORY_ORDER.map(category => {
        const categoryTemplates = groupedTemplates[category];
        if (!categoryTemplates || categoryTemplates.length === 0) return null;

        return (
          <div key={category} className="mb-8">
            <h2 className="text-lg font-semibold text-gray-800 mb-3">{category} Forms</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {categoryTemplates.map(template => (
                <div
                  key={template.template_name}
                  onClick={() => setSelectedTemplate(template)}
                  className="bg-white p-5 rounded-lg shadow-md hover:shadow-lg transition cursor-pointer border border-transparent hover:border-blue-500 group"
                >
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center justify-center h-10 w-10 rounded-md bg-blue-100 text-blue-600 group-hover:bg-blue-600 group-hover:text-white transition-colors">
                      <FileText size={20} />
                    </div>
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${CATEGORY_COLORS[template.category] || 'bg-gray-100 text-gray-600'}`}>
                      {template.category}
                    </span>
                  </div>
                  <h3 className="text-base font-medium text-gray-900 mb-1">{template.display_name}</h3>
                  <p className="text-sm text-gray-500 mb-2 line-clamp-2">{template.description}</p>
                  <p className="text-xs text-gray-400">{template.field_count} fields</p>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      {!loading && fetchError && (
        <div className="text-center py-12">
          <AlertCircle size={48} className="mx-auto mb-4 text-red-400" />
          <p className="text-red-600 mb-4">{fetchError}</p>
          <button
            onClick={fetchTemplates}
            className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition"
          >
            Try Again
          </button>
        </div>
      )}

      {!loading && !fetchError && filteredTemplates.length === 0 && (
        <div className="text-center py-12 text-gray-500">
          <FileText size={48} className="mx-auto mb-4 opacity-30" />
          <p>No documents match your search.</p>
        </div>
      )}
    </div>
  );
};

export default DocumentCenter;
