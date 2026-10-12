import React, { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import api from "../../lib/api";
import { mapWorkspaceError } from "../../lib/workspaceErrors";

// Admin and owner are deliberately absent: the backend rejects them, and the
// UI should not offer what cannot succeed.
const ROLES = [
    { value: "member", label: "Member - can create and edit memories" },
    { value: "viewer", label: "Viewer - read only" },
];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Add an existing SourceMind user to the workspace by email.
 *
 * Success is reported only when the 201 arrives. Every failure leaves the form
 * open with the reason, and never says or implies the person was added.
 */
export default function AddMemberDialog({ open, onOpenChange, workspaceId, workspaceName, onAdded }) {
    const [email, setEmail] = useState("");
    const [role, setRole] = useState("member");
    const [error, setError] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const inFlight = useRef(false);
    const mounted = useRef(true);
    useEffect(() => () => { mounted.current = false; }, []);

    // A closed dialog keeps no draft or stale error.
    useEffect(() => {
        if (!open) {
            setEmail("");
            setRole("member");
            setError(null);
        }
    }, [open]);

    const submit = async (event) => {
        event.preventDefault();
        if (inFlight.current) return;
        const address = email.trim();
        if (!EMAIL_RE.test(address)) {
            setError("Enter the email address they sign in with.");
            return;
        }
        inFlight.current = true;
        setSubmitting(true);
        setError(null);
        try {
            await api.addWorkspaceMember(workspaceId, { email: address, role });
            // Only reached on a 201.
            toast.success(`${address} added to ${workspaceName || "the workspace"}`);
            setEmail("");
            setRole("member");
            onOpenChange(false);
            if (onAdded) onAdded();
        } catch (err) {
            if (mounted.current) setError(mapWorkspaceError(err, "addMember").message);
        } finally {
            inFlight.current = false;
            if (mounted.current) setSubmitting(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(value) => { if (!submitting) onOpenChange(value); }}>
            <DialogContent className="bg-sm-surface border-sm-border text-sm-text" data-testid="add-member-dialog">
                <DialogHeader>
                    <DialogTitle>Add member</DialogTitle>
                    <DialogDescription className="text-sm-text-secondary">
                        They must have signed in to SourceMind once with this email address.
                    </DialogDescription>
                </DialogHeader>
                <form onSubmit={submit} noValidate className="space-y-4">
                    <div>
                        <label htmlFor="add-member-email" className="block text-[12px] text-sm-text-secondary mb-1.5 font-medium">Email</label>
                        <Input
                            id="add-member-email"
                            data-testid="add-member-email"
                            type="email"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            autoComplete="off"
                            placeholder="friend@example.com"
                            className="bg-sm-bg/60 border-sm-border text-sm-text h-10"
                        />
                    </div>
                    <div>
                        <label htmlFor="add-member-role" className="block text-[12px] text-sm-text-secondary mb-1.5 font-medium">Role</label>
                        <select
                            id="add-member-role"
                            data-testid="add-member-role"
                            value={role}
                            onChange={(e) => setRole(e.target.value)}
                            className="w-full h-10 rounded-md bg-sm-bg/60 border border-sm-border text-sm-text text-[13px] px-3"
                        >
                            {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                        </select>
                    </div>
                    {error && <p role="alert" data-testid="add-member-error" className="text-[12.5px] text-sm-red">{error}</p>}
                    <DialogFooter>
                        <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={submitting} className="border-sm-border text-sm-text">
                            Cancel
                        </Button>
                        <Button type="submit" data-testid="add-member-submit" disabled={submitting} className="bg-sm-blue text-white">
                            {submitting ? "Adding..." : "Add member"}
                        </Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}
