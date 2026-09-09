import React, { useEffect, useState } from "react";
import { KeyRound, Trash2, UserPlus } from "lucide-react";
import PageHeader from "../components/ui-kit/PageHeader";
import EmptyState from "../components/ui-kit/EmptyState";
import { Skeleton } from "../components/ui-kit/Skeleton";
import { Input } from "../components/ui/input";
import { Button } from "../components/ui/button";
import api from "../lib/api";
import { formatDate } from "../lib/format";

/**
 * Page 7, the final page of the Supermemory-console redesign.
 *
 * §4.13 is "stacked full-width cards, each a distinct concern ... Danger zone
 * visually distinguished with a red section title and a red-outlined button".
 * The tab strip is therefore gone: the reference's settings screen has no
 * tabs, and four concerns split across four tabs hid the danger zone behind a
 * click.
 *
 * §4.16 is the member table: MEMBER (avatar-less, name stacked over its
 * secondary line) / ROLE / ACCESS / JOINED, with the count in a footer line
 * and a primary Invite button in the page's top-right, outside the card.
 *
 * Verified against the real GET /v1/workspaces/{ws}/members. Every field the
 * old page read was wrong, because the response nests the user:
 *
 *   [{ user: { id, display_name, avatar_url }, role, joined_at }]
 *
 *   m.id / m.name / m.login   -> undefined (they live under m.user)
 *   m.email                   -> does not exist ANYWHERE; UserSummary carries
 *                                only id, display_name, avatar_url
 *   m.role                    -> 'admin', lowercase. The page compared against
 *                                "Owner" and "Admin", so both branches were
 *                                dead and every member got the fallback pill
 *   m.role !== "Owner"        -> always true, so the DELETE button rendered on
 *                                owners as well
 *   key={m.id}                -> undefined for every row (duplicate React keys)
 *
 * `joined_at` was available and unused; §4.16 wants it, so it is now a column.
 *
 * Role is rendered as plain text, per §4.16 ("ROLE ('Owner')"), not as a
 * coloured pill. That also removes the last sm-purple usage in the redesign
 * scope and sidesteps WORKING_STANDARDS rule 9 entirely — there is no
 * dynamically-constructed class left to mis-compile.
 */

// The four roles WorkspaceRole defines, and what each can do. Access is
// derived from the role's documented semantics in models/workspace.py; the
// API does not send an access field.
const ACCESS_BY_ROLE = {
    owner:  "Full",
    admin:  "Manage",
    member: "Edit",
    viewer: "Read-only",
};

export default function Settings() {
    const [members, setMembers] = useState(null);
    const [workspace, setWorkspace] = useState(null);
    const [wsName, setWsName] = useState("");

    useEffect(() => {
        api.listTeamMembers()
            .then(r => setMembers(Array.isArray(r?.members) ? r.members : []))
            .catch(() => setMembers([]));
        api.getWorkspace()
            .then((w) => { setWorkspace(w); setWsName(w?.name ?? ""); })
            .catch(() => setWorkspace(null));
    }, []);

    const wsSlug = workspace?.slug ?? "";
    const rows = members ?? [];

    return (
        <>
            <PageHeader
                title="Settings"
                subtitle={
                    workspace
                        ? `Workspace, members and access for ${workspace.name}.`
                        : "Workspace, members and access."
                }
            />

            {/* §4.13 — one card per concern, stacked full width. */}
            <div className="space-y-4 max-w-3xl">

                {/* ── Workspace ─────────────────────────────────────────── */}
                <section className="sm-card p-6">
                    <h2 className="text-title text-content mb-1">Workspace</h2>
                    <p className="text-body text-content-secondary mb-5">
                        Identity of this workspace across the product and the API.
                    </p>

                    <div className="space-y-4">
                        <div>
                            <label htmlFor="ws-name" className="sm-micro-label block mb-2">Name</label>
                            <Input
                                id="ws-name"
                                data-testid="ws-name"
                                value={wsName}
                                onChange={(e) => setWsName(e.target.value)}
                                disabled
                                className="bg-surface-page border-hairline text-content h-10 disabled:opacity-70"
                            />
                        </div>

                        <div>
                            <label className="sm-micro-label block mb-2">Slug</label>
                            {/* §3: a slug is a technical identifier → mono. The
                                old field prefixed a hardcoded "sourcemind.dev/"
                                that is not this deployment's domain. */}
                            <div className="flex items-center h-10 px-3 rounded-md bg-surface-page border border-hairline">
                                {workspace === null ? (
                                    <Skeleton className="h-[1em] w-40" />
                                ) : (
                                    <span className="font-mono text-body text-content select-all">
                                        {wsSlug || "—"}
                                    </span>
                                )}
                            </div>
                        </div>
                    </div>

                    {/* No PATCH /v1/workspaces/{id} route exists, so "Save
                        changes" could only ever fire a success toast over a
                        request that was never made. Disabled rather than
                        left as a convincing no-op. */}
                    <div className="mt-6 pt-5 border-t border-hairline flex items-center gap-3">
                        <Button
                            data-testid="save-workspace"
                            disabled
                            className="bg-brand-fill text-white disabled:opacity-40"
                        >
                            Save changes
                        </Button>
                        <p className="text-[11px] text-content-muted">
                            Editing a workspace isn't exposed by the API yet.
                        </p>
                    </div>
                </section>

                {/* ── Members — §4.16 ───────────────────────────────────── */}
                <section>
                    <div className="flex items-center justify-between gap-4 mb-3">
                        <h2 className="text-title text-content">Members</h2>
                        {/* §4.16: primary button top-right, OUTSIDE the card. */}
                        <Button
                            data-testid="invite-btn"
                            disabled
                            title="No invite endpoint exists yet"
                            className="h-9 bg-brand-fill text-white disabled:opacity-40"
                        >
                            <UserPlus className="w-4 h-4" /> Invite member
                        </Button>
                    </div>

                    <div className="sm-card p-6">
                        {members === null ? (
                            <div className="space-y-3" role="status" aria-busy="true" aria-label="Loading members">
                                {Array.from({ length: 2 }).map((_, i) => (
                                    <Skeleton key={i} className="h-tablerow w-full" />
                                ))}
                                <span className="sr-only">Loading members…</span>
                            </div>
                        ) : rows.length === 0 ? (
                            <EmptyState
                                testId="members-empty"
                                icon={UserPlus}
                                noun="members"
                                description="Everyone with access to this workspace will be listed here."
                            />
                        ) : (
                            <>
                                <table className="w-full">
                                    <thead>
                                        <tr className="text-left border-b border-hairline">
                                            <th className="sm-micro-label font-semibold pb-2">Member</th>
                                            <th className="sm-micro-label font-semibold pb-2">Role</th>
                                            <th className="sm-micro-label font-semibold pb-2">Access</th>
                                            <th className="sm-micro-label font-semibold pb-2 text-right">Joined</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {rows.map((m, i) => {
                                            // The user is NESTED. Reading m.name
                                            // or m.email off the row gives
                                            // undefined — m.email does not exist
                                            // at any level.
                                            const u = m?.user ?? {};
                                            const role = (m?.role || "").toLowerCase();
                                            return (
                                                <tr
                                                    key={u.id || i}
                                                    data-testid={`member-${u.id || i}`}
                                                    className="border-b border-hairline last:border-0"
                                                >
                                                    {/* §4.16: avatar-less, name
                                                        stacked over its
                                                        secondary line. The
                                                        reference stacks an
                                                        email; this API carries
                                                        none, so the user id —
                                                        the only other identifier
                                                        it returns — takes that
                                                        line, in mono per §3. */}
                                                    <td className="py-3.5 pr-4">
                                                        <div className="text-body text-content truncate">
                                                            {u.display_name || "Unnamed user"}
                                                        </div>
                                                        <div className="font-mono text-[10.5px] text-content-secondary truncate">
                                                            {u.id || "—"}
                                                        </div>
                                                    </td>
                                                    <td className="py-3.5 pr-4 text-body text-content capitalize">
                                                        {role || "—"}
                                                    </td>
                                                    <td className="py-3.5 pr-4 text-body text-content-secondary">
                                                        {ACCESS_BY_ROLE[role] || "—"}
                                                    </td>
                                                    <td className="py-3.5 text-right font-mono text-[11px] text-content-secondary">
                                                        {m?.joined_at ? formatDate(m.joined_at) : "—"}
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>

                                {/* §4.16 footer line. The reference's right-hand
                                    "1/1 seats used" is seat/plan messaging,
                                    excluded from scope. */}
                                <div className="flex items-center justify-between mt-4 pt-3 border-t border-hairline">
                                    <span className="font-mono text-[11px] text-content-secondary">
                                        {rows.length} member{rows.length === 1 ? "" : "s"}
                                    </span>
                                </div>
                            </>
                        )}
                    </div>
                </section>

                {/* ── API keys ──────────────────────────────────────────── */}
                <section className="sm-card p-6">
                    <h2 className="text-title text-content mb-1">API keys</h2>
                    <p className="text-body text-content-secondary mb-5">
                        Keys for server-to-server access to this workspace.
                    </p>

                    {/*
                      REMOVED: a hardcoded constant
                      "sm_live_4f7eff_a78bfa_34d399_b22c_k9qe2m3p6n8x1", rendered
                      in a credential field with a reveal toggle and a
                      copy-to-clipboard button, plus a "Rotate key" flow that
                      only fired a success toast.

                      It was not a real key and could never become one — there
                      is no API-key route anywhere in the backend. A fake
                      credential that presents as live is worse than an absent
                      one: it invites someone to copy it into an integration, or
                      to treat a leaked-looking `sm_live_` string as a real
                      secret. Replaced with the §5 template stating the truth.
                    */}
                    <EmptyState
                        testId="api-keys-empty"
                        icon={KeyRound}
                        noun="API keys"
                        description="Key issuance isn't available yet — the backend exposes no API-key endpoints. Requests are authenticated with a Clerk session token."
                    />
                </section>

                {/* ── Danger zone — §4.13 ───────────────────────────────── */}
                <section
                    data-testid="danger-zone"
                    className="rounded-card border p-6"
                    style={{
                        borderColor: "var(--c-status-danger-border)",
                        background: "var(--c-status-danger-subtle)",
                    }}
                >
                    {/* §4.13: "visually distinguished with a red section title
                        and a red-outlined button". */}
                    <h2 className="text-title text-danger mb-1">Danger zone</h2>
                    <p className="text-body text-content-secondary mb-5">
                        Permanently delete{" "}
                        <span className="font-mono text-content">{wsSlug || "this workspace"}</span>
                        {" "}and every memory, conflict and connector in it. This cannot be undone.
                    </p>

                    {/* The delete flow previously ended in
                        toast.error("Workspace deleted (demo)") — a confirmation
                        that nothing had happened, behind a type-the-slug
                        confirmation that made it look real. There is no
                        DELETE /v1/workspaces/{id} route, so the control is
                        disabled and says so. */}
                    <Button
                        data-testid="delete-workspace-btn"
                        disabled
                        title="No workspace-deletion endpoint exists yet"
                        className="bg-transparent border text-danger disabled:opacity-50"
                        style={{ borderColor: "var(--c-status-danger-border)" }}
                    >
                        <Trash2 className="w-4 h-4" /> Delete workspace
                    </Button>
                    <p className="text-[11px] text-content-muted mt-2">
                        Workspace deletion isn't exposed by the API yet.
                    </p>
                </section>
            </div>
        </>
    );
}
