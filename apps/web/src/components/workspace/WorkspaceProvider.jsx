import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api, { resetApiCaches, resolveCurrentWorkspace, selectWorkspace } from "../../lib/api";
import { classifyApiError } from "../../lib/apiError";
import { isStaleWorkspaceError, onIdentityReset } from "../../lib/realApi";

const WorkspaceContext = createContext(null);

export function useWorkspaces() {
    const ctx = useContext(WorkspaceContext);
    if (!ctx) throw new Error("useWorkspaces must be used inside <WorkspaceProvider>");
    return ctx;
}

// Detail routes carry ids that belong to one workspace; they mean nothing in
// another, so a switch lands on the section's list instead.
const DETAIL_ROUTE = /^(\/(?:memories|conflicts))\/[^/]+/;

/**
 * Owns the list of workspaces the user belongs to and which one is current.
 *
 * Status is explicit so an empty list (a signed-in user nobody has added yet)
 * is never confused with a failed lookup:
 *   loading | ready | empty | error
 */
export function WorkspaceProvider({ children }) {
    const navigate = useNavigate();
    const location = useLocation();
    const [state, setState] = useState({
        status: "loading",
        list: [],
        currentId: null,
        error: null,
        email: null,
        refreshing: false,
    });
    const token = useRef(0);
    const selfReset = useRef(false);
    const pathRef = useRef(location.pathname);
    pathRef.current = location.pathname;

    const load = useCallback(async () => {
        const mine = ++token.current;
        setState((s) => (s.status === "loading" ? s : { ...s, refreshing: true }));
        try {
            const [res, me] = await Promise.all([
                api.listWorkspaces(),
                api.getCurrentUser().catch(() => null),
            ]);
            if (mine !== token.current) return;
            const list = Array.isArray(res?.items) ? res.items : [];
            if (!list.length) {
                setState({ status: "empty", list: [], currentId: null, error: null, email: me?.email || null, refreshing: false });
                return;
            }
            let currentId;
            try {
                currentId = (await resolveCurrentWorkspace()) || list[0].id;
            } catch (err) {
                if (isStaleWorkspaceError(err)) {
                    if (mine === token.current) load();
                    return;
                }
                throw err;
            }
            if (mine !== token.current) return;
            setState({ status: "ready", list, currentId, error: null, email: me?.email || null, refreshing: false });
        } catch (err) {
            if (mine !== token.current || isStaleWorkspaceError(err)) return;
            // A failed lookup is an error, never an empty workspace list.
            setState((s) => ({ ...s, status: "error", error: classifyApiError(err), refreshing: false }));
        }
    }, []);

    useEffect(() => {
        load();
        // A different user is a different list. Workspace switches are handled
        // by switchTo below and need no reload.
        const off = onIdentityReset((_generation, kind) => {
            if (kind !== "identity" || selfReset.current) return;
            setState((s) => ({ ...s, status: "loading", list: [], currentId: null, error: null }));
            load();
        });
        return () => {
            token.current += 1;
            off();
        };
    }, [load]);

    /** "Check again": forget cached lookups, then list afresh. */
    const reload = useCallback(() => {
        selfReset.current = true;
        try {
            resetApiCaches();
        } finally {
            selfReset.current = false;
        }
        return load();
    }, [load]);

    const currentRef = useRef(null);
    currentRef.current = state.currentId;

    const switchTo = useCallback((workspaceId) => {
        if (!workspaceId || workspaceId === currentRef.current) return;
        currentRef.current = workspaceId;
        setState((s) => ({ ...s, currentId: workspaceId }));
        selectWorkspace(workspaceId);
        const match = DETAIL_ROUTE.exec(pathRef.current);
        if (match) navigate(match[1]);
    }, [navigate]);

    /** After creating a workspace: add it, select it, land on Memories. */
    const adopt = useCallback((workspace) => {
        setState((s) => ({
            ...s,
            status: "ready",
            error: null,
            list: s.list.some((w) => w.id === workspace.id) ? s.list : [...s.list, workspace],
            currentId: workspace.id,
        }));
        selectWorkspace(workspace.id);
        navigate("/memories");
        toast.success(`Workspace "${workspace.name}" created`);
    }, [navigate]);

    /** A creation the user walked away from: list it, do not switch to it. */
    const noteCreated = useCallback((workspace) => {
        setState((s) => ({
            ...s,
            list: s.list.some((w) => w.id === workspace.id) ? s.list : [...s.list, workspace],
        }));
        toast.success(`Workspace "${workspace.name}" created`);
    }, []);

    const value = useMemo(
        () => ({ ...state, reload, switchTo, adopt, noteCreated }),
        [state, reload, switchTo, adopt, noteCreated]
    );
    return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export default WorkspaceProvider;
