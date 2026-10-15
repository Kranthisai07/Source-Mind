import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import TopBar from "../components/layout/TopBar";
import { Button } from "../components/ui/button";
import api from "../lib/api";
import { classifyApiError } from "../lib/apiError";

const DAY_MS = 24 * 60 * 60 * 1000;
const FILTERS = [
    ["all", "All"],
    ["commits", "Commits"],
    ["pull_requests", "PRs"],
    ["reviews", "Reviews"],
    ["issues", "Issues"],
];

function githubProfileUrl(login, githubUserId) {
    if (githubUserId === null || githubUserId === undefined) return null;
    if (!/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/.test(login || "")) return null;
    const url = new URL(`https://github.com/${login}`);
    return url.protocol === "https:" ? url.toString() : null;
}

function lastActive(value) {
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "Unknown";
    return new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
    }).format(date);
}

export default function Contributors() {
    const { id } = useParams();
    const [days, setDays] = useState(30);
    const [type, setType] = useState("all");
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);
    const generation = useRef(0);
    const alive = useRef(true);

    useEffect(() => {
        alive.current = true;
        return () => { alive.current = false; };
    }, []);

    const load = useCallback(() => {
        const mine = ++generation.current;
        const until = new Date();
        const since = new Date(until.getTime() - days * DAY_MS);
        setLoading(true);
        setData(null);
        setError(null);
        return api.getGithubInsights(undefined, {
            connector_id: id,
            since: since.toISOString(),
            until: until.toISOString(),
            include_disconnected: false,
        }).then(
            (response) => {
                if (!alive.current || mine !== generation.current) return;
                setData(response);
                setLoading(false);
            },
            (requestError) => {
                if (!alive.current || mine !== generation.current) return;
                setError(classifyApiError(requestError));
                setLoading(false);
            }
        );
    }, [days, id]);

    useEffect(() => { load(); }, [load]);

    const contributors = useMemo(() => {
        const rows = data?.contributors || [];
        if (type === "all") return rows;
        if (type === "reviews") {
            return rows.filter((row) => (row.reviews || 0) + (row.review_comments || 0) > 0);
        }
        return rows.filter((row) => (row[type] || 0) > 0);
    }, [data, type]);

    const coverage = data?.coverage;
    const coverageText = coverage?.scopes_incomplete > 0
        ? `Imported window only; ${coverage.scopes_incomplete} of ${coverage.scopes_total} scopes still importing`
        : `Imported window only; all ${coverage?.scopes_total ?? 0} scopes imported`;

    return (
        <>
            <TopBar
                title="Contributors"
                subtitle="Stored GitHub activity from the imported window"
                actions={
                    <Button asChild variant="outline" size="sm" className="border-sm-border text-sm-text">
                        <Link to="/connectors"><ArrowLeft className="w-3.5 h-3.5" /> Connectors</Link>
                    </Button>
                }
            />
            <div className="flex-1 px-8 py-6 space-y-4">
                <div className="flex flex-wrap items-end gap-4">
                    <label className="text-[12px] text-sm-text-secondary">
                        <span className="block mb-1 font-mono uppercase tracking-wider">Window</span>
                        <select data-testid="window-selector" value={days} onChange={(event) => setDays(Number(event.target.value))} className="h-9 rounded-md border border-sm-border bg-sm-surface px-3 text-[13px] text-sm-text">
                            <option value={30}>30 days</option>
                            <option value={90}>90 days</option>
                        </select>
                    </label>
                    <label className="text-[12px] text-sm-text-secondary">
                        <span className="block mb-1 font-mono uppercase tracking-wider">Activity type</span>
                        <select data-testid="type-filter" value={type} onChange={(event) => setType(event.target.value)} className="h-9 rounded-md border border-sm-border bg-sm-surface px-3 text-[13px] text-sm-text">
                            {FILTERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                        </select>
                    </label>
                </div>

                {loading && <div data-testid="contributors-loading" role="status" className="sm-card p-6 text-[13px] text-sm-text-secondary">Loading contributor activity…</div>}

                {error && (
                    <div data-testid="contributors-error" role="alert" className="sm-card p-6 border-sm-red/30">
                        <div className="text-[14px] font-semibold text-sm-text">{error.title}</div>
                        <p className="mt-1 text-[12.5px] text-sm-text-secondary">{error.detail}</p>
                        {error.retryable && <Button data-testid="contributors-retry" onClick={load} size="sm" className="mt-4 bg-sm-blue text-white"><RefreshCw className="w-3.5 h-3.5" /> Retry</Button>}
                    </div>
                )}

                {data && (
                    <>
                        <section data-testid="insights-banner" className="rounded-lg border border-sm-blue/30 bg-sm-blue/10 p-4 text-[12.5px] text-sm-text">
                            <p className="font-medium">{coverageText}</p>
                            {(data.notes || []).map((note) => <p key={note} className="mt-1 text-sm-text-secondary">{note}</p>)}
                        </section>

                        {data.contributors.length === 0 ? (
                            <div data-testid="contributors-empty" className="sm-card p-8 text-center text-[13px] text-sm-text-secondary">No imported GitHub activity in this window</div>
                        ) : (
                            <section className="sm-card overflow-x-auto">
                                <div className="px-4 py-3 border-b border-sm-border text-[11.5px] text-sm-text-secondary">Co-authored activity is listed separately; it is not counted as full individual credit.</div>
                                {contributors.length === 0 ? (
                                    <p data-testid="contributors-filter-empty" className="p-6 text-[13px] text-sm-text-secondary">No contributors match this activity filter.</p>
                                ) : (
                                    <table data-testid="contributors-table" className="w-full min-w-[940px] text-[12px]">
                                        <thead><tr className="text-left font-mono text-[10px] uppercase tracking-wider text-sm-text-secondary border-b border-sm-border">
                                            <th className="px-4 py-3">Contributor</th><th className="px-3 py-3">Linked member</th>
                                            <th className="px-3 py-3 text-right">Commits</th><th className="px-3 py-3 text-right">PRs</th>
                                            <th className="px-3 py-3 text-right">Reviews</th><th className="px-3 py-3 text-right">Issues</th>
                                            <th className="px-3 py-3 text-right">Comments</th><th className="px-3 py-3 text-right">Co-authored</th>
                                            <th className="px-4 py-3">Last active</th>
                                        </tr></thead>
                                        <tbody>
                                            {contributors.map((row, index) => {
                                                const profile = !row.unresolved ? githubProfileUrl(row.login, row.github_user_id) : null;
                                                const key = row.github_user_id ?? `${row.login || "unresolved"}-${index}`;
                                                return (
                                                    <tr key={key} data-testid={`contributor-${row.login || index}`} className="border-b border-sm-border/60 last:border-b-0">
                                                        <td className="px-4 py-3 font-medium">
                                                            {row.unresolved ? <span>Unresolved author: {row.login || "Unknown"}</span> : profile ? (
                                                                <a href={profile} target="_blank" rel="noopener noreferrer" className="text-sm-blue hover:underline">{row.login || "Unknown"}</a>
                                                            ) : <span>{row.login || "Unknown"}</span>}
                                                        </td>
                                                        <td className="px-3 py-3 text-sm-text-secondary">{row.linked_user?.display_name || "—"}</td>
                                                        <NumberCell value={row.commits} /><NumberCell value={row.pull_requests} />
                                                        <NumberCell value={row.reviews} /><NumberCell value={row.issues} />
                                                        <NumberCell value={row.comments} /><NumberCell value={row.shared} />
                                                        <td className="px-4 py-3 text-sm-text-secondary whitespace-nowrap">{lastActive(row.last_activity)}</td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                )}
                            </section>
                        )}
                    </>
                )}
            </div>
        </>
    );
}

function NumberCell({ value }) {
    return <td className="px-3 py-3 text-right font-mono tabular-nums">{Number(value || 0).toLocaleString()}</td>;
}
