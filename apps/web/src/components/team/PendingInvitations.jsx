import React, { useState } from "react";
import { toast } from "sonner";
import { Button } from "../ui/button";
import { PageLoadError, PageLoading } from "../widgets/PageLoadState";
import useApiResource from "../../hooks/useApiResource";
import api from "../../lib/api";
import { formatDate } from "../../lib/format";
import { roleLabel } from "../../lib/roles";
import { mapWorkspaceError } from "../../lib/workspaceErrors";

/**
 * Pending invitations with revoke. Links cannot be shown again (the server
 * keeps only a hash); to hand someone a fresh link, invite them again.
 */
export default function PendingInvitations({ workspaceId, refreshKey = 0 }) {
    const resource = useApiResource(() => api.listInvitations(workspaceId), { deps: [workspaceId, refreshKey] });
    const [busy, setBusy] = useState(null);
    const [error, setError] = useState(null);
    const items = Array.isArray(resource.data?.items) ? resource.data.items : [];

    const revoke = async (invitation) => {
        if (busy) return;
        setBusy(invitation.id);
        setError(null);
        try {
            await api.revokeInvitation(workspaceId, invitation.id);
            toast.success(`Invitation for ${invitation.email} withdrawn`);
            resource.retry();
        } catch (err) {
            setError(mapWorkspaceError(err, "revokeInvite").message);
        } finally {
            setBusy(null);
        }
    };

    return (
        <section className="sm-card p-6 mt-4" data-testid="pending-invitations">
            <h3 className="text-[15px] font-semibold text-sm-text">Pending invitations</h3>
            <p className="text-[12.5px] text-sm-text-secondary mt-0.5 mb-4">Links that have not been used yet. Each works once and expires after 7 days.</p>
            {error && <p role="alert" data-testid="invitation-error" className="mb-3 text-[12.5px] text-sm-red">{error}</p>}
            {resource.error ? (
                <PageLoadError error={resource.error} onRetry={resource.retry} testId="invitations-error" compact />
            ) : resource.loading && !resource.data ? (
                <PageLoading label="Loading invitations" testId="invitations-loading" />
            ) : items.length === 0 ? (
                <div data-testid="invitations-empty" className="py-6 text-center text-[12.5px] text-sm-text-secondary">No pending invitations.</div>
            ) : (
                <div className="space-y-2">
                    {items.map((invitation) => (
                        <div key={invitation.id} data-testid={`invitation-${invitation.id}`} className="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] items-center gap-3 p-3 rounded-lg border border-sm-border bg-sm-bg/40">
                            <span className="text-[13px] text-sm-text truncate">{invitation.email}</span>
                            <span className="font-mono text-[10.5px] uppercase text-sm-text">{roleLabel(invitation.role)}</span>
                            <span className="font-mono text-[10.5px] text-sm-text-secondary">expires {formatDate(invitation.expires_at)}</span>
                            <Button type="button" variant="outline" disabled={busy === invitation.id} onClick={() => revoke(invitation)} data-testid={`revoke-${invitation.id}`} className="h-8 border-sm-border text-sm-text">
                                {busy === invitation.id ? "Withdrawing..." : "Withdraw"}
                            </Button>
                        </div>
                    ))}
                </div>
            )}
        </section>
    );
}
