import React from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, ArrowUpRight, BookOpen, Layers, FileText } from "lucide-react";
import { SiteHeader, SiteFooter, useSitePage } from "../components/marketing/SiteChrome";
import { MemoryExamples } from "../components/marketing/MemoryExamples";
import "../components/marketing/marketing.css";

export default function Develop() {
    useSitePage("Develop with SourceMind — Developer guide");
    return (
        <div className="source-site" data-testid="developer-page">
            <SiteHeader developer />
            <main className="site-container developer-main" id="main-content">
                <Link to="/" className="text-link back-link" data-testid="developer-back-home"><ArrowLeft size={15} /> SourceMind</Link>
                <div className="developer-intro">
                    <p className="eyebrow" data-testid="developer-eyebrow">Developer guide / Getting started</p>
                    <h1 className="text-4xl sm:text-5xl lg:text-6xl" data-testid="developer-title">Develop with<br /><em>SourceMind.</em></h1>
                    <p className="hero-description" data-testid="developer-description">Give your project a shared memory of the decisions, knowledge, and sources behind it.</p>
                    <div className="development-notice" role="note" data-testid="developer-status"><span className="status-label">In development</span><p>The API, SDK, and connectors aren’t available yet. This guide outlines the planned memory flow; the examples run locally without sending data.</p></div>
                </div>
                <div className="developer-layout">
                    <nav className="guide-navigation" aria-label="On this page" data-testid="guide-navigation"><p className="eyebrow" data-testid="guide-nav-label">On this page</p><a href="#mental-model" data-testid="guide-nav-model">01 <span>The mental model</span></a><a href="#memory-flow" data-testid="guide-nav-flow">02 <span>The memory flow</span></a><a href="#project-plan" data-testid="guide-nav-plan">03 <span>Plan your project</span></a></nav>
                    <div className="guide-content">
                        <section id="mental-model" data-testid="guide-mental-model">
                            <h2 data-testid="guide-model-heading">01 / The mental model</h2>
                            <p className="guide-lead" data-testid="guide-model-lead">A project is the boundary.<br />A memory is the knowledge.<br />A source is the evidence.</p>
                            <p data-testid="guide-model-description">Think of SourceMind as a context layer between your project’s sources and the people or tools that need them. Keep knowledge grouped by project, preserve its origin, and retrieve the relevant pieces when a question comes up.</p>
                            <div className="guide-model" data-testid="guide-model-flow"><span><FileText size={17} /> Sources</span><ArrowUpRight size={17} aria-hidden="true" /><span><Layers size={17} /> Memory</span><ArrowUpRight size={17} aria-hidden="true" /><span><BookOpen size={17} /> Context</span></div>
                        </section>
                        <section id="memory-flow" data-testid="guide-memory-flow"><h2 data-testid="guide-flow-heading">02 / The memory flow</h2><p data-testid="guide-flow-description">Follow a single architecture decision from a source document to the context behind an answer.</p><MemoryExamples /></section>
                        <section id="project-plan" data-testid="guide-project-plan"><h2 data-testid="guide-plan-heading">03 / Plan your project</h2><ol className="project-checklist">
                            <li data-testid="plan-project"><span>01</span><div><h3>Choose a project boundary</h3><p>Keep one project’s knowledge together. Decide which team, repo, or application it belongs to.</p></div></li>
                            <li data-testid="plan-sources"><span>02</span><div><h3>Identify the sources worth remembering</h3><p>Start with architecture decisions, project notes, and important discussions. Keep authors and source references.</p></div></li>
                            <li data-testid="plan-question"><span>03</span><div><h3>Start with a question you ask often</h3><p>“Why did we build this?” or “What changed?” Define the context that would make the answer useful.</p></div></li>
                        </ol><Link to="/#how-it-works" className="text-link" data-testid="guide-back-to-how">Back to how SourceMind works <ArrowUpRight size={15} /></Link></section>
                    </div>
                </div>
            </main>
            <SiteFooter />
        </div>
    );
}