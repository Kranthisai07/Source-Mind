import React from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "../ui/dialog";
import CreateWorkspaceForm from "./CreateWorkspaceForm";
import { useWorkspaces } from "./WorkspaceProvider";

export default function CreateWorkspaceDialog({ open, onOpenChange }) {
    const { adopt, noteCreated } = useWorkspaces();
    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="bg-sm-surface border-sm-border text-sm-text max-h-[90vh] overflow-y-auto" data-testid="create-workspace-dialog">
                <DialogHeader>
                    <DialogTitle>Create workspace</DialogTitle>
                    <DialogDescription className="text-sm-text-secondary">
                        A separate space for a team's memories. You will be a founder, and you can set up the rest of the team next.
                    </DialogDescription>
                </DialogHeader>
                {/* Remounted per open so a closed dialog never keeps a draft. */}
                {open && (
                    <CreateWorkspaceForm
                        idPrefix="cwd"
                        teamSetup
                        onCancel={() => onOpenChange(false)}
                        onCreated={(workspace, { dismissed } = {}) => {
                            // Dismissed while the request was pending: the
                            // workspace exists, but the user moved on, so do
                            // not switch or navigate under them.
                            if (dismissed) {
                                noteCreated(workspace);
                                return;
                            }
                            onOpenChange(false);
                            adopt(workspace);
                        }}
                    />
                )}
            </DialogContent>
        </Dialog>
    );
}
