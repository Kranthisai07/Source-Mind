import React from "react";
import { useNavigate } from "react-router-dom";
import { useClerk, useUser } from "@clerk/clerk-react";
import { LogOut, RefreshCw } from "lucide-react";
import { Button } from "../ui/button";
import ThemeToggle from "../widgets/ThemeToggle";
import CreateWorkspaceForm from "./CreateWorkspaceForm";
import { isWorkspacePinned } from "../../lib/realApi";
import { useWorkspaces } from "./WorkspaceProvider";

/**
 * Shown when the signed-in user belongs to no workspace. A friend can sign in
 * before the owner has added them, so this is an expected state with a way
 * forward, not an error.
 */
export default function NoWorkspaceState() {
    const { email: apiEmail, reload, refreshing, adopt } = useWorkspaces();
    const { isSignedIn, user } = useUser();
    const { signOut } = useClerk();
    const navigate = useNavigate();
    const email = apiEmail || user?.primaryEmailAddress?.emailAddress || "";

    const handleSignOut = async () => {
        if (isSignedIn) await signOut();
        else navigate("/");
    };

    return (
        <div className="min-h-screen bg-sm-bg text-sm-text flex flex-col" data-testid="no-workspace">
            <header className="flex items-center justify-end gap-2 px-6 h-16">
                <ThemeToggle />
                <Button variant="outline" onClick={handleSignOut} data-testid="no-workspace-signout" className="h-9 border-sm-border text-sm-text">
                    <LogOut className="w-4 h-4" /> Sign out
                </Button>
            </header>
            <main className="flex-1 flex items-start justify-center px-6 pb-16">
                <div className="w-full max-w-lg space-y-6">
                    <section className="sm-card p-6">
                        <h1 className="text-[18px] font-semibold text-sm-text">You're signed in but not in a workspace yet</h1>
                        <p className="mt-2 text-[13px] text-sm-text-secondary">
                            {email ? (
                                <>
                                    You're signed in as <span data-testid="no-workspace-email" className="font-mono text-sm-text">{email}</span>.
                                    Ask the workspace owner to add this address, then check again.
                                </>
                            ) : (
                                "Ask the workspace owner to add the email address you signed in with, then check again."
                            )}
                        </p>
                        <Button
                            type="button"
                            onClick={reload}
                            disabled={refreshing}
                            data-testid="no-workspace-check"
                            className="mt-4 bg-sm-blue hover:bg-sm-blue/90 text-white"
                        >
                            <RefreshCw className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`} /> Check again
                        </Button>
                    </section>
                    {!isWorkspacePinned() && <section className="sm-card p-6">
                        <h2 className="text-[15px] font-semibold text-sm-text">Or start your own</h2>
                        <p className="mt-1 mb-4 text-[12.5px] text-sm-text-secondary">
                            Create a workspace and add people to it yourself.
                        </p>
                        <CreateWorkspaceForm idPrefix="nw" teamSetup onCreated={adopt} />
                    </section>}
                </div>
            </main>
        </div>
    );
}
