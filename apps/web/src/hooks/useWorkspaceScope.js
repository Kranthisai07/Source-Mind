import { useSyncExternalStore } from "react";
import { onWorkspaceScopeChange, workspaceGeneration } from "@/lib/realApi";

/**
 * The current workspace-scope generation, as React state.
 *
 * It changes when the user switches workspace or the signed-in user changes.
 * Keying a subtree on it (`<Outlet key={scope} />`) discards that subtree's
 * state, effects, timers and refs in one step, so nothing from the previous
 * workspace can linger on screen.
 */
export function useWorkspaceScope() {
    return useSyncExternalStore(onWorkspaceScopeChange, workspaceGeneration);
}

export default useWorkspaceScope;
