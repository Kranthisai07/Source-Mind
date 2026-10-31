import React, { useRef, useState } from "react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Textarea } from "../ui/textarea";
import InviteLink from "./InviteLink";
import api from "../../lib/api";
import { ALL_ROLES, ROLE_SUMMARIES, roleLabel } from "../../lib/roles";
import { MAX_SETUP_INVITES, buildInvitePlan } from "../../lib/teamSetup";
import { mapWorkspaceError } from "../../lib/workspaceErrors";

/**
 * Second step of workspace setup: who are the founders, who holds admin, who
 * are the teammates. Everything here is optional and the same actions live in
 * Settings, so skipping loses nothing.
 *
 * Invitations are created one after another and each outcome is shown
 * separately. A failure for one address never hides the links that did work.
 */
export default function TeamSetupStep({ workspace, onFinish }) {
    const [adminMode, setAdminMode] = useState("me");
    const [admin, setAdmin] = useState("");
    const [founders, setFounders] = useState("");
    const [teammates, setTeammates] = useState("");
    const [problem, setProblem] = useState(null);
    const [working, setWorking] = useState(false);
    const [results, setResults] = useState(null);
    const inFlight = useRef(false);

    const submit = async (event) => {
        event.preventDefault();
        if (inFlight.current) return;
        const plan = buildInvitePlan({ admin: adminMode === "other" ? admin : "", founders, teammates });
        if (plan.invalid.length) {
            setProblem(`These don't look like email addresses: ${plan.invalid.join(", ")}`);
            return;
        }
        if (plan.tooMany) {
            setProblem(`Invite at most ${MAX_SETUP_INVITES} people here; you can add more from Settings.`);
            return;
        }
        if (adminMode === "other" && !plan.invites.some((i) => i.role === "admin") && !admin.trim()) {
            setProblem("Enter the email address of the person who will hold admin, or choose \"I'll hold admin myself\".");
            return;
        }
        if (plan.invites.length === 0) {
            onFinish();
            return;
        }
        setProblem(null);
        inFlight.current = true;
        setWorking(true);
        const done = [];
        for (const invite of plan.invites) {
            try {
                const created = await api.createInvitation(workspace.id, invite);
                done.push({ ...invite, ok: true, created });
            } catch (err) {
                done.push({ ...invite, ok: false, message: mapWorkspaceError(err, "invite").message });
            }
        }
        inFlight.current = false;
        setWorking(false);
        setResults(done);
    };

    if (results) {
        const failed = results.filter((r) => !r.ok);
        return (
            <div data-testid="team-setup-results" className="space-y-3">
                <p className="text-[13px] text-sm-text">
                    {results.length - failed.length} invitation link{results.length - failed.length === 1 ? "" : "s"} ready. Send each person their own link; it works once, for that email address, for 7 days.
                </p>
                {results.filter((r) => r.ok).map((r) => (
                    <InviteLink key={r.email} email={r.email} role={roleLabel(r.role)} acceptPath={r.created.accept_path} />
                ))}
                {failed.map((r) => (
                    <p key={r.email} role="alert" data-testid="team-setup-failure" className="text-[12.5px] text-sm-red">
                        {r.email} ({roleLabel(r.role)}): {r.message}
                    </p>
                ))}
                {failed.length > 0 && <p className="text-[11.5px] text-sm-text-muted">You can invite the others again from Settings, Team.</p>}
                <p className="text-[11.5px] text-sm-text-muted">These links are shown only now. Copy them before you continue.</p>
                <div className="flex justify-end">
                    <Button type="button" onClick={onFinish} data-testid="team-setup-done" className="bg-sm-blue text-white">Continue to {workspace.name}</Button>
                </div>
            </div>
        );
    }

    const label = "block text-[12px] text-sm-text-secondary mb-1.5 font-medium";
    return (
        <form onSubmit={submit} noValidate className="space-y-4" data-testid="team-setup-form">
            <p className="text-[13px] text-sm-text-secondary">
                <strong className="text-sm-text">{workspace.name}</strong> is ready and you are a founder. Set up who else is on the team now, or skip and do it later in Settings.
            </p>
            <div>
                <span className={label}>Who holds admin?</span>
                <div className="space-y-2">
                    <label className="flex items-center gap-2 text-[13px] text-sm-text">
                        <input type="radio" name="admin-mode" checked={adminMode === "me"} onChange={() => setAdminMode("me")} data-testid="admin-me" />
                        I'll hold admin myself
                    </label>
                    <label className="flex items-center gap-2 text-[13px] text-sm-text">
                        <input type="radio" name="admin-mode" checked={adminMode === "other"} onChange={() => setAdminMode("other")} data-testid="admin-other" />
                        Someone else
                    </label>
                    {adminMode === "other" && (
                        <Input
                            aria-label="Admin email"
                            data-testid="admin-email"
                            type="email"
                            value={admin}
                            onChange={(e) => setAdmin(e.target.value)}
                            placeholder="admin@example.com"
                            autoComplete="off"
                            className="bg-sm-bg/60 border-sm-border text-sm-text h-10"
                        />
                    )}
                </div>
            </div>
            <div>
                <label htmlFor="setup-founders" className={label}>Other founders (optional)</label>
                <Textarea id="setup-founders" data-testid="setup-founders" value={founders} onChange={(e) => setFounders(e.target.value)} rows={2} placeholder="One email per line. Founders have equal top rights." className="bg-sm-bg/60 border-sm-border text-sm-text resize-none" />
            </div>
            <div>
                <label htmlFor="setup-teammates" className={label}>Teammates (optional)</label>
                <Textarea id="setup-teammates" data-testid="setup-teammates" value={teammates} onChange={(e) => setTeammates(e.target.value)} rows={3} placeholder="One email per line. They add and read the team's memory." className="bg-sm-bg/60 border-sm-border text-sm-text resize-none" />
            </div>
            <dl className="rounded-lg border border-sm-border bg-sm-bg/40 p-3 space-y-1.5" data-testid="role-guide">
                {ALL_ROLES.map((r) => (
                    <div key={r} className="flex gap-2 text-[12px]">
                        <dt className="w-16 shrink-0 font-medium text-sm-text">{roleLabel(r)}</dt>
                        <dd className="text-sm-text-secondary">{ROLE_SUMMARIES[r]}</dd>
                    </div>
                ))}
            </dl>
            {problem && <p role="alert" data-testid="team-setup-error" className="text-[12.5px] text-sm-red">{problem}</p>}
            <div className="flex justify-end gap-2">
                <Button type="button" variant="outline" onClick={onFinish} disabled={working} data-testid="team-setup-skip" className="border-sm-border text-sm-text">Skip for now</Button>
                <Button type="submit" disabled={working} data-testid="team-setup-submit" className="bg-sm-blue hover:bg-sm-blue/90 text-white">{working ? "Creating links..." : "Create invite links"}</Button>
            </div>
        </form>
    );
}
