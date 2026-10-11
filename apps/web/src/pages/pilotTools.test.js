import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const mockGetCurrentUser = jest.fn();
const mockListMembers = jest.fn();
const mockCreateRating = jest.fn();
const mockGetExport = jest.fn();
const mockResolveWorkspace = jest.fn();

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        getCurrentUser: (...args) => mockGetCurrentUser(...args),
        listTeamMembers: (...args) => mockListMembers(...args),
        createPilotRating: (...args) => mockCreateRating(...args),
        getPilotExport: (...args) => mockGetExport(...args),
    },
    resolveCurrentWorkspace: (...args) => mockResolveWorkspace(...args),
}));

import PilotTools, { validatePilotRating } from "./PilotTools";

const WORKSPACE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const OPERATOR_ID = "11111111-1111-4111-8111-111111111111";
const RATER_ID = "22222222-2222-4222-8222-222222222222";
const EVENT_ID = "33333333-3333-4333-8333-333333333333";
const MEMORY_ID = "44444444-4444-4444-8444-444444444444";

beforeEach(() => {
    process.env.REACT_APP_PILOT_MODE = "true";
    mockResolveWorkspace.mockReset().mockResolvedValue(WORKSPACE_ID);
    mockGetCurrentUser.mockReset().mockResolvedValue({ id: OPERATOR_ID });
    mockListMembers.mockReset().mockResolvedValue({
        members: [
            { role: "owner", user: { id: OPERATOR_ID, display_name: "Operator" } },
            { role: "member", user: { id: RATER_ID, display_name: "Rater Two" } },
        ],
    });
    mockCreateRating.mockReset().mockResolvedValue({ id: "rating-1" });
    mockGetExport.mockReset().mockResolvedValue({ schema_version: 2, canonical_sha256: "abc" });
});

afterEach(() => {
    delete process.env.REACT_APP_PILOT_MODE;
});

async function renderOperator() {
    render(<PilotTools />);
    expect(await screen.findByTestId("pilot-rating-form")).toBeTruthy();
}

test.each(["owner", "admin"])("%s receives rating and export controls", async (role) => {
    mockListMembers.mockResolvedValue({
        members: [{ role, user: { id: OPERATOR_ID, display_name: "Operator" } }],
    });

    await renderOperator();

    expect(screen.getByTestId("pilot-export-panel")).toBeTruthy();
});

function fillSharedFields() {
    fireEvent.change(screen.getByTestId("rating-search-event"), { target: { value: EVENT_ID } });
    fireEvent.change(screen.getByTestId("rating-memory"), { target: { value: MEMORY_ID } });
}

test("participants do not receive rating or export controls", async () => {
    mockListMembers.mockResolvedValue({
        members: [{ role: "member", user: { id: OPERATOR_ID, display_name: "Participant" } }],
    });

    render(<PilotTools />);

    expect(await screen.findByTestId("pilot-access-denied")).toBeTruthy();
    expect(screen.queryByTestId("pilot-rating-form")).toBeNull();
    expect(screen.queryByTestId("pilot-export-panel")).toBeNull();
});

test("validates required rating fields before sending", async () => {
    await renderOperator();
    fireEvent.click(screen.getByTestId("rating-submit"));

    expect(await screen.findByTestId("rating-error")).toBeTruthy();
    expect(mockCreateRating).not.toHaveBeenCalled();
});

test("submits an independent rating with the selected workspace rater", async () => {
    await renderOperator();
    fillSharedFields();
    fireEvent.change(screen.getByTestId("rating-rater"), { target: { value: RATER_ID } });
    fireEvent.change(screen.getByTestId("rating-allocation"), { target: { value: "35.5" } });
    fireEvent.click(screen.getByTestId("rating-submit"));

    await waitFor(() => expect(mockCreateRating).toHaveBeenCalledTimes(1));
    expect(mockCreateRating.mock.calls[0][0]).toMatchObject({
        workspace_id: WORKSPACE_ID,
        search_event_id: EVENT_ID,
        memory_id: MEMORY_ID,
        rating_source: "independent",
        rater_user_id: RATER_ID,
        rater_pseudonym: null,
        allocation: 35.5,
        exclusion_reason: null,
    });
    expect(mockCreateRating.mock.calls[0][0].idempotencyKey).toMatch(/^[0-9a-f-]{36}$/i);
    expect(await screen.findByTestId("rating-success")).toBeTruthy();
});

test("repeated submit after success sends only one request", async () => {
    await renderOperator();
    fillSharedFields();
    fireEvent.change(screen.getByTestId("rating-rater"), { target: { value: RATER_ID } });
    fireEvent.change(screen.getByTestId("rating-allocation"), { target: { value: "35.5" } });
    fireEvent.click(screen.getByTestId("rating-submit"));

    expect(await screen.findByTestId("rating-success")).toBeTruthy();
    expect(screen.getByTestId("rating-submit").disabled).toBe(true);
    fireEvent.click(screen.getByTestId("rating-submit"));

    expect(mockCreateRating).toHaveBeenCalledTimes(1);
});

test("changing a submitted rating enables a new request with a new key", async () => {
    await renderOperator();
    fillSharedFields();
    fireEvent.change(screen.getByTestId("rating-rater"), { target: { value: RATER_ID } });
    fireEvent.change(screen.getByTestId("rating-allocation"), { target: { value: "35.5" } });
    fireEvent.click(screen.getByTestId("rating-submit"));

    expect(await screen.findByTestId("rating-success")).toBeTruthy();
    const firstKey = mockCreateRating.mock.calls[0][0].idempotencyKey;
    fireEvent.change(screen.getByTestId("rating-allocation"), { target: { value: "40" } });
    expect(screen.getByTestId("rating-submit").disabled).toBe(false);
    fireEvent.click(screen.getByTestId("rating-submit"));

    await waitFor(() => expect(mockCreateRating).toHaveBeenCalledTimes(2));
    expect(mockCreateRating.mock.calls[1][0].idempotencyKey).not.toBe(firstKey);
    expect(mockCreateRating.mock.calls[1][0].allocation).toBe(40);
});

test("submits an external exclusion without a workspace user", async () => {
    await renderOperator();
    fillSharedFields();
    fireEvent.change(screen.getByTestId("rating-source"), { target: { value: "external" } });
    fireEvent.change(screen.getByTestId("rating-pseudonym"), { target: { value: "R-03" } });
    fireEvent.change(screen.getByTestId("rating-kind"), { target: { value: "exclusion" } });
    fireEvent.change(screen.getByTestId("rating-exclusion"), { target: { value: "Insufficient context" } });
    fireEvent.click(screen.getByTestId("rating-submit"));

    await waitFor(() => expect(mockCreateRating).toHaveBeenCalledTimes(1));
    expect(mockCreateRating.mock.calls[0][0]).toMatchObject({
        rating_source: "external",
        rater_user_id: null,
        rater_pseudonym: "R-03",
        allocation: null,
        exclusion_reason: "Insufficient context",
    });
});

test("validation supports self ratings and rejects incomplete external ratings", () => {
    const base = {
        ...initialValidationForm(),
        searchEventId: EVENT_ID,
        memoryId: MEMORY_ID,
        ratingSource: "self",
        raterUserId: RATER_ID,
        allocation: "20",
    };
    expect(validatePilotRating(base)).toBeNull();
    expect(validatePilotRating({ ...base, ratingSource: "external", raterUserId: "", raterPseudonym: "" }))
        .toMatch(/pseudonym/i);
});

function initialValidationForm() {
    return {
        searchEventId: "",
        memoryId: "",
        ratingSource: "independent",
        raterUserId: "",
        raterPseudonym: "",
        ratingKind: "allocation",
        allocation: "",
        exclusionReason: "",
    };
}
