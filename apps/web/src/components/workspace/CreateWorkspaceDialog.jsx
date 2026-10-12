import React from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "../ui/dialog";
import CreateWorkspaceForm from "./CreateWorkspaceForm";
import { useWorkspaces } from "./WorkspaceProvider";

export default function CreateWorkspaceDialog({ open, onOpenChange }) {
    const { adopt } = useWorkspaces();
    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="bg-sm-surface border-sm-border text-sm-text" data-testid="create-workspace-dialog">
                <DialogHeader>
                    <DialogTitle>Create workspace</DialogTitle>
                    <DialogDescription className="text-sm-text-secondary">
                        A separate space for a team's memories. You will be its owner and can add people afterwards.
                    </DialogDescription>
                </DialogHeader>
                {/* Remounted per open so a closed dialog never keeps a draft. */}
                {open && (
                    <CreateWorkspaceForm
                        idPrefix="cwd"
                        onCancel={() => onOpenChange(false)}
                        onCreated={(workspace) => {
                            onOpenChange(false);
                            adopt(workspace);
                        }}
                    />
                )}
            </DialogContent>
        </Dialog>
    );
}
