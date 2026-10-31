import React, { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "@clerk/clerk-react";
import { Button } from "../components/ui/button";
import api, { useMocks } from "../lib/api";
import { INVITE_TOKEN_RE } from "../lib/inviteToken";
import { roleLabel } from "../lib/roles";
import { selectWorkspace } from "../lib/realApi";
import { mapWorkspaceError } from "../lib/workspaceErrors";

/**
 * Landing page for an invitation link. Deliberately outside the app shell: the
 * person arriving usually belongs to no workspace yet, and the shell would show
 * them "ask the owner to add you" instead of this.
 *
 * Accepting is a button, not something that happens on load: a link preview or
 * a stray refresh must not consume a one-time invitation.
 */
export default function InviteAccept() {
    const { token } = useParams();
    const { isLoaded, isSignedIn } = useAuth();
    const navigate = useNavigate();
    const [state, setState] = useState({ phase: "ready" });
    const inFlight = useRef(false);
    const wellFormed = INVITE_TOKEN_RE.test(token || "");
    const signedIn = useMocks || isSignedIn;

    const accept = async () => {
        if (inFlight.current) return;
        inFlight.current = true;
        setState({ phase: "working" });
        try {
            const joined = await api.acceptInvitation(token);
            // Open the workspace they just joined rather than their last one.
            if (joined?.workspace_id) selectWorkspace(joined.workspace_id);
            setState({ phase: "done", role: joined?.role });
        } catch (err) {
            setState({ phase: "failed", message: mapWorkspaceError(err, "acceptInvite").message });
        } finally {
            inFlight.current = false;
        }
    };

    let body;
    if (!wellFormed) {
        body = (
            <>
                <h1 className="text-[18px] font-semibold text-sm-text">This link isn't a valid invitation</h1>
                <p className="mt-2 text-[13px] text-sm-text-secondary">Check that the whole link was copied, or ask the person who invited you for a new one.</p>
            </>
        );
    } else if (!useMocks && !isLoaded) {
        body = <p className="text-[13px] text-sm-text-secondary">Loading session…</p>;
    } else if (!signedIn) {
        const next = encodeURIComponent(`/invite/${token}`);
        body = (
            <>
                <h1 className="text-[18px] font-semibold text-sm-text">You've been invited to a SourceMind workspace</h1>
                <p className="mt-2 text-[13px] text-sm-text-secondary">
                    Sign in, or create an account, with the email address the invitation was sent to. You'll come straight back here.
                </p>
                <div className="mt-4 flex gap-2">
                    <Button asChild className="bg-sm-blue text-white"><Link to={`/sign-in?next=${next}`}>Sign in</Link></Button>
                    <Button asChild variant="outline" className="border-sm-border text-sm-text"><Link to={`/sign-up?next=${next}`}>Create account</Link></Button>
                </div>
            </>
        );
    } else if (state.phase === "done") {
        body = (
            <>
                <h1 data-testid="invite-accepted" className="text-[18px] font-semibold text-sm-text">You've joined the workspace</h1>
                <p className="mt-2 text-[13px] text-sm-text-secondary">
                    {state.role ? `You're a ${roleLabel(state.role)}.` : "Your access is ready."}
                </p>
                <Button type="button" onClick={() => navigate("/memories")} data-testid="invite-open" className="mt-4 bg-sm-blue text-white">Open workspace</Button>
            </>
        );
    } else {
        body = (
            <>
                <h1 className="text-[18px] font-semibold text-sm-text">Join this workspace</h1>
                <p className="mt-2 text-[13px] text-sm-text-secondary">
                    You were invited to a SourceMind workspace. Accepting works only if you're signed in with the email address that was invited.
                </p>
                {state.phase === "failed" && (
                    <p role="alert" data-testid="invite-accept-error" className="mt-3 text-[12.5px] text-sm-red">{state.message}</p>
                )}
                <Button type="button" onClick={accept} disabled={state.phase === "working"} data-testid="invite-accept" className="mt-4 bg-sm-blue text-white">
                    {state.phase === "working" ? "Joining..." : "Accept invitation"}
                </Button>
            </>
        );
    }

    return (
        <div className="min-h-screen bg-sm-bg text-sm-text flex items-start justify-center px-6 pt-24" data-testid="invite-page">
            <section className="sm-card p-6 w-full max-w-md">{body}</section>
        </div>
    );
}
