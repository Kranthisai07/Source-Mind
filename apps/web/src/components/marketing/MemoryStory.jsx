import React from "react";
import { FileText, GitPullRequest, MessageSquare, CornerDownRight, Link2 } from "lucide-react";

const SOURCES = [
    { id: "decision", icon: FileText, type: "Decision record", title: "Why we chose PostgreSQL", note: "The reasoning, not just the outcome.", color: "green" },
    { id: "discussion", icon: MessageSquare, type: "Team conversation", title: "What changed along the way", note: "The trade-offs behind the decision.", color: "blue" },
    { id: "pull-request", icon: GitPullRequest, type: "Code & pull requests", title: "Where the decision became code", note: "The implementation, with its history.", color: "orange" },
];

export const MemoryStory = () => (
    <section id="concept" className="site-container concept-section editorial-section" data-testid="concept-section">
        <div className="section-intro">
            <h2 className="eyebrow" data-testid="concept-section-label"><span className="section-index">01 /</span> What is SourceMind?</h2>
            <div>
                <p className="editorial-heading" data-testid="concept-heading">Your tools hold the pieces.<br /><em>SourceMind holds the thread.</em></p>
                <p className="section-description" data-testid="concept-description">SourceMind is a shared memory store for your project. It’s designed to connect knowledge scattered across documents, code, and conversations—and keep the original source attached.</p>
                <p className="concept-callout" data-testid="concept-callout">Not just “where is that file?”<br /><strong>“Why did we build it this way?”</strong></p>
            </div>
        </div>
        <div className="source-story" data-testid="source-story">
            <div className="source-story-header"><span className="eyebrow" data-testid="source-story-label">One decision. The full story.</span><span className="example-label" data-testid="source-story-example">Illustrative example</span></div>
            <div className="source-documents">
                {SOURCES.map(({ id, icon: Icon, type, title, note, color }) => (
                    <article className="source-document" key={id} data-testid={`source-document-${id}`}>
                        <div className={`source-type ${color}`}><Icon size={17} aria-hidden="true" /><span data-testid={`source-type-${id}`}>{type}</span></div>
                        <h3 data-testid={`source-title-${id}`}>{title}</h3><p data-testid={`source-note-${id}`}>{note}</p>
                    </article>
                ))}
            </div>
            <div className="connected-context" data-testid="connected-context"><CornerDownRight size={20} aria-hidden="true" /><p data-testid="connected-context-description">One connected memory. The decision, the discussion, and the people behind it.</p><span data-testid="source-trace-label"><Link2 size={13} /> Always traceable</span></div>
        </div>
    </section>
);