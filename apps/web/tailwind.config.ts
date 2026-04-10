import type { Config } from 'tailwindcss'

const config: Config = {
  darkMode: ['class'],
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        bg: '#0A0A0F',
        surface: '#111118',
        elevated: '#1A1A24',
        border: '#2A2A3A',
        'border-active': '#3A3A4F',
        primary: '#E8E8F0',
        secondary: '#8888A0',
        muted: '#4A4A5A',
        accent: {
          blue: '#4F8EF7',
          purple: '#8B5CF6',
          green: '#10B981',
          amber: '#F59E0B',
          red: '#EF4444',
          cyan: '#06B6D4',
          pink: '#EC4899',
          lime: '#84CC16',
        },
      },
      fontFamily: {
        display: ['Fraunces', 'Georgia', 'serif'],
        sans: ['Geist', 'system-ui', 'sans-serif'],
        mono: ['GeistMono', 'JetBrains Mono', 'monospace'],
      },
      borderRadius: {
        lg: '8px',
        md: '6px',
        sm: '4px',
      },
      animation: {
        shimmer: 'shimmer 2s linear infinite',
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'draw-arc': 'draw-arc 0.8s ease-out forwards',
      },
      keyframes: {
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
      backgroundImage: {
        'gradient-accent': 'linear-gradient(135deg, #4F8EF7, #8B5CF6)',
        'gradient-subtle': 'linear-gradient(135deg, rgba(79,142,247,0.1), rgba(139,92,246,0.1))',
        shimmer: 'linear-gradient(90deg, transparent 25%, rgba(255,255,255,0.04) 50%, transparent 75%)',
      },
    },
  },
  plugins: [],
}

export default config
