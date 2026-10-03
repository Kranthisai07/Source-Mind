import React from "react";
import { appUrl } from "@/lib/appUrl";

const CAPABILITIES = [
    { id: "decisions", label: "Decisions" },
    { id: "attribution", label: "Attribution" },
    { id: "ownership", label: "Ownership" },
    { id: "conflicts", label: "Conflicts & gaps" },
    { id: "handoffs", label: "Handoffs" },
];

export const ProjectIntelligenceArtwork = () => (
    <figure className="intelligence-artwork" data-testid="hero-memory-visual" aria-label="An illustration of connected project intelligence">
        <div className="intelligence-artwork-image" data-testid="intelligence-artwork-image">
            <img className="intelligence-art-light" src={appUrl("/images/project-intelligence-light.webp")} width="1264" height="540" fetchPriority="high" alt="Interwoven sculptural paths connect decisions, source traces, knowledge owners, a flagged gap, and a handoff bridge—one connected understanding of a project." data-testid="intelligence-art-light" />
            <img className="intelligence-art-dark" src={appUrl("/images/project-intelligence-dark.webp")} width="1264" height="540" alt="Interwoven sculptural paths connect decisions, source traces, knowledge owners, a flagged gap, and a handoff bridge—one connected understanding of a project." data-testid="intelligence-art-dark" />
        </div>
        <figcaption className="intelligence-art-caption">
            {CAPABILITIES.map(({ id, label }, index) => <span key={id} data-testid={`art-capability-${id}`}><b aria-hidden="true">0{index + 1}</b>{label}</span>)}
        </figcaption>
    </figure>
);
