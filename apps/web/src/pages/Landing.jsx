import React from "react";
import { Link } from "react-router-dom";
import { Brain, Link as LinkIcon, Zap, AlertTriangle, ArrowRightLeft, Plug, Check, Github } from "lucide-react";
import { Button } from "../components/ui/button";
import { CONTRIBUTORS } from "../lib/mockData";

const FEATURES = [
    { icon: Brain,           title: "Intelligent Memory",  body: "Auto-extracts facts from any text — commits, PRs, incident reports, decision records.", color: "#4F7EFF" },
    { icon: LinkIcon,        title: "Source Attribution",  body: "Knows exactly who contributed what, with weighted signals across author, edits, and reviews.", color: "#A78BFA" },
    { icon: Zap,             title: "Hybrid Search",       body: "Vector + BM25 keyword search, fused with reciprocal rank fusion. Sub-50ms at scale.", color: "#34D399" },
    { icon: AlertTriangle,   title: "Conflict Detection",  body: "Flags contradictions between memories automatically using Claude Sonnet 4.6.", color: "#F59E0B" },
    { icon: ArrowRightLeft,  title: "Team Handoff",        body: "Tiered transfer workflows for when engineers leave. No more 'that person knew everything'.", color: "#EF4444" },
    { icon: Plug,            title: "Native Connectors",   body: "GitHub (commits, PRs, issues, discussions) + Discord. Slack, Notion, Linear coming soon.", color: "#60A5FA" },
];

const TIERS = [
    { name: "Free",       price: "$0",   period: "/mo",  desc: "For individuals trying things out.", features: ["100 req/min", "1 workspace", "GitHub connector", "Community support"], cta: "Start for free", accent: "#8888A8" },
    { name: "Pro",        price: "$49",  period: "/mo",  desc: "For engineering teams.",             features: ["1,000 req/min", "Unlimited workspaces", "All connectors", "Conflict detection", "Priority support"], cta: "Start Pro trial", accent: "#4F7EFF", featured: true },
    { name: "Enterprise", price: "Custom", period: "",  desc: "For organizations at scale.",        features: ["10,000 req/min", "SSO / SAML", "SLA 99.9%", "Dedicated Slack channel", "Custom connectors"], cta: "Talk to sales", accent: "#A78BFA" },
];

export default function Landing() {
    return (
        <div className="min-h-screen bg-sm-bg text-sm-text">
            {/* Nav */}
            <nav className="max-w-7xl mx-auto flex items-center justify-between px-6 h-16">
                <Link to="/" data-testid="landing-logo" className="flex items-center gap-2.5">
                    <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-sm-blue to-sm-purple flex items-center justify-center">
                        <Brain className="w-4 h-4 text-white" strokeWidth={2.5} />
                    </div>
                    <span className="font-semibold tracking-tight">SourceMind</span>
                </Link>
                <div className="hidden md:flex items-center gap-8 text-[13px] text-sm-text-secondary">
                    <a href="#features" className="hover:text-sm-text transition-colors">Features</a>
                    <a href="#pricing"  className="hover:text-sm-text transition-colors">Pricing</a>
                    <a href="https://github.com" target="_blank" rel="noreferrer" className="hover:text-sm-text transition-colors">Docs</a>
                </div>
                <div className="flex items-center gap-2">
                    <Link to="/sign-in" data-testid="landing-signin">
                        <Button variant="ghost" size="sm" className="text-sm-text-secondary hover:text-sm-text hover:bg-white/5">Sign in</Button>
                    </Link>
                    <Link to="/sign-up" data-testid="landing-signup-top">
                        <Button size="sm" className="bg-sm-blue hover:bg-sm-blue/90 text-white shadow-[0_0_0_1px_rgba(79,126,255,0.4)_inset]">Get started</Button>
                    </Link>
                </div>
            </nav>

            {/* Hero */}
            <section className="relative overflow-hidden">
                <div className="absolute inset-0 bg-grid bg-grid-fade opacity-40" />
                <div className="absolute inset-x-0 top-0 h-[520px] bg-[radial-gradient(ellipse_60%_40%_at_50%_0%,rgba(79,126,255,0.18),transparent_70%)] pointer-events-none" />
                <div className="relative max-w-6xl mx-auto px-6 pt-24 pb-20 text-center">
                    <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-sm-border bg-sm-surface/50 text-[11.5px] text-sm-text-secondary font-mono mb-8">
                        <span className="w-1.5 h-1.5 rounded-full bg-sm-green pulse-dot relative" />
                        Now with Claude Sonnet 4.6 extraction
                    </div>
                    <h1 className="text-[44px] md:text-[64px] leading-[1.03] font-semibold tracking-tight text-sm-text">
                        Your team's knowledge,<br />
                        <span className="bg-gradient-to-r from-sm-blue via-sm-purple to-sm-green bg-clip-text text-transparent">
                            organized and searchable.
                        </span>
                    </h1>
                    <p className="mt-6 max-w-2xl mx-auto text-[16px] leading-relaxed text-sm-text-secondary">
                        SourceMind ingests GitHub, Discord, and documents — attributes knowledge to the right
                        people, and surfaces gaps before they become incidents.
                    </p>
                    <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
                        <Link to="/sign-up" data-testid="landing-hero-cta">
                            <Button size="lg" className="bg-sm-blue hover:bg-sm-blue/90 text-white h-11 px-6 shadow-[0_0_0_1px_rgba(79,126,255,0.4)_inset,0_8px_32px_-8px_rgba(79,126,255,0.4)]">
                                Start for free
                            </Button>
                        </Link>
                        <Link to="/dashboard" data-testid="landing-demo-cta">
                            <Button variant="outline" size="lg" className="h-11 px-6 border-sm-border bg-transparent hover:bg-white/[0.03] text-sm-text">
                                View demo →
                            </Button>
                        </Link>
                    </div>

                    {/* Hero visualization — knowledge graph */}
                    <KnowledgeGraphHero />
                </div>
            </section>

            {/* Features */}
            <section id="features" className="max-w-6xl mx-auto px-6 py-24">
                <div className="text-center mb-14">
                    <div className="text-[11px] uppercase tracking-[0.18em] text-sm-blue font-semibold mb-3">Features</div>
                    <h2 className="text-[32px] md:text-[40px] font-semibold tracking-tight">Built for engineering teams that ship.</h2>
                </div>
                <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {FEATURES.map((f) => (
                        <div key={f.title} className="sm-card p-6 group">
                            <div className="w-10 h-10 rounded-lg flex items-center justify-center mb-5" style={{ background: `${f.color}18`, color: f.color }}>
                                <f.icon className="w-5 h-5" strokeWidth={2} />
                            </div>
                            <h3 className="text-[16px] font-semibold mb-2 tracking-tight">{f.title}</h3>
                            <p className="text-[13.5px] text-sm-text-secondary leading-relaxed">{f.body}</p>
                        </div>
                    ))}
                </div>
            </section>

            {/* Pricing */}
            <section id="pricing" className="max-w-6xl mx-auto px-6 py-24 border-t border-sm-border">
                <div className="text-center mb-14">
                    <div className="text-[11px] uppercase tracking-[0.18em] text-sm-blue font-semibold mb-3">Pricing</div>
                    <h2 className="text-[32px] md:text-[40px] font-semibold tracking-tight">Simple, usage-based pricing.</h2>
                </div>
                <div className="grid md:grid-cols-3 gap-4">
                    {TIERS.map((t) => (
                        <div
                            key={t.name}
                            data-testid={`pricing-${t.name.toLowerCase()}`}
                            className={`sm-card p-7 relative ${t.featured ? "ring-1 ring-sm-blue/40 shadow-[0_0_0_1px_rgba(79,126,255,0.2),0_20px_60px_-20px_rgba(79,126,255,0.3)]" : ""}`}
                        >
                            {t.featured && (
                                <span className="absolute -top-2.5 left-7 px-2 py-0.5 text-[10px] font-mono tracking-wider rounded-md bg-sm-blue text-white">
                                    MOST POPULAR
                                </span>
                            )}
                            <div className="flex items-baseline justify-between mb-2">
                                <span className="text-[15px] font-semibold">{t.name}</span>
                                <span className="w-2 h-2 rounded-full" style={{ background: t.accent }} />
                            </div>
                            <div className="flex items-baseline gap-1 mb-2">
                                <span className="font-mono text-[36px] font-semibold tracking-tight">{t.price}</span>
                                <span className="text-sm-text-secondary text-[13px]">{t.period}</span>
                            </div>
                            <p className="text-[13px] text-sm-text-secondary mb-6">{t.desc}</p>
                            <ul className="space-y-2.5 mb-7">
                                {t.features.map((f) => (
                                    <li key={f} className="flex items-start gap-2 text-[13px] text-sm-text">
                                        <Check className="w-4 h-4 mt-0.5 shrink-0" style={{ color: t.accent }} />
                                        <span>{f}</span>
                                    </li>
                                ))}
                            </ul>
                            <Link to="/sign-up">
                                <Button
                                    className={t.featured ? "w-full bg-sm-blue hover:bg-sm-blue/90 text-white" : "w-full bg-white/[0.04] hover:bg-white/[0.08] text-sm-text border border-sm-border"}
                                >
                                    {t.cta}
                                </Button>
                            </Link>
                        </div>
                    ))}
                </div>
            </section>

            {/* Footer */}
            <footer className="border-t border-sm-border">
                <div className="max-w-6xl mx-auto px-6 py-10 flex flex-wrap items-center justify-between gap-4 text-[12.5px] text-sm-text-secondary">
                    <div className="flex items-center gap-2">
                        <div className="w-6 h-6 rounded-md bg-gradient-to-br from-sm-blue to-sm-purple flex items-center justify-center">
                            <Brain className="w-3 h-3 text-white" strokeWidth={2.5} />
                        </div>
                        <span>© {new Date().getFullYear()} SourceMind</span>
                    </div>
                    <div className="flex items-center gap-6 font-mono text-[11.5px]">
                        <a href="#" className="hover:text-sm-text">Privacy</a>
                        <a href="#" className="hover:text-sm-text">Terms</a>
                        <a href="#" className="hover:text-sm-text">Security</a>
                        <a href="https://github.com" target="_blank" rel="noreferrer" className="flex items-center gap-1.5 hover:text-sm-text">
                            <Github className="w-3.5 h-3.5" /> GitHub
                        </a>
                    </div>
                </div>
            </footer>
        </div>
    );
}

// Simple SVG knowledge-graph visualization — contributor nodes with glowing edges.
function KnowledgeGraphHero() {
    const nodes = CONTRIBUTORS.slice(0, 7).map((c, i) => {
        const angle = (i / 7) * Math.PI * 2 - Math.PI / 2;
        const r = 160;
        return {
            ...c,
            x: 400 + r * Math.cos(angle),
            y: 220 + r * Math.sin(angle) * 0.8,
        };
    });
    const center = { x: 400, y: 220 };

    return (
        <div className="mt-16 relative">
            <div className="sm-card p-2 inline-block w-full max-w-3xl mx-auto overflow-hidden relative">
                <svg viewBox="0 0 800 400" className="w-full h-auto">
                    <defs>
                        <radialGradient id="glow" cx="50%" cy="50%" r="50%">
                            <stop offset="0%" stopColor="#4F7EFF" stopOpacity="0.35" />
                            <stop offset="100%" stopColor="#4F7EFF" stopOpacity="0" />
                        </radialGradient>
                    </defs>
                    <circle cx={center.x} cy={center.y} r="140" fill="url(#glow)" />
                    {nodes.map((n, i) => (
                        <line
                            key={`l-${i}`}
                            x1={center.x} y1={center.y}
                            x2={n.x}      y2={n.y}
                            stroke={n.avatarColor}
                            strokeOpacity="0.4"
                            strokeWidth="1"
                            strokeDasharray="3 3"
                        />
                    ))}
                    {/* cross connections */}
                    {nodes.map((n, i) => {
                        const next = nodes[(i + 2) % nodes.length];
                        return (
                            <line key={`c-${i}`} x1={n.x} y1={n.y} x2={next.x} y2={next.y} stroke="#4F7EFF" strokeOpacity="0.12" strokeWidth="1" />
                        );
                    })}
                    {/* center brain */}
                    <circle cx={center.x} cy={center.y} r="38" fill="#12121A" stroke="#4F7EFF" strokeWidth="1.5" />
                    <text x={center.x} y={center.y + 5} textAnchor="middle" fill="#E8E8F0" fontSize="14" fontWeight="600" fontFamily="JetBrains Mono">
                        memory
                    </text>
                    {nodes.map((n, i) => (
                        <g key={`n-${i}`}>
                            <circle cx={n.x} cy={n.y} r="22" fill={n.avatarColor} opacity="0.18" />
                            <circle cx={n.x} cy={n.y} r="16" fill={n.avatarColor} />
                            <text x={n.x} y={n.y + 4} textAnchor="middle" fill="white" fontSize="10" fontWeight="700">
                                {n.name.split(" ").map(w => w[0]).join("")}
                            </text>
                            <text x={n.x} y={n.y + 36} textAnchor="middle" fill="#8888A8" fontSize="9" fontFamily="JetBrains Mono">
                                @{n.login.split(".")[0]}
                            </text>
                        </g>
                    ))}
                </svg>
            </div>
        </div>
    );
}
