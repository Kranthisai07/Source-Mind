import React, { useState } from "react";
import { Copy, Check } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../ui/button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../ui/tabs";

const EXAMPLES = [
    { id: "memory", label: "Memory", title: "A decision with its source attached.", description: "A memory brings together a useful fact, its project, and the source that gives it meaning.", data: { project: "atlas", content: "We chose PostgreSQL for transactional consistency.", source: { type: "decision_record", reference: "architecture/database-decision.md", author: "Alex" }, tags: ["architecture", "database"] } },
    { id: "question", label: "Question", title: "A question, scoped to the right project.", description: "Retrieval starts with a question. Keeping a project boundary helps prevent unrelated knowledge from entering the conversation.", data: { project: "atlas", question: "Why did we choose PostgreSQL?", include: ["related_memories", "sources"] } },
    { id: "context", label: "Context", title: "The reasoning, ready to build on.", description: "Useful context includes more than an answer: it preserves the original source so people and tools can check the reasoning.", data: { project: "atlas", context: "PostgreSQL was chosen for transactional consistency.", sources: [{ reference: "architecture/database-decision.md", author: "Alex" }], related_topics: ["architecture", "database"] } },
];

export const MemoryExamples = () => {
    const [tab, setTab] = useState("memory");
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(JSON.stringify(EXAMPLES.find(item => item.id === tab).data, null, 2));
            setCopied(true);
            toast.success("Example copied", { "data-testid": "example-copy-confirmation" });
        } catch { toast.error("Couldn’t copy. Select and copy the example text instead.", { "data-testid": "example-copy-error" }); }
    };
    return (
        <Tabs value={tab} onValueChange={value => { setTab(value); setCopied(false); }} className="memory-examples" data-testid="memory-examples">
            <div className="example-toolbar">
                <TabsList className="example-tabs" aria-label="Memory flow examples" data-testid="example-tabs">
                    {EXAMPLES.map(item => <TabsTrigger className="example-tab" key={item.id} value={item.id} data-testid={`example-tab-${item.id}`}>{item.label}</TabsTrigger>)}
                </TabsList>
                <Button size="icon" variant="ghost" onClick={copy} title={copied ? "Copied" : "Copy JSON example"} aria-label={copied ? "Copied" : "Copy JSON example"} data-testid="copy-example-button">{copied ? <Check size={16} /> : <Copy size={16} />}</Button>
            </div>
            {EXAMPLES.map(item => <TabsContent key={item.id} value={item.id} className="example-panel" data-testid={`example-panel-${item.id}`}>
                <div className="example-explanation"><h3 data-testid={`example-title-${item.id}`}>{item.title}</h3><p data-testid={`example-description-${item.id}`}>{item.description}</p></div>
                <pre data-testid={`example-code-${item.id}`}><code>{JSON.stringify(item.data, null, 2)}</code></pre>
            </TabsContent>)}
            <p className="example-disclaimer" data-testid="example-disclaimer">Conceptual data example · Not a published API contract</p>
        </Tabs>
    );
};