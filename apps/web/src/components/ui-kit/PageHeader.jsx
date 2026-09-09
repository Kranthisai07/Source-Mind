import React from "react";

/**
 * §1: "Every page follows the same content header formula: H1 page title
 * (bold, ~28px) + one line of gray descriptive subtext + often an inline blue
 * text link ... This recurs on literally every screen — a strong, consistent
 * pattern."
 *
 * `subtitle` is required, not optional. The reference calls this out as
 * exceptionless, so the component enforces it rather than leaving each page to
 * remember. `action` is the right-aligned slot; `learnMore` is the inline blue
 * docs link the reference describes sitting with the subtext.
 */
export default function PageHeader({ title, subtitle, learnMore, action }) {
    return (
        <div
            data-testid="page-header"
            className="flex items-start justify-between gap-6 mb-8"
        >
            <div className="min-w-0">
                <h1
                    data-testid="page-title"
                    className="text-h1 text-content tracking-tight"
                >
                    {title}
                </h1>
                <p
                    data-testid="page-subtitle"
                    className="text-body-lg text-content-secondary mt-1.5"
                >
                    {subtitle}
                    {learnMore && (
                        <>
                            {" "}
                            <a
                                href={learnMore.href}
                                target="_blank"
                                rel="noreferrer"
                                className="text-brand hover:text-brand-hover underline-offset-2 hover:underline sm-focusable rounded-sm"
                            >
                                {learnMore.label} ↗
                            </a>
                        </>
                    )}
                </p>
            </div>
            {action && <div className="shrink-0 flex items-center gap-2">{action}</div>}
        </div>
    );
}
