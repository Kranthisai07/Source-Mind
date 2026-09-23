import React from "react";

/**
 * §5, implemented as the literal template rather than a loose "empty state":
 *
 *   [outline icon] -> [bold ~16px headline, "No ___ yet"]
 *   -> [one gray sentence explaining what will appear and why]
 *   -> [optional blue "Learn how ↗" text link]
 *   -> [optional primary action button repeating the page's main CTA]
 *
 * The reference stresses that the value here is the *sameness* — "same vertical
 * rhythm, same icon size, same copy formula" across every zero-state in the
 * product. So the order and spacing are fixed by this component and callers
 * only supply content; there is deliberately no layout escape hatch.
 *
 * `noun` builds the headline so the "No ___ yet" formula cannot drift into
 * "Nothing here" or "No results found" on one page and not another.
 */
export default function EmptyState({
    icon: Icon,
    noun,
    headline,
    description,
    learnMore,
    action,
    testId = "empty-state",
}) {
    return (
        <div
            data-testid={testId}
            className="flex flex-col items-center justify-center text-center px-6 py-16"
        >
            {Icon && (
                <Icon
                    className="w-10 h-10 text-content-muted mb-4"
                    strokeWidth={1.5}
                    aria-hidden="true"
                />
            )}

            <h3 className="text-[16px] font-semibold text-content">
                {headline || `No ${noun} yet`}
            </h3>

            <p className="text-body text-content-secondary mt-2 max-w-[420px] leading-relaxed">
                {description}
            </p>

            {learnMore && (
                <a
                    href={learnMore.href}
                    target="_blank"
                    rel="noreferrer"
                    className="text-body text-brand hover:text-brand-hover mt-3 underline-offset-2 hover:underline sm-focusable rounded-sm"
                >
                    {learnMore.label || "Learn how"} ↗
                </a>
            )}

            {action && <div className="mt-6">{action}</div>}
        </div>
    );
}
