import React, { useEffect, useState } from "react";
import { Sun, Moon } from "lucide-react";

const STORAGE_KEY = "sm-theme";

function currentTheme() {
    if (typeof document === "undefined") return "light";
    return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

// Self-contained light/dark switch. Flips the `dark` class on <html> and
// persists the choice; no external theme provider required.
export default function ThemeToggle({ className = "" }) {
    const [theme, setTheme] = useState("light");
    useEffect(() => setTheme(currentTheme()), []);

    const apply = (next) => {
        const root = document.documentElement;
        if (next === "dark") root.classList.add("dark");
        else root.classList.remove("dark");
        try { localStorage.setItem(STORAGE_KEY, next); } catch (e) { /* ignore */ }
        setTheme(next);
    };

    const isDark = theme === "dark";

    return (
        <button
            type="button"
            data-testid="theme-toggle"
            aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
            onClick={() => apply(isDark ? "light" : "dark")}
            className={`w-9 h-9 rounded-lg border border-sm-border bg-sm-surface text-sm-text-secondary hover:text-sm-text hover:border-sm-border-hover flex items-center justify-center transition-colors ${className}`}
        >
            {isDark ? <Sun className="w-4 h-4" strokeWidth={2} /> : <Moon className="w-4 h-4" strokeWidth={2} />}
        </button>
    );
}
