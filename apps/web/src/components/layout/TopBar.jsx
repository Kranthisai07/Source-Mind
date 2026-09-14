import React from "react";
import PageHeader from "../ui-kit/PageHeader";

/**
 * MIGRATION SHIM — not the target design.
 *
 * TopBar used to be a 64px sticky full-width bar carrying the page title. §1
 * replaces that with two separate things: a ~48px strip (now TopStrip, in
 * AppLayout) and an in-content H1 + subtext block (now PageHeader).
 *
 * The six pages that have not yet been redesigned still import TopBar. Left
 * as it was, each would render a second header directly beneath the new strip.
 * Forwarding to PageHeader means they pick up the correct §1 header formula
 * immediately, and each page's own turn only has to delete the import.
 *
 * Delete this file once no page imports it.
 */
export default function TopBar({ title, subtitle, actions }) {
    return (
        <PageHeader
            title={title}
            subtitle={subtitle ?? ""}
            action={actions}
        />
    );
}
