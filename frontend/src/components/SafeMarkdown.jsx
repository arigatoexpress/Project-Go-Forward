import React from 'react';
import Markdown from 'react-markdown';
import rehypeSanitize from 'rehype-sanitize';
import PropertyCard from './PropertyCard';

/**
 * Error boundary wrapper for react-markdown to prevent crashes
 */
class MarkdownErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false };
    }

    static getDerivedStateFromError(_error) {
        return { hasError: true };
    }

    componentDidCatch(error, errorInfo) {
        console.error('Markdown rendering error:', error, errorInfo);
    }

    render() {
        if (this.state.hasError) {
            // Render the raw markdown source as plain text — children is the
            // <Markdown> element, which would render as nothing useful.
            return <div className="whitespace-pre-wrap">{this.props.fallbackText || ''}</div>;
        }

        return this.props.children;
    }
}

/**
 * Safe markdown renderer with error boundary and custom components
 */
export default function SafeMarkdown({ content, comparisonList = [], onToggleCompare }) {
    return (
        <MarkdownErrorBoundary fallbackText={content}>
            <div className="prose prose-sm max-w-none">
            <Markdown
                rehypePlugins={[rehypeSanitize]}
                components={{
                    // Customize markdown rendering
                    code(props) {
                        const { children, className, ...rest } = props;
                        const match = /language-(\w+)/.exec(className || '');
                        const language = match ? match[1] : '';

                        if (language === 'property' || language === 'json-property' || language === 'json') {
                            try {
                                const contentStr = String(children).replace(/\n$/, '');
                                const propertyData = JSON.parse(contentStr);

                                // For 'json' blocks, verify it looks like a property before rendering as card
                                if (language === 'json') {
                                    // Basic schema check
                                    const hasRequiredFields = propertyData &&
                                        (propertyData.pricing || propertyData.display_price) &&
                                        (propertyData.specs || propertyData.sq_ft);

                                    if (!hasRequiredFields) {
                                        // Not a property card, render as normal code
                                        throw new Error("Not a property object");
                                    }
                                }

                                const isSelected = comparisonList.some(p => p.id === propertyData.id);
                                return (
                                    <div className="not-prose">
                                        <PropertyCard
                                            property={propertyData}
                                            onToggleCompare={onToggleCompare}
                                            isSelected={isSelected}
                                        />
                                    </div>
                                );
                            } catch {
                                // Fallback to normal code block if parsing fails or validation fails
                                return <code {...rest} className={className}>{children}</code>;
                            }
                        }

                        return match ? (
                            <code {...rest} className={className}>
                                {children}
                            </code>
                        ) : (
                            <code {...rest} className="bg-gray-200 px-1 py-0.5 rounded text-xs">
                                {children}
                            </code>
                        );
                    },
                    p: ({ node: _node, ...props }) => <p className="mb-2 last:mb-0" {...props} />,
                    ul: ({ node: _node, ...props }) => <ul className="list-disc list-inside mb-2" {...props} />,
                    ol: ({ node: _node, ...props }) => <ol className="list-decimal list-inside mb-2" {...props} />,
                    li: ({ node: _node, ...props }) => <li className="mb-1" {...props} />,
                    strong: ({ node: _node, ...props }) => <strong className="font-bold" {...props} />,
                    em: ({ node: _node, ...props }) => <em className="italic" {...props} />,
                }}
            >
                {content}
            </Markdown>
            </div>
        </MarkdownErrorBoundary>
    );
}
