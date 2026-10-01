/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    darkMode: 'class',
    theme: {
        extend: {
            colors: {
                // Cyan is the instrument light — reserved for the system's own
                // voice (readouts, focus, the core). Never used for P&L.
                primary: {
                    50: '#ecfeff', 100: '#cffafe', 200: '#a5f3fc', 300: '#67e8f9',
                    400: '#22d3ee', 500: '#06b6d4', 600: '#0891b2', 700: '#0e7490',
                    800: '#155e75', 900: '#164e63', 950: '#083344',
                },
                // Amber means "the system wants your attention".
                alert: {
                    300: '#fcd34d', 400: '#fbbf24', 500: '#f59e0b', 600: '#d97706',
                },
                success: {
                    300: '#86efac', 400: '#4ade80', 500: '#22c55e', 600: '#16a34a',
                },
                danger: {
                    300: '#fca5a5', 400: '#f87171', 500: '#ef4444', 600: '#dc2626',
                },
                // Near-black with a blue cast, so the cyan glow reads as light
                // emitted by the interface rather than paint on top of it.
                dark: {
                    50: '#f8fafc', 100: '#e9eef5', 200: '#cbd5e1', 300: '#94a3b8',
                    400: '#64748b', 500: '#475569', 600: '#334155', 700: '#1e293b',
                    800: '#111a2b', 900: '#0a1120', 950: '#050a14',
                },
            },
            fontFamily: {
                sans: ['Inter', 'system-ui', 'sans-serif'],
                mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
            },
            boxShadow: {
                glow: '0 0 20px -4px rgba(34, 211, 238, 0.45)',
                'glow-lg': '0 0 40px -8px rgba(34, 211, 238, 0.55)',
                'glow-alert': '0 0 24px -4px rgba(245, 158, 11, 0.5)',
                'glow-danger': '0 0 24px -4px rgba(239, 68, 68, 0.5)',
                'inner-hud': 'inset 0 1px 0 0 rgba(255,255,255,0.04)',
            },
            animation: {
                'fade-in': 'fadeIn 0.35s ease-out both',
                'slide-up': 'slideUp 0.4s cubic-bezier(0.16, 1, 0.3, 1) both',
                'pulse-ring': 'pulseRing 2.4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
                'spin-slow': 'spin 14s linear infinite',
                'spin-reverse': 'spinReverse 9s linear infinite',
                scan: 'scan 7s linear infinite',
                flicker: 'flicker 4s ease-in-out infinite',
                'ticker': 'ticker 40s linear infinite',
            },
            keyframes: {
                fadeIn: { '0%': { opacity: '0' }, '100%': { opacity: '1' } },
                slideUp: {
                    '0%': { opacity: '0', transform: 'translateY(12px)' },
                    '100%': { opacity: '1', transform: 'translateY(0)' },
                },
                pulseRing: {
                    '0%, 100%': { opacity: '0.45', transform: 'scale(1)' },
                    '50%': { opacity: '0.9', transform: 'scale(1.06)' },
                },
                spinReverse: { to: { transform: 'rotate(-360deg)' } },
                scan: {
                    '0%': { transform: 'translateY(-100%)' },
                    '100%': { transform: 'translateY(100vh)' },
                },
                flicker: {
                    '0%, 100%': { opacity: '1' },
                    '48%': { opacity: '1' },
                    '50%': { opacity: '0.82' },
                    '52%': { opacity: '1' },
                },
                ticker: {
                    '0%': { transform: 'translateX(0)' },
                    '100%': { transform: 'translateX(-50%)' },
                },
            },
        },
    },
    plugins: [],
}
