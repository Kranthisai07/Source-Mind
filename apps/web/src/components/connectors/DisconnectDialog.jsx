import React, { useEffect, useRef, useState } from "react";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "../ui/dialog";
import { Button } from "../ui/button";
import api from "../../lib/api";
import { describeConnectorError } from "./connectorText";

// The two choices, in plain words. Nothing is preselected: disconnecting is
// destructive enough that the admin states which one they mean.
export default function DisconnectDialog({ connector, onClose, onDone }) {
    const [choice, setChoice] = useState(null); // "keep" | "remove"
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const alive = useRef(true);
    useEffect(() => {
        alive.current = true;
        return () => { alive.current = false; };
    }, []);

    const confirm = async () => {
        if (!choice || busy) return;
        setBusy(true);
        setError(null);
        try {
            const result = await api.deleteConnector(connector.id, { remove_memories: choice === "remove" });
            if (alive.current) onDone(result);
        } catch (requestError) {
            if (alive.current) setError(describeConnectorError(requestError, "Disconnecting failed."));
        } finally {
            if (alive.current) setBusy(false);
        }
    };

    return (
        <Dialog open={Boolean(connector)} onOpenChange={(v) => { if (!v && !busy) onClose(); }}>
            <DialogContent className="bg-sm-surface border-sm-border text-sm-text" data-testid="disconnect-dialog">
                <DialogHeader>
                    <DialogTitle>Disconnect {connector?.display_name}?</DialogTitle>
                    <DialogDescription className="text-sm-text-secondary">
                        Syncing stops and cannot be resumed; to import again you connect GitHub again. Choose what happens to what was already imported.
                    </DialogDescription>
                </DialogHeader>
                <div className="space-y-3" role="radiogroup" aria-label="What happens to imported memories">
                    <label className="flex items-start gap-3 rounded-md border border-sm-border p-3 cursor-pointer">
                        <input
                            type="radio" name="disconnect-choice" value="keep"
                            checked={choice === "keep"} onChange={() => setChoice("keep")}
                            data-testid="disconnect-option-keep" className="mt-1"
                        />
                        <span className="text-[12.5px]">
                            <span className="block font-medium">Keep imported memories</span>
                            <span className="text-sm-text-secondary">They stay searchable. They are no longer updated from GitHub.</span>
                        </span>
                    </label>
                    <label className="flex items-start gap-3 rounded-md border border-sm-border p-3 cursor-pointer">
                        <input
                            type="radio" name="disconnect-choice" value="remove"
                            checked={choice === "remove"} onChange={() => setChoice("remove")}
                            data-testid="disconnect-option-remove" className="mt-1"
                        />
                        <span className="text-[12.5px]">
                            <span className="block font-medium">Remove imported memories</span>
                            <span className="text-sm-text-secondary">
                                Imported memories nobody has edited are removed from search. Memories someone has edited by hand stay searchable and are marked as no longer backed by their source.
                            </span>
                        </span>
                    </label>
                </div>
                {error && <p role="alert" data-testid="disconnect-error" className="text-[12px] text-sm-red">{error}</p>}
                <DialogFooter>
                    <Button variant="outline" onClick={onClose} disabled={busy} data-testid="disconnect-cancel">Cancel</Button>
                    <Button onClick={confirm} disabled={!choice || busy} data-testid="disconnect-confirm" className="bg-sm-red text-white">
                        {busy ? "Disconnecting…" : "Disconnect"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
