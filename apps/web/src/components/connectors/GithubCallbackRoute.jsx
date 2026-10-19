import React, { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { stashGithubCallback } from "./connectorText";

// Route element for /connectors/github/callback. GitHub sends the browser here
// with ?code=..&state=..&installation_id=.. (or ?error=access_denied). The
// values are moved into memory and the URL is replaced immediately, so the
// single-use code is not left in the address bar, history or a bookmark.
export default function GithubCallbackRoute() {
    const location = useLocation();
    const navigate = useNavigate();

    useEffect(() => {
        const params = new URLSearchParams(location.search);
        const installation = Number.parseInt(params.get("installation_id") || "", 10);
        stashGithubCallback({
            code: params.get("code") || null,
            state: params.get("state") || null,
            installation_id: Number.isInteger(installation) && installation > 0 ? installation : null,
            denied: params.get("error") ? true : false,
        });
        navigate("/connectors", { replace: true });
        // Runs once per mount; location.search is the arrival URL.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return (
        <div data-testid="github-callback-route" className="flex-1 px-8 py-6 text-[13px] text-sm-text-secondary">
            Returning from GitHub…
        </div>
    );
}
