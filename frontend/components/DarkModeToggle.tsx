'use client';

import { FC, useEffect, useState } from 'react';

const DarkModeToggle: FC = () => {
    const [isDark, setIsDark] = useState(false);
    const [mounted, setMounted] = useState(false);

    // Initialize theme from localStorage or system preference
    useEffect(() => {
        setMounted(true);
        const stored = localStorage.getItem('theme');
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

        const initialTheme = stored || (prefersDark ? 'dark' : 'light');
        setIsDark(initialTheme === 'dark');
        document.documentElement.setAttribute('data-theme', initialTheme);
    }, []);

    // Toggle theme
    const toggleTheme = () => {
        const newTheme = isDark ? 'light' : 'dark';
        setIsDark(!isDark);
        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('theme', newTheme);
    };

    // Prevent hydration mismatch
    if (!mounted) {
        return (
            <button className="btn btn-ghost btn-sm" disabled>
                <span className="text-lg">🌓</span>
            </button>
        );
    }

    return (
        <button
            onClick={toggleTheme}
            className="btn btn-ghost btn-sm"
            aria-label={`Switch to ${isDark ? 'light' : 'dark'} mode`}
            title={`Switch to ${isDark ? 'light' : 'dark'} mode`}
        >
            <span className="text-lg transition-transform hover:scale-110">
                {isDark ? '☀️' : '🌙'}
            </span>
        </button>
    );
};

export default DarkModeToggle;
