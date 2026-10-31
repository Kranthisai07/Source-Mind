import React, { useEffect, useRef, useState } from "react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import InviteLink from "./InviteLink";
import api from "../../lib/api";
import { roleLabel, ROLE_SUMMARIES, invitableRoles } from "../../lib/roles";
import { mapWorkspaceError } from "../../lib/workspaceErrors";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Invite someone who may not have an account yet. The result is a one-time
 * link for the inviter to share; no email is sent. The link is only on screen
 * in this dialog, so it stays until the inviter closes it.
 */
export default function InviteDialog({ open, onOpenChange, workspaceId, callerRole, onInvited }) {
    const roles = invitableRoles(callerRole);
    const [email, setEmail] = useState("");
    const [role, setRole] = useState("member");
    const [error, setError] = useState(null);
    const [created, setCreated] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const inFlight = useRef(false);
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);
    useEffect(() => {
        if (!open) { setEmail(""); setRole("member"); setError(null); setCreated(null); }
    }, [open]);

    const submit = async (event) => {
        event.preventDefault();
        if (inFlight.current) return;
        const address = email.trim();
        if (!EMAIL_RE.test(address)) { setError("Enter a valid email address."); return; }
        inFlight.current = true;
        setSubmitting(true);
        setError(null);
        try {
            const result = await api.createInvitation(workspaceId, { email: address, role });
            if (mounted.current) setCreated(result);
            if (onInvited) onInvited();
        } catch (err) {
            if (mounted.current) setError(mapWorkspaceError(err, "invite").message);
        } finally {
            inFlight.current = false;
            if (mounted.current) setSubmitting(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(value) => { if (!submitting) onOpenChange(value); }}>
            <DialogContent className="bg-sm-surface border-sm-border text-sm-text" data-testid="invite-dialog">
                <DialogHeader>
                    <DialogTitle>Invite to workspace</DialogTitle>
                    <DialogDescription className="text-sm-text-secondary">
                        They don't need an account yet. You'll get a link to send them; it works once, for this email address, for 7 days.
                    </DialogDescription>
                </DialogHeader>
                {created ? (
                    <div className="space-y-3">
                        <InviteLink email={created.email} role={roleLabel(created.role)} acceptPath={created.accept_path} />
                        <p className="text-[11.5px] text-sm-text-muted">This link is shown only now. Copy it before you close this window; if you lose it, invite them again to get a new one.</p>
                        <DialogFooter>
                            <Button type="button" onClick={() => onOpenChange(false)} data-testid="invite-done" className="bg-sm-blue text-white">Done</Button>
                        </DialogFooter>
                    </div>
                ) : (
                    <form onSubmit={submit} noValidate className="space-y-4">
                        <div>
                            <label htmlFor="invite-email" className="block text-[12px] text-sm-text-secondary mb-1.5 font-medium">Email</label>
                            <Input id="invite-email" data-testid="invite-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" placeholder="teammate@example.com" className="bg-sm-bg/60 border-sm-border text-sm-text h-10" />
                        </div>
                        <div>
                            <label htmlFor="invite-role" className="block text-[12px] text-sm-text-secondary mb-1.5 font-medium">Role</label>
                            <select id="invite-role" data-testid="invite-role" value={role} onChange={(e) => setRole(e.target.value)} className="w-full h-10 rounded-md bg-sm-bg/60 border border-sm-border text-sm-text text-[13px] px-3">
                                {roles.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
                            </select>
                            <p className="mt-1.5 text-[11.5px] text-sm-text-muted">{ROLE_SUMMARIES[role]}</p>
                        </div>
                        {error && <p role="alert" data-testid="invite-error" className="text-[12.5px] text-sm-red">{error}</p>}
                        <DialogFooter>
                            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={submitting} className="border-sm-border text-sm-text">Cancel</Button>
                            <Button type="submit" data-testid="invite-submit" disabled={submitting} className="bg-sm-blue text-white">{submitting ? "Creating link..." : "Create invite link"}</Button>
                        </DialogFooter>
                    </form>
                )}
            </DialogContent>
        </Dialog>
    );
}
