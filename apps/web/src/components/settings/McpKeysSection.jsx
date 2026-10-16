import React, { useEffect, useRef, useState } from "react";
import { Copy, KeyRound, Plus } from "lucide-react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "../ui/dialog";
import { PageLoadError, PageLoading } from "../widgets/PageLoadState";
import useApiResource from "../../hooks/useApiResource";
import api from "../../lib/api";
import { classifyApiError } from "../../lib/apiError";
import { formatDate } from "../../lib/format";

const EXPIRY_CHOICES = [30, 90, 365];
const NAME_MAX = 100;

const STATUS_STYLE = {
    active: "bg-sm-green/15 text-sm-green",
    expired: "bg-sm-amber/15 text-sm-amber",
    revoked: "bg-sm-red/15 text-sm-red",
};

function createErrorText(error) {
    const info = classifyApiError(error);
    // A key's secret is never part of an error; only the classified, generic text is shown.
    if (info.kind === "rate-limited") {
        return "Too many keys were created recently. Try again later.";
    }
    return info.detail || "The key could not be created.";
}

/**
 * MCP keys: a key lets an AI tool READ this workspace's memories as the member who created it.
 *
 * The secret exists only in the state of the show-once dialog: it is never stored (no
 * localStorage/sessionStorage), never put in a URL, a log or an error, and it is gone from state
 * and DOM the moment that dialog closes. The list endpoint never returns it.
 */
export default function McpKeysSection() {
    const resource = useApiResource(() => api.listMcpKeys());
    const { data, error, loading, retry } = resource;
    const keys = Array.isArray(data?.keys) ? data.keys : [];
    const showCreator = new Set(keys.map((key) => key.user_id)).size > 1;

    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);

    // ----- create
    const [createOpen, setCreateOpen] = useState(false);
    const [name, setName] = useState("");
    const [days, setDays] = useState(90);
    const [nameError, setNameError] = useState(null);
    const [createError, setCreateError] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const inFlight = useRef(false);

    // ----- show-once secret
    const [secret, setSecret] = useState(null);
    const [copyState, setCopyState] = useState("idle");

    // ----- revoke
    const [revokeTarget, setRevokeTarget] = useState(null);
    const [revoking, setRevoking] = useState(false);
    const [revokeError, setRevokeError] = useState(null);

    useEffect(() => {
        if (!createOpen) {
            setName("");
            setDays(90);
            setNameError(null);
            setCreateError(null);
        }
    }, [createOpen]);

    const closeSecret = () => {
        setSecret(null);
        setCopyState("idle");
    };

    const submitCreate = async (event) => {
        event.preventDefault();
        if (inFlight.current) return;
        const trimmed = name.trim();
        if (trimmed.length < 1 || trimmed.length > NAME_MAX) {
            setNameError(`Enter a name of 1 to ${NAME_MAX} characters.`);
            return;
        }
        setNameError(null);
        setCreateError(null);
        inFlight.current = true;
        setSubmitting(true);
        try {
            const created = await api.createMcpKey(undefined, { name: trimmed, expires_in_days: days });
            if (!mounted.current) return;
            setSecret(created);
            setCopyState("idle");
            setCreateOpen(false);
            retry();
        } catch (requestError) {
            if (mounted.current) setCreateError(createErrorText(requestError));
        } finally {
            inFlight.current = false;
            if (mounted.current) setSubmitting(false);
        }
    };

    const copySecret = async () => {
        try {
            await navigator.clipboard.writeText(secret.key);
            setCopyState("copied");
        } catch (_clipboardError) {
            setCopyState("failed");
        }
    };

    const confirmRevoke = async () => {
        if (!revokeTarget || revoking) return;
        setRevoking(true);
        setRevokeError(null);
        try {
            await api.revokeMcpKey(undefined, revokeTarget.id);
            if (!mounted.current) return;
            setRevokeTarget(null);
            retry();
        } catch (requestError) {
            if (mounted.current) setRevokeError(classifyApiError(requestError).detail);
        } finally {
            if (mounted.current) setRevoking(false);
        }
    };

    return (
        <section className="sm-card p-6 max-w-4xl" data-testid="mcp-keys-section">
            <div className="flex items-start justify-between gap-4">
                <div>
                    <h3 className="flex items-center gap-2 text-[15px] font-semibold text-sm-text">
                        <KeyRound className="w-4 h-4" /> MCP keys
                    </h3>
                    <p className="mt-1 text-[12.5px] text-sm-text-secondary">
                        A key lets an AI tool read this workspace&apos;s memories as you. It is read-only and
                        works in this workspace only. Keys expire, and once you revoke one the tool stops
                        working on its next call.
                    </p>
                </div>
                {!loading && (
                    <Button
                        type="button"
                        size="sm"
                        data-testid="mcp-keys-create-btn"
                        onClick={() => setCreateOpen(true)}
                        className="bg-sm-blue hover:bg-sm-blue/90 text-white shrink-0"
                    >
                        <Plus className="w-3.5 h-3.5" /> Create key
                    </Button>
                )}
            </div>

            {data && data.mcp_enabled === false && (
                <div
                    data-testid="mcp-disabled-banner"
                    role="status"
                    className="mt-4 rounded-lg border border-sm-amber/40 bg-sm-amber/10 p-3 text-[12.5px] text-sm-text"
                >
                    MCP is not enabled on this deployment, so keys cannot be used yet.
                </div>
            )}

            <div className="mt-4">
                {loading && <PageLoading label="Loading MCP keys" testId="mcp-keys-loading" />}
                {!loading && error && (
                    <PageLoadError error={error} onRetry={retry} testId="mcp-keys-error" />
                )}
                {!loading && !error && data && keys.length === 0 && (
                    <div
                        data-testid="mcp-keys-empty"
                        className="rounded-lg border border-sm-border p-8 text-center text-[13px] text-sm-text-secondary"
                    >
                        No MCP keys yet. Create one to connect an AI tool to this workspace.
                    </div>
                )}
                {!loading && !error && keys.length > 0 && (
                    <div className="overflow-x-auto">
                        <table data-testid="mcp-keys-table" className="w-full min-w-[720px] text-[12px]">
                            <thead>
                                <tr className="text-left font-mono text-[10px] uppercase tracking-wider text-sm-text-secondary border-b border-sm-border">
                                    <th className="px-3 py-2">Name</th>
                                    <th className="px-3 py-2">Key</th>
                                    {showCreator && <th className="px-3 py-2">Created by</th>}
                                    <th className="px-3 py-2">Created</th>
                                    <th className="px-3 py-2">Expires</th>
                                    <th className="px-3 py-2">Last used</th>
                                    <th className="px-3 py-2">Status</th>
                                    <th className="px-3 py-2" />
                                </tr>
                            </thead>
                            <tbody>
                                {keys.map((key) => (
                                    <tr
                                        key={key.id}
                                        data-testid={`mcp-key-row-${key.id}`}
                                        className="border-b border-sm-border/60"
                                    >
                                        <td className="px-3 py-3 font-medium text-sm-text">{key.name}</td>
                                        <td className="px-3 py-3 font-mono text-sm-text-secondary">{key.key_prefix}…</td>
                                        {showCreator && (
                                            <td className="px-3 py-3 text-sm-text-secondary">{key.creator_display_name}</td>
                                        )}
                                        <td className="px-3 py-3 text-sm-text-secondary whitespace-nowrap">{formatDate(key.created_at)}</td>
                                        <td className="px-3 py-3 text-sm-text-secondary whitespace-nowrap">{formatDate(key.expires_at)}</td>
                                        <td className="px-3 py-3 text-sm-text-secondary whitespace-nowrap">
                                            {key.last_used_at ? formatDate(key.last_used_at) : "never"}
                                        </td>
                                        <td className="px-3 py-3">
                                            <span
                                                data-testid={`mcp-key-status-${key.id}`}
                                                className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_STYLE[key.status] || ""}`}
                                            >
                                                {key.status}
                                            </span>
                                        </td>
                                        <td className="px-3 py-3 text-right">
                                            {key.status === "active" && (
                                                <Button
                                                    type="button"
                                                    size="sm"
                                                    variant="outline"
                                                    data-testid={`mcp-key-revoke-${key.id}`}
                                                    onClick={() => { setRevokeError(null); setRevokeTarget(key); }}
                                                    className="border-sm-border text-sm-text"
                                                >
                                                    Revoke
                                                </Button>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            <Dialog open={createOpen} onOpenChange={(next) => { if (!submitting) setCreateOpen(next); }}>
                <DialogContent data-testid="mcp-key-create-dialog">
                    <form onSubmit={submitCreate} noValidate>
                        <DialogHeader>
                            <DialogTitle>Create MCP key</DialogTitle>
                            <DialogDescription>
                                The key acts as you, read-only, in this workspace. You will see it once.
                            </DialogDescription>
                        </DialogHeader>
                        <div className="mt-4 space-y-4">
                            <label className="block text-[12px] text-sm-text-secondary">
                                <span className="block mb-1 font-medium">Name</span>
                                <Input
                                    data-testid="mcp-key-name-input"
                                    value={name}
                                    maxLength={NAME_MAX + 20}
                                    onChange={(event) => setName(event.target.value)}
                                    placeholder="e.g. Claude Code on my laptop"
                                    autoComplete="off"
                                />
                                {nameError && (
                                    <span data-testid="mcp-key-name-error" role="alert" className="mt-1 block text-sm-red">
                                        {nameError}
                                    </span>
                                )}
                            </label>
                            <label className="block text-[12px] text-sm-text-secondary">
                                <span className="block mb-1 font-medium">Expires after</span>
                                <select
                                    data-testid="mcp-key-expiry-select"
                                    value={days}
                                    onChange={(event) => setDays(Number(event.target.value))}
                                    className="h-9 rounded-md border border-sm-border bg-sm-surface px-3 text-[13px] text-sm-text"
                                >
                                    {EXPIRY_CHOICES.map((choice) => (
                                        <option key={choice} value={choice}>{choice} days</option>
                                    ))}
                                </select>
                            </label>
                            {createError && (
                                <p data-testid="mcp-key-create-error" role="alert" className="text-[12.5px] text-sm-red">
                                    {createError}
                                </p>
                            )}
                        </div>
                        <DialogFooter className="mt-6">
                            <Button type="button" variant="outline" onClick={() => setCreateOpen(false)} disabled={submitting}>
                                Cancel
                            </Button>
                            <Button
                                type="submit"
                                data-testid="mcp-key-create-submit"
                                disabled={submitting}
                                className="bg-sm-blue hover:bg-sm-blue/90 text-white"
                            >
                                {submitting ? "Creating…" : "Create key"}
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>

            <Dialog open={secret !== null} onOpenChange={(next) => { if (!next) closeSecret(); }}>
                <DialogContent
                    data-testid="mcp-key-secret-dialog"
                    onInteractOutside={(event) => event.preventDefault()}
                >
                    <DialogHeader>
                        <DialogTitle>Your new MCP key</DialogTitle>
                        <DialogDescription>
                            This is the only time the key is shown. Store it like a password.
                        </DialogDescription>
                    </DialogHeader>
                    {secret && (
                        <div className="mt-4 space-y-3">
                            <div className="flex items-center gap-2">
                                <Input
                                    readOnly
                                    data-testid="mcp-key-secret"
                                    value={secret.key}
                                    aria-label="MCP key"
                                    className="font-mono text-[12px]"
                                    onFocus={(event) => event.target.select()}
                                />
                                <Button
                                    type="button"
                                    variant="outline"
                                    data-testid="mcp-key-copy"
                                    onClick={copySecret}
                                    className="shrink-0"
                                >
                                    <Copy className="w-3.5 h-3.5" /> Copy
                                </Button>
                            </div>
                            {copyState === "copied" && (
                                <p data-testid="mcp-key-copy-status" role="status" className="text-[12px] text-sm-green">Copied</p>
                            )}
                            {copyState === "failed" && (
                                <p data-testid="mcp-key-copy-status" role="status" className="text-[12px] text-sm-amber">
                                    Copy failed. Select the key above and copy it manually.
                                </p>
                            )}
                            <p className="text-[12px] text-sm-text-secondary">
                                Expires {formatDate(secret.expires_at)}. Anyone with this key can read this workspace&apos;s
                                memories as you until it expires or you revoke it.
                            </p>
                        </div>
                    )}
                    <DialogFooter className="mt-6">
                        <Button
                            type="button"
                            data-testid="mcp-key-secret-close"
                            onClick={closeSecret}
                            className="bg-sm-blue hover:bg-sm-blue/90 text-white"
                        >
                            I&apos;ve stored it
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={revokeTarget !== null} onOpenChange={(next) => { if (!next && !revoking) setRevokeTarget(null); }}>
                <DialogContent data-testid="mcp-key-revoke-dialog">
                    <DialogHeader>
                        <DialogTitle>Revoke &quot;{revokeTarget?.name}&quot;?</DialogTitle>
                        <DialogDescription>
                            Tools using this key stop working on their next call. This cannot be undone.
                        </DialogDescription>
                    </DialogHeader>
                    {revokeError && (
                        <p data-testid="mcp-key-revoke-error" role="alert" className="mt-3 text-[12.5px] text-sm-red">
                            {revokeError}
                        </p>
                    )}
                    <DialogFooter className="mt-6">
                        <Button
                            type="button"
                            variant="outline"
                            data-testid="mcp-key-revoke-cancel"
                            onClick={() => setRevokeTarget(null)}
                            disabled={revoking}
                        >
                            Cancel
                        </Button>
                        <Button
                            type="button"
                            data-testid="mcp-key-revoke-confirm"
                            onClick={confirmRevoke}
                            disabled={revoking}
                            className="bg-sm-red hover:bg-sm-red/90 text-white"
                        >
                            {revoking ? "Revoking…" : "Revoke key"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </section>
    );
}
