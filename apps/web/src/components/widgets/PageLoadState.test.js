import React from "react";
import { render, screen } from "@testing-library/react";

import { appUrl } from "../../lib/appUrl";
import { PageLoadError } from "./PageLoadState";

test("offers the existing sign-in route for an expired session", () => {
    render(
        <PageLoadError
            error={{
                kind: "auth",
                title: "Your session has expired",
                detail: "Sign in again to continue.",
                retryable: false,
            }}
        />
    );

    expect(screen.getByTestId("page-load-error-reauth").getAttribute("href"))
        .toBe(appUrl("/sign-in"));
});
