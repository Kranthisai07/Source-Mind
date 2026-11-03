import React from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";

const STEPS = [
    { n: "01", title: "Add a memory", text: "Write down a decision or paste a document.", to: "/memories", testId: "getstarted-memory" },
    { n: "02", title: "Connect GitHub", text: "Bring in commits, pull requests and issues.", to: "/connectors", testId: "getstarted-connect" },
    { n: "03", title: "Invite your team", text: "Founders, an admin and teammates, by link.", to: "/settings", testId: "getstarted-invite" },
];

/** First-run strip for an empty workspace: three ways to get going. */
export default function GetStarted() {
    return (
        <section data-testid="get-started" className="sm-card p-6">
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-sm-text-muted">Get started</p>
            <h2 className="font-serif text-[22px] font-normal leading-tight text-sm-text mt-1">Nothing here yet. Three ways in.</h2>
            <div className="grid gap-3 mt-4 sm:grid-cols-3">
                {STEPS.map((s) => (
                    <Link key={s.n} to={s.to} data-testid={s.testId} className="group rounded border border-sm-border p-4 hover:border-sm-border-hover transition-colors">
                        <div className="flex items-center justify-between font-mono text-[10px] text-sm-text-muted">
                            <span>{s.n}</span>
                            <ArrowUpRight className="w-3.5 h-3.5 group-hover:text-sm-text" />
                        </div>
                        <div className="mt-2 text-[14px] font-medium text-sm-text">{s.title}</div>
                        <div className="mt-1 text-[12.5px] text-sm-text-secondary">{s.text}</div>
                    </Link>
                ))}
            </div>
        </section>
    );
}
