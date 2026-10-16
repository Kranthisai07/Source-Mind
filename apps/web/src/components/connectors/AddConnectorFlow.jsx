import React, { useEffect, useRef, useState } from "react";
import { Github, MessageSquare, FileText } from "lucide-react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import api from "../../lib/api";
import { describeConnectorError, isGithubAuthorizeUrl, redirectTo } from "./connectorText";

// Real connect flow: authorize (browser leaves for GitHub) -> callback page
// redeems code+state -> pick repositories -> consent -> create. There is no
// path that reports success without the create call having succeeded.
export default function AddConnectorFlow({ callback, onCreated, onClose }) {
    const [phase, setPhase] = useState(callback ? "redeeming" : "choose");
    const [error, setError] = useState(null);
    const [ticket, setTicket] = useState(null);
    const started = useRef(false);
    const alive = useRef(true);

    useEffect(() => {
        alive.current = true;
        return () => { alive.current = false; };
    }, []);

    // Redeem the callback exactly once, even if this effect re-runs.
    useEffect(() => {
        if (!callback || started.current) return;
        started.current = true;
        if (callback.denied || !callback.code || !callback.state) {
            setError(callback.denied
                ? "GitHub did not grant access (the authorization was cancelled or denied). Nothing was connected."
                : "GitHub did not return the information needed to finish connecting. Start again.");
            setPhase("error");
            return;
        }
        api.completeGithubCallback({
            code: callback.code,
            state: callback.state,
            installation_id: callback.installation_id,
        }).then(
            (result) => {
                if (!alive.current) return;
                setTicket(result);
                setPhase("pick");
            },
            (requestError) => {
                if (!alive.current) return;
                setError(describeConnectorError(requestError, "GitHub authorization could not be completed."));
                setPhase("error");
            }
        );
    }, [callback]);

    const authorize = async () => {
        setPhase("authorizing");
        setError(null);
        try {
            const result = await api.authorizeGithub();
            if (!alive.current) return;
            if (!isGithubAuthorizeUrl(result?.authorize_url)) {
                setError("The server returned an unexpected address, so we did not open it.");
                setPhase("error");
                return;
            }
            redirectTo(result.authorize_url);
        } catch (requestError) {
            if (!alive.current) return;
            setError(describeConnectorError(requestError, "Could not start the GitHub connection."));
            setPhase("error");
        }
    };

    const restart = () => { setError(null); setTicket(null); setPhase("choose"); };

    return (
        <div data-testid="add-connector-flow">
            {phase === "choose" && (
                <div className="p-6 space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                        <TypeCard icon={Github} name="GitHub" desc="PR and issue text, reviews, comments, commit messages" color="#E8E8F0" onClick={() => setPhase("explain")} testId="type-github" />
                        <TypeCard disabled icon={MessageSquare} name="Discord" desc="Coming soon" color="#8888A8" testId="type-discord" />
                        <TypeCard disabled icon={MessageSquare} name="Slack" desc="Coming soon" color="#8888A8" testId="type-slack" />
                        <TypeCard disabled icon={FileText} name="Notion" desc="Coming soon" color="#8888A8" testId="type-notion" />
                    </div>
                </div>
            )}

            {phase === "explain" && (
                <div className="p-6 space-y-4">
                    <p className="text-[13px] text-sm-text">
                        You will go to GitHub to approve access. SourceMind then shows the repositories you can reach so you choose which ones to import. Nothing is imported until you confirm.
                    </p>
                    <div className="flex items-center gap-2">
                        <Button onClick={() => setPhase("choose")} variant="outline" className="bg-white/[0.03] border-sm-border text-sm-text">Back</Button>
                        <Button onClick={authorize} data-testid="github-authorize-btn" className="flex-1 bg-sm-blue hover:bg-sm-blue/90 text-white">Continue to GitHub</Button>
                    </div>
                </div>
            )}

            {phase === "authorizing" && (
                <p data-testid="github-authorizing" role="status" className="p-6 text-[13px] text-sm-text-secondary">Opening GitHub…</p>
            )}

            {phase === "redeeming" && (
                <p data-testid="github-redeeming" role="status" className="p-6 text-[13px] text-sm-text-secondary">Finishing the GitHub connection…</p>
            )}

            {phase === "error" && (
                <div className="p-6 space-y-4">
                    <p role="alert" data-testid="add-flow-error" className="text-[13px] text-sm-red">{error}</p>
                    <div className="flex gap-2">
                        <Button onClick={restart} data-testid="add-flow-restart" className="bg-sm-blue hover:bg-sm-blue/90 text-white">Start again</Button>
                        <Button onClick={onClose} variant="outline" className="bg-white/[0.03] border-sm-border text-sm-text">Close</Button>
                    </div>
                </div>
            )}

            {phase === "pick" && ticket && (
                <RepoPicker ticket={ticket} onCreated={onCreated} onCancel={onClose} />
            )}
        </div>
    );
}

function RepoPicker({ ticket, onCreated, onCancel }) {
    // Only repositories the server listed are ever offered or sent.
    const offered = Array.isArray(ticket.repositories) ? ticket.repositories : [];
    const [selected, setSelected] = useState(() => new Set());
    const [consent, setConsent] = useState(false);
    const [name, setName] = useState(`GitHub: ${ticket.github_login || "account"}`);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const alive = useRef(true);
    useEffect(() => {
        alive.current = true;
        return () => { alive.current = false; };
    }, []);

    const toggle = (fullName) => {
        const next = new Set(selected);
        if (next.has(fullName)) next.delete(fullName); else next.add(fullName);
        setSelected(next);
    };

    const repos = offered.map((r) => r.full_name).filter((n) => selected.has(n));
    const canSubmit = repos.length > 0 && consent && name.trim().length > 0 && !busy;

    const submit = async () => {
        if (!canSubmit) return;
        setBusy(true);
        setError(null);
        try {
            const created = await api.createConnector(ticket.workspace_id, {
                connector_type: "github",
                display_name: name.trim(),
                config: { repos, consent_workspace_readable: true },
                ticket_id: ticket.ticket_id,
            });
            if (alive.current) onCreated(created);
        } catch (requestError) {
            if (alive.current) setError(describeConnectorError(requestError, "The connector was not created."));
        } finally {
            if (alive.current) setBusy(false);
        }
    };

    return (
        <div className="p-6 space-y-5" data-testid="repo-picker">
            <div>
                <label className="block text-[12px] text-sm-text-secondary mb-2 font-medium" htmlFor="connector-name">Name</label>
                <Input id="connector-name" data-testid="connector-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={255} className="bg-sm-bg/60 border-sm-border text-[12.5px]" />
            </div>
            <fieldset>
                <legend className="block text-[12px] text-sm-text-secondary mb-2 font-medium">
                    Repositories {ticket.github_login ? `available to ${ticket.github_login}` : "available to you"}
                </legend>
                {offered.length === 0 ? (
                    <p data-testid="repo-picker-empty" className="text-[12.5px] text-sm-text-secondary">
                        No repositories are available through this GitHub authorization. Check the GitHub App installation and connect again.
                    </p>
                ) : (
                    <div className="space-y-2 max-h-64 overflow-y-auto">
                        {offered.map((r) => (
                            <label key={r.full_name} className="flex items-center gap-2.5 h-9 px-3 rounded-md border border-sm-border bg-sm-bg/40 cursor-pointer">
                                <input
                                    type="checkbox"
                                    data-testid={`repo-${r.full_name}`}
                                    checked={selected.has(r.full_name)}
                                    onChange={() => toggle(r.full_name)}
                                />
                                <span className="font-mono text-[12px] text-sm-text">{r.full_name}</span>
                                <span className="ml-auto font-mono text-[10px] text-sm-text-secondary">{r.private ? "private" : "public"}</span>
                            </label>
                        ))}
                    </div>
                )}
                {ticket.truncated && (
                    <p data-testid="repo-picker-truncated" className="mt-2 text-[11.5px] text-sm-amber">
                        The list was cut short by GitHub, so some repositories may be missing.
                    </p>
                )}
            </fieldset>
            <label className="flex items-start gap-2.5 rounded-md border border-sm-border p-3 cursor-pointer">
                <input type="checkbox" data-testid="consent-checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-1" />
                <span className="text-[12.5px] text-sm-text">
                    I understand that everyone in this workspace can read the imported content, including content from private repositories.
                </span>
            </label>
            {error && <p role="alert" data-testid="create-error" className="text-[12.5px] text-sm-red">{error}</p>}
            <div className="flex items-center gap-2 pt-2">
                <Button onClick={onCancel} variant="outline" className="bg-white/[0.03] border-sm-border text-sm-text">Cancel</Button>
                <Button onClick={submit} disabled={!canSubmit} data-testid="connector-connect-btn" className="flex-1 bg-sm-blue hover:bg-sm-blue/90 text-white disabled:opacity-50">
                    {busy ? "Connecting…" : "Connect"}
                </Button>
            </div>
        </div>
    );
}

function TypeCard({ icon: Icon, name, desc, color, onClick, disabled, testId }) {
    return (
        <button
            type="button"
            data-testid={testId}
            onClick={onClick}
            disabled={disabled}
            className={`rounded-lg border p-5 text-left transition-all ${disabled ? "border-sm-border bg-sm-surface/40 opacity-50 cursor-not-allowed" : "border-sm-border bg-sm-bg/40 hover:border-sm-blue/50 hover:bg-sm-blue/5"}`}
        >
            <div className="w-10 h-10 rounded-lg bg-sm-surface border border-sm-border flex items-center justify-center mb-3">
                <Icon className="w-5 h-5" style={{ color }} />
            </div>
            <div className="text-[14px] font-semibold text-sm-text mb-1">{name}</div>
            <div className="text-[11.5px] text-sm-text-secondary">{desc}</div>
        </button>
    );
}
