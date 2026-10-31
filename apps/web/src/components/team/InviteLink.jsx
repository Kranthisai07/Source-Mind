import React, { useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "../ui/button";
import { appUrl } from "../../lib/appUrl";

/** Absolute link for an invitation's accept_path (which is basename-relative). */
export const inviteUrl = (acceptPath) => `${window.location.origin}${appUrl(acceptPath)}`;

/**
 * One invite link with a copy button. The link is shown once, so the copy
 * outcome is reported plainly: a failed copy never looks like a success.
 */
export default function InviteLink({ email, role, acceptPath }) {
    const [state, setState] = useState("idle");
    const url = inviteUrl(acceptPath);
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(url);
            setState("copied");
        } catch {
            setState("failed");
        }
    };
    return (
        <div data-testid="invite-link" className="rounded-lg border border-sm-border bg-sm-bg/40 p-3">
            <div className="flex items-center justify-between gap-3">
                <span className="text-[12.5px] text-sm-text truncate">{email}</span>
                <span className="font-mono text-[10.5px] uppercase text-sm-text-secondary">{role}</span>
            </div>
            <div className="mt-2 flex items-center gap-2">
                <input
                    readOnly
                    aria-label={`Invitation link for ${email}`}
                    value={url}
                    onFocus={(e) => e.target.select()}
                    className="min-w-0 flex-1 h-9 rounded-md bg-sm-bg/60 border border-sm-border px-2 font-mono text-[11px] text-sm-text"
                />
                <Button type="button" variant="outline" onClick={copy} data-testid="invite-link-copy" className="h-9 border-sm-border text-sm-text">
                    {state === "copied" ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                    {state === "copied" ? "Copied" : "Copy"}
                </Button>
            </div>
            {state === "failed" && (
                <p role="alert" className="mt-1.5 text-[11.5px] text-sm-red">Could not copy automatically. Select the link above and copy it.</p>
            )}
        </div>
    );
}
