import React from "react";
import { SiteHeader, SiteFooter, DevelopLink, useSitePage } from "../components/marketing/SiteChrome";
import { LandingHero } from "../components/marketing/LandingHero";
import { MemoryStory } from "../components/marketing/MemoryStory";
import { HowItWorks, BuiltFor } from "../components/marketing/LandingSections";
import "../components/marketing/marketing.css";

export default function Landing() {
    useSitePage("SourceMind — A shared memory for everything you build");
    return (
        <div className="source-site" data-testid="landing-page">
            <SiteHeader />
            <main id="main-content">
                <LandingHero />
                <MemoryStory />
                <HowItWorks />
                <BuiltFor />
                <section className="closing-section" data-testid="landing-developer-section">
                    <div className="site-container closing-inner">
                        <div>
                            <p className="eyebrow" data-testid="closing-label">Less rediscovering. More building.</p>
                            <p className="editorial-heading" data-testid="closing-heading">Good work deserves<br /><em>a longer memory.</em></p>
                            <p className="section-description" data-testid="closing-description">Make your project’s context part of what you build next.</p>
                        </div>
                        <DevelopLink testId="landing-footer-cta" />
                    </div>
                </section>
            </main>
            <SiteFooter />
        </div>
    );
}