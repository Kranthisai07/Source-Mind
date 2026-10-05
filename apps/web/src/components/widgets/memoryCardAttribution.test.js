import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import MemoryCard from "./MemoryCard";

const memory = {
    id: "00000000-0000-4000-8000-000000000001",
    content: "Synthetic memory content.",
    created_at: "2026-10-04T12:00:00Z",
    tags: [],
};

test("renders an explicit unattributed state without inventing a handle", () => {
    render(
        <MemoryRouter>
            <MemoryCard memory={{ ...memory, attribution: [] }} />
        </MemoryRouter>
    );

    expect(screen.getByTestId("memory-attribution-unattributed").textContent).toBe("Unattributed");
    expect(document.body.textContent).not.toContain("@unattributed");
});

test("renders a real contributor handle when attribution is present", () => {
    render(
        <MemoryRouter>
            <MemoryCard
                memory={{
                    ...memory,
                    attribution: [{ author: "synthetic-author", name: "Synthetic Author", score: 1, is_primary: true }],
                }}
            />
        </MemoryRouter>
    );

    expect(screen.getByText("Synthetic Author")).toBeTruthy();
    expect(screen.getByTestId("memory-attribution-handle").textContent).toBe("@synthetic-author");
    expect(screen.queryByTestId("memory-attribution-unattributed")).toBeNull();
});
