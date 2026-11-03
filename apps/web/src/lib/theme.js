/** Theme colours as CSS variables, so charts and inline styles follow light/dark and the landing palette. */
export const ACCENT = "rgb(var(--sm-blue-rgb))";
export const SECONDARY = "rgb(var(--sm-purple-rgb))";
/** A tint of any colour string (hex, rgb(), var()) over transparent. */
export const tint = (color, percent) => `color-mix(in srgb, ${color} ${percent}%, transparent)`;
