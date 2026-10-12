import React, { useEffect, useRef, useState } from "react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Textarea } from "../ui/textarea";
import api from "../../lib/api";
import { slugify, validateWorkspaceForm } from "../../lib/slug";
import { mapWorkspaceError } from "../../lib/workspaceErrors";

/**
 * Name + URL name (+ optional description) for a new workspace.
 *
 * The backend does not de-duplicate creation requests, so a second submit
 * while one is in flight would create a second workspace. The guard is a ref,
 * not state: two clicks in the same tick both see the stale `submitting`
 * value from the last render.
 */
export default function CreateWorkspaceForm({ onCreated, onCancel, idPrefix = "cw" }) {
    const [name, setName] = useState("");
    const [slug, setSlug] = useState("");
    const [slugEdited, setSlugEdited] = useState(false);
    const [description, setDescription] = useState("");
    const [fieldErrors, setFieldErrors] = useState({});
    const [serverError, setServerError] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const inFlight = useRef(false);
    const mounted = useRef(true);
    useEffect(() => () => { mounted.current = false; }, []);

    const onName = (value) => {
        setName(value);
        if (!slugEdited) setSlug(slugify(value));
    };

    const submit = async (event) => {
        event.preventDefault();
        if (inFlight.current) return;
        const errors = validateWorkspaceForm({ name, slug, description });
        setFieldErrors(errors);
        setServerError(null);
        if (Object.keys(errors).length) return;

        inFlight.current = true;
        setSubmitting(true);
        try {
            const workspace = await api.createWorkspace({
                name: name.trim(),
                slug,
                description: description.trim() || undefined,
            });
            await onCreated(workspace);
        } catch (err) {
            if (!mounted.current) return;
            const mapped = mapWorkspaceError(err, "create");
            if (mapped.field === "slug") setFieldErrors({ slug: mapped.message });
            else setServerError(mapped.message);
        } finally {
            inFlight.current = false;
            if (mounted.current) setSubmitting(false);
        }
    };

    const label = "block text-[12px] text-sm-text-secondary mb-1.5 font-medium";
    const input = "bg-sm-bg/60 border-sm-border text-sm-text h-10";
    const err = "mt-1 text-[11.5px] text-sm-red";
    return (
        <form onSubmit={submit} noValidate className="space-y-4" data-testid="create-workspace-form">
            <div>
                <label htmlFor={`${idPrefix}-name`} className={label}>Workspace name</label>
                <Input
                    id={`${idPrefix}-name`}
                    data-testid="create-workspace-name"
                    value={name}
                    onChange={(e) => onName(e.target.value)}
                    placeholder="Friends"
                    maxLength={255}
                    autoComplete="off"
                    aria-invalid={Boolean(fieldErrors.name)}
                    className={input}
                />
                {fieldErrors.name && <p role="alert" data-testid="create-workspace-name-error" className={err}>{fieldErrors.name}</p>}
            </div>
            <div>
                <label htmlFor={`${idPrefix}-slug`} className={label}>URL name</label>
                <Input
                    id={`${idPrefix}-slug`}
                    data-testid="create-workspace-slug"
                    value={slug}
                    onChange={(e) => { setSlugEdited(true); setSlug(e.target.value); }}
                    placeholder="friends"
                    maxLength={100}
                    autoComplete="off"
                    spellCheck={false}
                    aria-invalid={Boolean(fieldErrors.slug)}
                    className={`${input} font-mono text-[12.5px]`}
                />
                {fieldErrors.slug && <p role="alert" data-testid="create-workspace-slug-error" className={err}>{fieldErrors.slug}</p>}
            </div>
            <div>
                <label htmlFor={`${idPrefix}-description`} className={label}>Description (optional)</label>
                <Textarea
                    id={`${idPrefix}-description`}
                    data-testid="create-workspace-description"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    rows={3}
                    maxLength={1000}
                    className="bg-sm-bg/60 border-sm-border text-sm-text resize-none"
                />
                {fieldErrors.description && <p role="alert" className={err}>{fieldErrors.description}</p>}
            </div>
            {serverError && (
                <p role="alert" data-testid="create-workspace-error" className="text-[12.5px] text-sm-red">{serverError}</p>
            )}
            <div className="flex justify-end gap-2">
                {onCancel && (
                    <Button type="button" variant="outline" onClick={onCancel} disabled={submitting} className="border-sm-border text-sm-text">
                        Cancel
                    </Button>
                )}
                <Button type="submit" data-testid="create-workspace-submit" disabled={submitting} className="bg-sm-blue hover:bg-sm-blue/90 text-white">
                    {submitting ? "Creating..." : "Create workspace"}
                </Button>
            </div>
        </form>
    );
}
