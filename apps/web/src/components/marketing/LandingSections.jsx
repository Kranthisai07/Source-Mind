import React, { useState } from "react";
import { ArrowRight, Braces, FileInput, Network, Search, Users, History, ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../ui/tabs";

const STEPS = [
    { number: "01", title: "Capture the knowledge.", icon: FileInput, description: "Start with the source: a document, a code change, or a team discussion. Keep the details that explain your project.", annotation: "Sources → project knowledge" },
    { number: "02", title: "Connect the context.", icon: Network, description: "Bring related decisions and facts together. Preserve who contributed them, where they came from, and what changed.", annotation: "Knowledge → shared memory" },
    { number: "03", title: "Recall what matters.", icon: Search, description: "Ask a question in your own words. Find the relevant memory and its sources, ready for your team or your AI tools.", annotation: "Shared memory → useful answers" },
];

export const HowItWorks = () => (
    <section id="how-it-works" className="how-section" data-testid="how-it-works-section">
        <div className="site-container editorial-section">
            <div className="section-intro">
                <h2 className="eyebrow" data-testid="how-section-label"><span className="section-index">02 /</span> How it works</h2>
                <div><p className="editorial-heading" data-testid="how-heading">From scattered information<br /><em>to shared understanding.</em></p><p className="section-description" data-testid="how-description">A simple idea: knowledge becomes more useful when it remembers where it came from.</p></div>
            </div>
            <div className="how-steps">
                {STEPS.map(({ number, title, icon: Icon, description, annotation }, index) => (
                    <article className="how-step" key={number} data-testid={`how-step-${number}`}>
                        <div className="step-top"><span className="step-number" data-testid={`step-number-${number}`}>{number}</span><Icon size={23} strokeWidth={1.5} aria-hidden="true" />{index < 2 && <ArrowRight className="step-arrow" size={19} aria-hidden="true" />}</div>
                        <h3 data-testid={`step-title-${number}`}>{title}</h3><p data-testid={`step-description-${number}`}>{description}</p><span className="step-annotation" data-testid={`step-annotation-${number}`}>{annotation}</span>
                    </article>
                ))}
            </div>
        </div>
    </section>
);

const USE_CASES = [
    { id: "teams", title: "For your team", icon: Users, heading: "Keep the knowledge. Even when the team changes.", description: "Give a new teammate the reasoning behind the repo—not a week of searching through old messages. Keep decisions, ownership, and project history connected.", question: "Why did we choose this architecture?", context: "The original decision, the alternatives considered, and the people who made the call.", tag: "Team knowledge & onboarding" },
    { id: "ai", title: "For your AI tools", icon: Braces, heading: "Give your tools context beyond the current conversation.", description: "Design assistants around your project’s decisions and conventions. A shared memory layer gives AI tools a consistent source of context beyond a single session.", question: "What should I know before changing this service?", context: "Related design decisions, project conventions, and source references for the task at hand.", tag: "Context-aware applications" },
    { id: "projects", title: "For the long run", icon: History, heading: "Pick up where your project left off.", description: "Projects evolve. The reasons behind them shouldn’t disappear. Keep a thread from the first decision through the latest change, even across handoffs.", question: "What changed since the last handoff?", context: "The decisions that changed, the work that followed, and the sources to revisit.", tag: "Project continuity & handoffs" },
];

export const BuiltFor = () => {
    const [active, setActive] = useState("teams");
    return (
        <section className="site-container editorial-section built-for" data-testid="built-for-section">
            <div className="section-intro">
                <h2 className="eyebrow" data-testid="built-for-label"><span className="section-index">03 /</span> Built for context</h2>
                <p className="editorial-heading" data-testid="built-for-heading">Same project. Shared memory.<br /><em>More ways to build on it.</em></p>
            </div>
            <Tabs value={active} onValueChange={setActive} className="use-case-tabs" data-testid="use-case-tabs">
                <TabsList className="use-case-tab-list" aria-label="Ways to use SourceMind" data-testid="use-case-tab-list">
                    {USE_CASES.map(({ id, title, icon: Icon }) => <TabsTrigger value={id} key={id} className="use-case-tab" data-testid={`use-case-tab-${id}`}><Icon size={15} aria-hidden="true" /><span>{title}</span></TabsTrigger>)}
                </TabsList>
                {USE_CASES.map(item => <TabsContent value={item.id} key={item.id} className="use-case-content" data-testid={`use-case-content-${item.id}`}>
                    <div><h3 data-testid={`use-case-title-${item.id}`}>{item.heading}</h3><p data-testid={`use-case-description-${item.id}`}>{item.description}</p><Link to="/develop" className="text-link" data-testid={`use-case-guide-${item.id}`}>Explore the developer guide <ArrowUpRight size={15} /></Link></div>
                    <div className="context-example" data-testid={`context-example-${item.id}`}><span className="eyebrow" data-testid={`context-label-${item.id}`}>{item.tag}</span><blockquote data-testid={`context-question-${item.id}`}>“{item.question}”</blockquote><div><ArrowRight size={16} aria-hidden="true" /><p data-testid={`context-answer-${item.id}`}>{item.context}</p></div></div>
                </TabsContent>)}
            </Tabs>
        </section>
    );
};