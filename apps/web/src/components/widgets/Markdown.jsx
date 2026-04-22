import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// SourceMind markdown renderer — dark-theme styled, safe by default.
// Used in MemoryDetail for rendering memory content.
export default function Markdown({ children, className = "" }) {
    return (
        <div className={`sm-md text-[15px] leading-[1.7] text-sm-text ${className}`}>
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                skipHtml
                components={{
                    h1: ({ node, ...p }) => <h1 className="text-[22px] font-semibold text-sm-text mt-6 mb-3 tracking-tight" {...p} />,
                    h2: ({ node, ...p }) => <h2 className="text-[18px] font-semibold text-sm-text mt-5 mb-2 tracking-tight" {...p} />,
                    h3: ({ node, ...p }) => <h3 className="text-[15px] font-semibold text-sm-text mt-4 mb-2" {...p} />,
                    p:  ({ node, ...p }) => <p className="my-3 text-sm-text" {...p} />,
                    a:  ({ node, ...p }) => <a className="text-sm-blue hover:underline underline-offset-2" target="_blank" rel="noreferrer" {...p} />,
                    ul: ({ node, ...p }) => <ul className="my-3 list-disc pl-5 space-y-1 marker:text-sm-text-muted" {...p} />,
                    ol: ({ node, ...p }) => <ol className="my-3 list-decimal pl-5 space-y-1 marker:text-sm-text-muted" {...p} />,
                    li: ({ node, ...p }) => <li className="text-sm-text" {...p} />,
                    strong: ({ node, ...p }) => <strong className="font-semibold text-sm-text" {...p} />,
                    em:     ({ node, ...p }) => <em className="italic text-sm-text" {...p} />,
                    blockquote: ({ node, ...p }) => (
                        <blockquote className="my-4 border-l-2 border-sm-blue/60 pl-4 text-sm-text-secondary italic" {...p} />
                    ),
                    hr: () => <hr className="my-6 border-sm-border" />,
                    code: ({ inline, className: c, children: ch, ...p }) => {
                        if (inline) {
                            return (
                                <code className="font-mono text-[0.88em] px-1.5 py-0.5 rounded-md bg-sm-surface/80 border border-sm-border text-sm-blue" {...p}>
                                    {ch}
                                </code>
                            );
                        }
                        return (
                            <code className={`font-mono text-[12.5px] ${c || ""}`} {...p}>
                                {ch}
                            </code>
                        );
                    },
                    pre: ({ node, ...p }) => (
                        <pre
                            className="my-4 p-4 rounded-lg bg-[#0A0A0F] border border-sm-border overflow-x-auto text-[12.5px] leading-[1.6] font-mono"
                            {...p}
                        />
                    ),
                    table: ({ node, ...p }) => (
                        <div className="my-4 overflow-x-auto rounded-lg border border-sm-border">
                            <table className="w-full text-[12.5px]" {...p} />
                        </div>
                    ),
                    thead: ({ node, ...p }) => <thead className="bg-sm-surface/50" {...p} />,
                    th: ({ node, ...p }) => <th className="text-left font-medium text-sm-text-secondary px-3 py-2 border-b border-sm-border" {...p} />,
                    td: ({ node, ...p }) => <td className="px-3 py-2 border-b border-sm-border/40 text-sm-text" {...p} />,
                }}
            >
                {children || ""}
            </ReactMarkdown>
        </div>
    );
}
