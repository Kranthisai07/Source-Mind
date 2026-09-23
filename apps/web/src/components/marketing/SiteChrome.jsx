import React, { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { ArrowUpRight, Menu, X } from "lucide-react";
import { Button } from "../ui/button";
import ThemeToggle from "../widgets/ThemeToggle";

export const SourceMark = ({ className = "" }) => (
    <svg className={`source-mark ${className}`} viewBox="0 0 32 32" fill="none" aria-hidden="true" focusable="false">
        <path d="M4 12L16 6L28 12L16 18L4 12Z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
        <path d="M4 19L16 25L28 19" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        <circle className="source-mark-accent" cx="16" cy="6" r="2.5" />
    </svg>
);

export const DevelopLink = ({ testId, compact = false }) => (
    <Button asChild className={`develop-button ${compact ? "develop-button-compact" : ""}`}>
        <Link to="/develop" data-testid={testId}>
            <span>Develop with SourceMind</span><ArrowUpRight size={17} aria-hidden="true" />
        </Link>
    </Button>
);

export const useSitePage = (title) => {
    const { pathname, hash } = useLocation();
    useEffect(() => {
        document.title = title;
        if (hash) document.getElementById(hash.slice(1))?.scrollIntoView();
        else window.scrollTo(0, 0);
    }, [pathname, hash, title]);
};

const NAV = [
    { label: "What is SourceMind?", href: "/#concept", id: "concept" },
    { label: "How it works", href: "/#how-it-works", id: "how-it-works" },
    { label: "Developers", href: "/develop", id: "developers" },
];

export const SiteHeader = ({ developer = false }) => {
    const [menuOpen, setMenuOpen] = useState(false);
    const { pathname, hash } = useLocation();
    useEffect(() => setMenuOpen(false), [pathname, hash]);
    useEffect(() => {
        const onKey = (e) => { if (e.key === "Escape") setMenuOpen(false); };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, []);
    return (
        <header className="site-header" data-testid="site-header">
            <a className="skip-link" href="#main-content" data-testid="skip-to-content">Skip to content</a>
            <div className="site-container header-inner">
                <Link to="/" className="source-brand" data-testid="landing-logo" aria-label="SourceMind home">
                    <SourceMark /><span>SourceMind<span className="brand-period">.</span></span>
                </Link>
                <nav className="desktop-navigation" aria-label="Main navigation">
                    {NAV.map(item => <Link key={item.id} to={item.href} data-testid={`nav-${item.id}`} aria-current={developer && item.id === "developers" ? "page" : undefined}>{item.label}</Link>)}
                </nav>
                <div className="header-actions">
                    {/* The upstream marketing chrome shipped no sign-in entry
                        point, which would have left the Clerk flow unreachable
                        from the home page. Kept on the same testid the previous
                        landing used. */}
                    <Link to="/sign-in" className="site-signin" data-testid="landing-signin">Sign in</Link>
                    <ThemeToggle className="site-theme-toggle" />
                    {!developer && <div className="header-develop"><DevelopLink testId="landing-nav-cta" compact /></div>}
                    <Button variant="ghost" size="icon" className="mobile-menu-button" data-testid="mobile-menu-toggle" aria-label={menuOpen ? "Close menu" : "Open menu"} aria-expanded={menuOpen} aria-controls="mobile-navigation" onClick={() => setMenuOpen(!menuOpen)}>
                        {menuOpen ? <X size={20} /> : <Menu size={20} />}
                    </Button>
                </div>
            </div>
            {menuOpen && <nav className="mobile-navigation" id="mobile-navigation" aria-label="Mobile navigation" data-testid="mobile-navigation">
                {NAV.map(item => <Link key={item.id} to={item.href} onClick={() => setMenuOpen(false)} data-testid={`mobile-nav-${item.id}`}>{item.label}<ArrowUpRight size={16} /></Link>)}
                <Link to="/sign-in" onClick={() => setMenuOpen(false)} data-testid="mobile-nav-signin">Sign in<ArrowUpRight size={16} /></Link>
                {!developer && <DevelopLink testId="mobile-develop-cta" />}
            </nav>}
        </header>
    );
};

export const SiteFooter = () => (
    <footer className="site-footer site-container" data-testid="site-footer">
        <div>
            <Link to="/" className="source-brand footer-brand" data-testid="footer-logo"><SourceMark /><span>SourceMind<span className="brand-period">.</span></span></Link>
            <p data-testid="footer-tagline">Shared knowledge. Lasting context.</p>
        </div>
        <div className="footer-links">
            <Link to="/#how-it-works" data-testid="footer-how-it-works">How it works <ArrowUpRight size={13} /></Link>
            <Link to="/develop" data-testid="footer-developers">Developer guide <ArrowUpRight size={13} /></Link>
        </div>
        <span className="footer-copyright" data-testid="footer-copyright">© {new Date().getFullYear()} SourceMind</span>
    </footer>
);