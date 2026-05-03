/** @type {import('tailwindcss').Config} */
export default {
    darkMode: 'class',
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            colors: {
                // Studio palette: tokens used by AdStudio's dark-glassmorphism theme.
                // Values mirror the existing CSS custom properties in AdStudio.css so
                // utility chains can be swapped in without any visual change.
                studio: {
                    // Solid background ramp (matches the main gradient stops).
                    bg: '#0b1120',
                    'bg-mid': '#0f172a',
                    'bg-end': '#1a1f3a',
                    // Surfaces (rgba so they layer over the gradient).
                    card: 'rgba(30, 41, 59, 0.6)',
                    'card-strong': 'rgba(30, 41, 59, 0.85)',
                    'card-hover': 'rgba(30, 41, 59, 0.7)',
                    surface: 'rgba(15, 23, 42, 0.4)',
                    'surface-strong': 'rgba(15, 23, 42, 0.85)',
                    preview: 'rgba(11, 17, 32, 0.97)',
                    // Borders.
                    border: 'rgba(59, 130, 246, 0.15)',
                    'border-strong': 'rgba(59, 130, 246, 0.4)',
                    'border-muted': 'rgba(100, 116, 139, 0.2)',
                    // Brand accents (mirror --tho-primary / --tho-accent).
                    primary: '#3b82f6',
                    'primary-soft': 'rgba(59, 130, 246, 0.15)',
                    accent: '#ec4899',
                    // Text ramp.
                    text: '#e2e8f0',
                    'text-strong': '#f1f5f9',
                    'text-muted': '#94a3b8',
                },
            },
            boxShadow: {
                // Glow rings used on focused / active studio elements.
                studio: '0 0 15px rgba(59, 130, 246, 0.2)',
                'studio-lg': '0 0 30px rgba(59, 130, 246, 0.25)',
                'studio-accent': '0 0 20px rgba(236, 72, 153, 0.25)',
            },
            backgroundImage: {
                // Main app-shell gradient (tho-studio root).
                'studio-gradient':
                    'linear-gradient(145deg, #0b1120 0%, #0f172a 40%, #1a1f3a 100%)',
            },
            keyframes: {
                'studio-glow-rotate': {
                    '0%': { transform: 'rotate(0deg)' },
                    '100%': { transform: 'rotate(360deg)' },
                },
                'studio-spin': {
                    to: { transform: 'rotate(360deg)' },
                },
                'studio-fade-in': {
                    from: { opacity: '0', transform: 'translateY(4px)' },
                    to: { opacity: '1', transform: 'translateY(0)' },
                },
            },
            animation: {
                'studio-glow-rotate': 'studio-glow-rotate 4s linear infinite',
                'studio-spin': 'studio-spin 1s linear infinite',
                'studio-fade-in': 'studio-fade-in 0.3s ease',
            },
        },
    },
    plugins: [],
}
