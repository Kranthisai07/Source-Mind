const TRUE_VALUES = new Set(["1", "true", "yes", "on"]);

export function isPilotMode() {
    return TRUE_VALUES.has(String(process.env.REACT_APP_PILOT_MODE || "").trim().toLowerCase());
}

export function isPilotOperatorRole(role) {
    return ["owner", "admin"].includes(String(role || "").toLowerCase());
}
