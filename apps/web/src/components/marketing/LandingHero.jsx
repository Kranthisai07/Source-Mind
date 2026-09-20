import React from "react";
import { ArrowDown } from "lucide-react";
import { DevelopLink } from "./SiteChrome";
import { ProjectIntelligenceArtwork } from "./ProjectIntelligenceArtwork";

export const LandingHero = () => (
    <section className="landing-hero" data-testid="landing-hero">
        <div className="site-container hero-inner">
            <div className="hero-copy">
                <p className="eyebrow hero-eyebrow" data-testid="hero-eyebrow"><span className="tiny-square" /> SourceMind / The shared memory layer</p>
                <h1 className="text-4xl sm:text-5xl lg:text-6xl" data-testid="hero-title">The memory behind<br /><em>everything you build.</em></h1>
                <p className="hero-description" data-testid="hero-description">Your code, conversations, and decisions. One shared memory.<br className="desktop-break" /> SourceMind keeps the context together, so your team and AI tools don’t have to start from scratch.</p>
                <div className="hero-actions">
                    <DevelopLink testId="landing-hero-cta" />
                    <a href="#how-it-works" className="text-link" data-testid="hero-how-it-works">How it works <ArrowDown size={15} /></a>
                </div>
            </div>
            <ProjectIntelligenceArtwork />
        </div>
        <div className="hero-footnote site-container" data-testid="hero-footnote"><span>Built around your project. Not another silo.</span><span>Capture. Connect. Recall.</span></div>
    </section>
);