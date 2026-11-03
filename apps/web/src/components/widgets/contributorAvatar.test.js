import React from "react";
import { render, screen } from "@testing-library/react";
import ContributorAvatar from "./ContributorAvatar";

test("without a colour, initials use the theme's on-accent colour (readable on dark-mode sage)", () => {
    render(<ContributorAvatar contributor={{ name: "Ana Diaz" }} />);
    const el = screen.getByTestId("contributor-avatar");
    expect(el.className).toContain("text-[color:var(--sm-on-accent)]");
    expect(el.className).not.toContain("text-white");
});

test("a colour supplied with the data keeps white initials", () => {
    render(<ContributorAvatar contributor={{ name: "Ana Diaz", avatarColor: "#A78BFA" }} />);
    expect(screen.getByTestId("contributor-avatar").className).toContain("text-white");
});
