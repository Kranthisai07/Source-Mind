import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import GraphCanvas from "./GraphCanvas";
import GraphView, { LARGE_GRAPH } from "./GraphView";
import { relatedToGraph } from "./graphModel";

const related = (overrides = {}) => ({
    memory_id: "m1",
    source: "graph",
    depth_requested: 1,
    depth_served: 1,
    nodes: [
        { id: "m1", version: 3, is_current: true, importance: 0.8, hops: 0, preview: "Focal memory" },
        { id: "m2", version: 2, is_current: false, importance: 0.4, hops: 1, preview: "Older memory" },
        { id: "m3", version: 1, is_current: true, importance: 0.5, hops: 1, preview: "Sibling memory" },
    ],
    edges: [
        { id: "e1", source: "m1", target: "m2", type: "updates", confidence: 0.9 },
        { id: "e2", source: "m1", target: "m3", type: "extends", confidence: 0.7 },
    ],
    ...overrides,
});

const resourceOf = (data, extra = {}) => ({ data, error: null, loading: false, retry: jest.fn(), ...extra });

const renderView = (resource, props = {}) =>
    render(
        <MemoryRouter>
            <GraphView title="Related memories" resource={resource} build={relatedToGraph} {...props} />
        </MemoryRouter>
    );

describe("GraphCanvas", () => {
    const graph = relatedToGraph(related());
    const draw = (props = {}) =>
        render(<GraphCanvas nodes={graph.nodes} edges={graph.edges} label="Related" {...props} />);

    test("every memory is a labelled, focusable button, and the focal one says so", () => {
        draw();
        const focal = screen.getByTestId("graph-node-m1");
        expect(focal.getAttribute("role")).toBe("button");
        expect(focal.getAttribute("tabindex")).toBe("0");
        expect(focal.getAttribute("aria-label")).toMatch(/^This memory, Memory version 3, current/);
        expect(screen.getByTestId("graph-node-m2").getAttribute("aria-label")).toMatch(/superseded/);
    });

    test("clicking selects, clicking again clears", () => {
        const onSelect = jest.fn();
        const { rerender } = draw({ onSelect });
        fireEvent.click(screen.getByTestId("graph-node-m2"));
        expect(onSelect).toHaveBeenLastCalledWith("m2");
        rerender(<GraphCanvas nodes={graph.nodes} edges={graph.edges} selectedId="m2" onSelect={onSelect} />);
        expect(screen.getByTestId("graph-node-m2").getAttribute("aria-pressed")).toBe("true");
        fireEvent.click(screen.getByTestId("graph-node-m2"));
        expect(onSelect).toHaveBeenLastCalledWith(null);
    });

    test("Enter and Space select, Escape clears, arrows move focus and wrap", () => {
        const onSelect = jest.fn();
        draw({ onSelect });
        const first = screen.getByTestId("graph-node-m1");
        first.focus();
        fireEvent.keyDown(first, { key: "Enter" });
        expect(onSelect).toHaveBeenLastCalledWith("m1");
        fireEvent.keyDown(first, { key: " " });
        expect(onSelect).toHaveBeenCalledTimes(2);
        fireEvent.keyDown(first, { key: "ArrowRight" });
        expect(document.activeElement).toBe(screen.getByTestId("graph-node-m2"));
        fireEvent.keyDown(screen.getByTestId("graph-node-m2"), { key: "ArrowLeft" });
        expect(document.activeElement).toBe(first);
        fireEvent.keyDown(first, { key: "ArrowLeft" }); // wraps to the last
        expect(document.activeElement).toBe(screen.getByTestId("graph-node-m3"));
        fireEvent.keyDown(screen.getByTestId("graph-node-m3"), { key: "Home" });
        expect(document.activeElement).toBe(first);
        fireEvent.keyDown(first, { key: "Escape" });
        expect(onSelect).toHaveBeenLastCalledWith(null);
    });

    test("only the edges at the active node are labelled, and the rest dim", () => {
        draw();
        expect(document.querySelectorAll(".gv-edge-label")).toHaveLength(0);
        fireEvent.pointerEnter(screen.getByTestId("graph-node-m2"));
        const labels = [...document.querySelectorAll(".gv-edge-label")].map((n) => n.textContent);
        expect(labels).toEqual(["Updates"]);
        expect(screen.getByTestId("graph-node-m3").classList.contains("gv-dim")).toBe(true);
        expect(screen.getByTestId("graph-node-m1").classList.contains("gv-dim")).toBe(false);
        fireEvent.pointerLeave(screen.getByTestId("graph-node-m2"));
        expect(document.querySelectorAll(".gv-dim")).toHaveLength(0);
    });

    test("directed relations get an arrow head and undirected ones do not", () => {
        draw();
        const arrows = [...document.querySelectorAll("polygon.gv-arrow")].map((n) => n.getAttribute("class"));
        expect(arrows).toHaveLength(1);
        expect(arrows[0]).toMatch(/gv-edge-updates/);
    });

    test("an edge to a node that is not drawn is skipped instead of throwing", () => {
        expect(() =>
            render(
                <GraphCanvas
                    nodes={graph.nodes}
                    edges={[{ id: "x", source: "m1", target: "nope", type: "updates" }]}
                />
            )
        ).not.toThrow();
        expect(document.querySelectorAll("line.gv-edge")).toHaveLength(0);
    });

    test("memory text from the API is rendered as text, never as markup", () => {
        const hostile = relatedToGraph(
            related({
                nodes: [
                    { id: "m1", version: 1, is_current: true, importance: 1, hops: 0, preview: "<img src=x onerror=alert(1)>" },
                ],
                edges: [],
            })
        );
        render(<GraphCanvas nodes={hostile.nodes} edges={[]} />);
        expect(document.querySelector("img")).toBeNull();
        expect(screen.getByTestId("graph-node-m1").getAttribute("aria-label")).toContain("<img");
    });
});

describe("GraphView", () => {
    test("loading shows a busy status, not an empty state", () => {
        renderView(resourceOf(null, { loading: true }));
        const status = screen.getByTestId("graph-view-loading");
        expect(status.getAttribute("aria-busy")).toBe("true");
        expect(screen.queryByTestId("graph-view-empty")).toBeNull();
    });

    test("an error with no data shows the error with a working retry", () => {
        const retry = jest.fn();
        renderView(resourceOf(null, { error: Object.assign(new Error("boom"), { status: 500 }), retry }));
        const box = screen.getByTestId("graph-view-error");
        fireEvent.click(within(box).getByRole("button"));
        expect(retry).toHaveBeenCalled();
        expect(screen.queryByTestId("graph-view-empty")).toBeNull();
    });

    test("a memory with no neighbours is an empty state, not a lonely hexagon", () => {
        renderView(resourceOf(related({ nodes: [related().nodes[0]], edges: [] })), { emptyTitle: "No related memories yet" });
        expect(screen.getByTestId("graph-view-empty").textContent).toContain("No related memories yet");
        expect(screen.queryByTestId("graph-view-canvas")).toBeNull();
    });

    test("data shows the picture, a legend limited to what is drawn, and the source chip", () => {
        renderView(resourceOf(related()));
        expect(screen.getByTestId("graph-view-canvas")).not.toBeNull();
        const legend = screen.getByTestId("graph-view-legend");
        expect(legend.textContent).toContain("Updates");
        expect(legend.textContent).toContain("Extends");
        expect(legend.textContent).not.toContain("Contradicts");
        expect(screen.getByTestId("graph-view-source").getAttribute("data-source")).toBe("graph");
    });

    test("the database fallback is labelled as such", () => {
        renderView(resourceOf(related({ source: "postgres" })));
        expect(screen.getByTestId("graph-view-source").getAttribute("data-source")).toBe("postgres");
        expect(screen.getByTestId("graph-view-source").textContent).toContain("Database");
    });

    test("selecting a node opens its detail panel, with a link to open that memory", () => {
        renderView(resourceOf(related()));
        expect(screen.getByTestId("graph-view-hint")).not.toBeNull();
        fireEvent.click(screen.getByTestId("graph-node-m2"));
        const panel = screen.getByTestId("graph-view-detail");
        expect(panel.textContent).toContain("Memory v2");
        expect(panel.textContent).toContain("Superseded");
        expect(within(panel).getByTestId("graph-view-detail-open").getAttribute("href")).toBe("/memories/m2");
        fireEvent.click(within(panel).getByLabelText("Close details"));
        expect(screen.queryByTestId("graph-view-detail")).toBeNull();
    });

    test("the focal memory has no 'open' link to itself", () => {
        renderView(resourceOf(related()));
        fireEvent.click(screen.getByTestId("graph-node-m1"));
        expect(screen.queryByTestId("graph-view-detail-open")).toBeNull();
    });

    test("the list view carries the same nodes and links as the picture", () => {
        renderView(resourceOf(related()));
        fireEvent.click(screen.getByTestId("graph-view-view-list"));
        const list = screen.getByTestId("graph-view-list");
        const rows = within(list).getAllByRole("row");
        expect(rows).toHaveLength(1 + 3); // header + one per memory
        expect(list.textContent).toContain("Updates v2");
        expect(list.textContent).toContain("Superseded");
        expect(screen.queryByTestId("graph-view-canvas")).toBeNull();
        expect(screen.getByTestId("graph-view-view-list").getAttribute("aria-pressed")).toBe("true");
    });

    test("a big graph opens as the list, and the picture is one click away", () => {
        const nodes = [related().nodes[0]];
        const edges = [];
        for (let i = 0; i < LARGE_GRAPH + 1; i += 1) {
            nodes.push({ id: `n${i}`, version: 1, is_current: true, importance: 0.5, hops: 1, preview: `Memory ${i}` });
            edges.push({ id: `e${i}`, source: "m1", target: `n${i}`, type: "references" });
        }
        renderView(resourceOf(related({ nodes, edges })));
        expect(screen.getByTestId("graph-view-list")).not.toBeNull();
        fireEvent.click(screen.getByTestId("graph-view-view-graph"));
        expect(screen.getByTestId("graph-view-canvas")).not.toBeNull();
    });

    test("a refetch keeps the previous picture on screen, dimmed, and marks it busy", () => {
        const { container } = renderView(resourceOf(related(), { loading: true }));
        expect(screen.getByTestId("graph-view-canvas")).not.toBeNull();
        expect(container.querySelector('[aria-busy="true"]').classList.contains("opacity-60")).toBe(true);
    });

    test("a note and a truncation warning are shown only with data", () => {
        renderView(resourceOf(related({ truncated: true })), { note: "Showing 1 hop." });
        expect(screen.getByTestId("graph-view-note").textContent).toContain("Showing 1 hop.");
        expect(screen.getByTestId("graph-view-truncated")).not.toBeNull();
    });

    test("a person graph is a list of people and the people they share memories with", () => {
        const people = {
            source: "postgres",
            nodes: [
                { user_id: "u1", display_name: "Ada", memory_count: 5 },
                { user_id: "u2", display_name: "Grace", memory_count: 3 },
            ],
            edges: [{ source: "u1", target: "u2", shared_memories: 2 }],
        };
        // eslint-disable-next-line global-require
        const { collaborationToGraph } = require("./graphModel");
        render(
            <MemoryRouter>
                <GraphView
                    title="Collaboration"
                    resource={resourceOf(people)}
                    build={collaborationToGraph}
                    kind="people"
                    layout="circular"
                    testId="collab"
                />
            </MemoryRouter>
        );
        fireEvent.click(screen.getByTestId("collab-view-list"));
        expect(screen.getByTestId("collab-list").textContent).toContain("Grace (2)");
    });
});
