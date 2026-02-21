import React from 'react';
import Markdown from 'react-markdown';
import PropertyCard from './PropertyCard';

/**
 * Error boundary wrapper for react-markdown to prevent crashes
 */
class MarkdownErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false };
    }

    static getDerivedStateFromError(error) {
        return { hasError: true };
    }

    componentDidCatch(error, errorInfo) {
        console.error('Markdown rendering error:', error, errorInfo);
    }

    render() {
        if (this.state.hasError) {
            // Fallback to plain text if markdown fails
            return <div className="whitespace-pre-wrap">{this.props.children}</div>;
        }

        return this.props.children;
    }
}

/**
 * Safe markdown renderer with error boundary and custom components
 */
export default function SafeMarkdown({ content, comparisonList = [], onToggleCompare }) {
    return (
        <MarkdownErrorBoundary>
            <Markdown
                className="prose prose-sm max-w-none"
                components={{
                    // Customize markdown rendering
                    code(props) {
                        const { children, className, node, ...rest } = props;
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
                            } catch (e) {
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
                    p: ({ node, ...props }) => <p className="mb-2 last:mb-0" {...props} />,
                    ul: ({ node, ...props }) => <ul className="list-disc list-inside mb-2" {...props} />,
                    ol: ({ node, ...props }) => <ol className="list-decimal list-inside mb-2" {...props} />,
                    li: ({ node, ...props }) => <li className="mb-1" {...props} />,
                    strong: ({ node, ...props }) => <strong className="font-bold" {...props} />,
                    em: ({ node, ...props }) => <em className="italic" {...props} />,
                }}
            >
                {content}
            </Markdown>
        </MarkdownErrorBoundary>
    );
}
