import React from "react";

const CAPABILITIES = [
    { id: "decisions", label: "Decisions" },
    { id: "attribution", label: "Attribution" },
    { id: "ownership", label: "Ownership" },
    { id: "conflicts", label: "Conflicts & gaps" },
    { id: "handoffs", label: "Handoffs" },
];

/*
 * PLACEHOLDER — pending image provenance.
 *
 * The upstream design shipped two raster files here,
 * /images/project-intelligence-{light,dark}.webp, with no attribution, no
 * licence and no recorded origin. They were NOT copied in: redistribution
 * rights for artwork of unknown provenance cannot be established, and this
 * repository is public.
 *
 * This inline SVG stands in for them. It is drawn here, so its rights are not
 * in question, and it renders in both themes off the --site-* variables that
 * marketing.css already scopes to .source-site.
 *
 * Layout is deliberately unchanged: the parent .intelligence-artwork-image
 * carries `aspect-ratio: 1264 / 540`, so restoring the real artwork later is a
 * one-file change with no reflow. The original alt text is preserved verbatim
 * as the accessible name.
 *
 * To restore: confirm provenance, add the two .webp files to public/images/,
 * and put back the two <img> elements with classes intelligence-art-light and
 * intelligence-art-dark. The CSS for both is still present and unmodified.
 */

const NODES = [
    { cx: 120, cy: 300, r: 15 },
    { cx: 360, cy: 160, r: 12 },
    { cx: 400, cy: 420, r: 12 },
    { cx: 640, cy: 270, r: 18 },
    { cx: 890, cy: 150, r: 12 },
    { cx: 920, cy: 400, r: 12 },
    { cx: 1140, cy: 280, r: 15 },
];

const EDGES = [
    "M120,300 C240,300 260,160 360,160",
    "M120,300 C240,300 280,420 400,420",
    "M360,160 C500,160 540,270 640,270",
    "M400,420 C520,420 560,270 640,270",
    "M640,270 C760,270 790,150 890,150",
    "M640,270 C770,270 810,400 920,400",
    "M890,150 C1020,150 1040,280 1140,280",
    "M920,400 C1030,400 1060,280 1140,280",
];

const IntelligencePlaceholder = () => (
    <svg
        viewBox="0 0 1264 540"
        role="img"
        aria-label="Interwoven sculptural paths connect decisions, source traces, knowledge owners, a flagged gap, and a handoff bridge—one connected understanding of a project."
        data-testid="intelligence-art-placeholder"
        style={{ display: "block", width: "100%", height: "auto" }}
    >
        <g fill="none" stroke="var(--site-line)" strokeWidth="2">
            {EDGES.map((d) => <path key={d} d={d} />)}
        </g>
        <g>
            {NODES.map(({ cx, cy, r }) => (
                <circle
                    key={`${cx}-${cy}`}
                    cx={cx}
                    cy={cy}
                    r={r}
                    fill="var(--site-paper)"
                    stroke="var(--site-accent)"
                    strokeWidth="2"
                />
            ))}
        </g>
        {/* The flagged gap the caption refers to. */}
        <circle cx="920" cy="400" r="26" fill="none" stroke="var(--site-orange)" strokeWidth="2" strokeDasharray="5 6" />
    </svg>
);

export const ProjectIntelligenceArtwork = () => (
    <figure className="intelligence-artwork" data-testid="hero-memory-visual" aria-label="An illustration of connected project intelligence">
        <div className="intelligence-artwork-image" data-testid="intelligence-artwork-image">
            <IntelligencePlaceholder />
        </div>
        <figcaption className="intelligence-art-caption">
            {CAPABILITIES.map(({ id, label }, index) => <span key={id} data-testid={`art-capability-${id}`}><b aria-hidden="true">0{index + 1}</b>{label}</span>)}
        </figcaption>
    </figure>
);
