// Matches the backend's WorkspaceCreate.slug validator.
export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** A URL-safe suggestion derived from a workspace name. */
export function slugify(name) {
    return String(name || "")
        .normalize("NFKD")
        .replace(/[̀-ͯ]/g, "")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 100)
        .replace(/-+$/g, "");
}

/** Client-side checks mirroring the backend limits. Returns {field: message}. */
export function validateWorkspaceForm({ name, slug, description }) {
    const errors = {};
    const n = String(name || "").trim();
    if (n.length < 2) errors.name = "Give the workspace a name of at least 2 characters.";
    else if (n.length > 255) errors.name = "The name must be 255 characters or fewer.";
    const s = String(slug || "");
    if (s.length < 2 || s.length > 100 || !SLUG_RE.test(s)) {
        errors.slug = "Use 2-100 lowercase letters, numbers and single hyphens (not at the start or end).";
    }
    if (String(description || "").trim().length > 1000) {
        errors.description = "The description must be 1000 characters or fewer.";
    }
    return errors;
}
