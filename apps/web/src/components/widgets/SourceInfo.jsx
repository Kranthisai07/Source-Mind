import React from "react";
import { ExternalLink } from "lucide-react";
import { relativeTime } from "../../lib/format";
import { authorshipLabel, revisionLabel, safeHttpsUrl, sourceTypeLabel, staleNotice } from "../../lib/sourceView";

const stop = (event) => event.stopPropagation();

function SafeLink({ href, children, testId }) {
    // Opens in a new tab; stopPropagation keeps a click on a card from also
    // navigating to the memory.
    return (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            data-testid={testId}
            onClick={stop}
            onKeyDown={stop}
            className="inline-flex items-center gap-1 text-sm-blue hover:underline"
        >
            {children}
            <ExternalLink className="w-3 h-3" aria-hidden="true" />
        </a>
    );
}

/**
 * Source line for a memory: type label with link, revision, authorship, and a
 * stale notice. Renders nothing for a memory with no external source.
 */
export default function SourceInfo({ memory, testPrefix = "source" }) {
    const source = memory?.source;
    const authorship = authorshipLabel(memory?.authorship);
    const stale = staleNotice(memory?.source_state);
    if (!source && !authorship && !stale) return null;

    const typeLabel = sourceTypeLabel(source);
    const url = safeHttpsUrl(source?.url);
    const revision = revisionLabel(source);

    return (
        <div data-testid={`${testPrefix}-info`} className="flex flex-col gap-1.5 text-[11.5px] text-sm-text-secondary">
            {(typeLabel || revision) && (
                <div className="flex items-center gap-2 flex-wrap">
                    {typeLabel && (url
                        ? <SafeLink href={url} testId={`${testPrefix}-link`}>{typeLabel}</SafeLink>
                        : <span data-testid={`${testPrefix}-label`}>{typeLabel}</span>)}
                    {revision && <span data-testid={`${testPrefix}-revision`} className="font-mono text-[10.5px]">{revision}</span>}
                </div>
            )}
            {authorship && <div data-testid={`${testPrefix}-authorship`}>{authorship}</div>}
            {stale && (
                <div data-testid={`${testPrefix}-stale`} role="status" className="rounded-md border border-sm-amber/30 bg-sm-amber/10 px-2.5 py-1.5 text-sm-amber">
                    <span className="font-medium">Source changed since this was imported.</span>{" "}
                    {stale.text}
                    {stale.since && <> ({relativeTime(stale.since)})</>}
                    {stale.newerUrl && (
                        <> <SafeLink href={stale.newerUrl} testId={`${testPrefix}-newer-link`}>View the newer source</SafeLink></>
                    )}
                </div>
            )}
        </div>
    );
}
