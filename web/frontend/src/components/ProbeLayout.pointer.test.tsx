import type { CanvasHTMLAttributes, ReactElement, RefObject } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { probeUnitsInPolygon } from "../probeSelection";
import type { ProbeGeometry } from "../types";
import ProbeLayout from "./ProbeLayout";

const hooks = vi.hoisted(() => ({ effects: [] as Array<() => void> }));
vi.mock("react", async (importOriginal) => ({
  ...await importOriginal<typeof import("react")>(),
  // Mount the canvas effect and actual JSX pointer handlers without a DOM.
  // State redraws are irrelevant to the ref-backed in-progress pointer path.
  useEffect: (effect: () => void) => { hooks.effects.push(effect); },
  useMemo: (factory: () => unknown) => factory(),
  useRef: (current: unknown) => ({ current }),
  useState: (initial: unknown) => [initial, () => undefined],
}));
afterEach(() => { hooks.effects = []; vi.unstubAllGlobals(); });

const geometry: ProbeGeometry = {
  probe: "ProbeA", channels: [], units: [
    { unitId: 17, x: 10, y: 100 },
    { unitId: 42, x: 35, y: 200 },
    { unitId: 99, x: 65, y: 300 },
    { unitId: 101, x: null, y: null },
  ],
};

type CanvasProps = CanvasHTMLAttributes<HTMLCanvasElement> & { ref: RefObject<HTMLCanvasElement | null> };

function mountProbe(depthZoom: number) {
  const onLasso = vi.fn<(vertices: Array<readonly [number, number]>) => void>();
  const onSelection = vi.fn(), onCluster = vi.fn();
  const tree = ProbeLayout({ geometry, availableUnitIds: [17, 42, 99, 101], currentClusterId: -1,
    selection: null, onSelection, onCluster, onLasso, depthZoom });
  const canvasElement = (tree.props.children as ReactElement<CanvasProps>[])[0];
  const drawnUnits: Array<[number, number]> = [];
  const context = {
    setTransform: vi.fn(), fillRect: vi.fn(), fillText: vi.fn(), strokeRect: vi.fn(),
    beginPath: vi.fn(), arc: (x: number, y: number) => { drawnUnits.push([x, y]); },
    fill: vi.fn(), stroke: vi.fn(), save: vi.fn(), translate: vi.fn(), rotate: vi.fn(), restore: vi.fn(),
  };
  // A negative viewport top exercises a canvas scrolled within the zoom view.
  const bounds = { left: 137, top: depthZoom > 1 ? -180 : 63 };
  const canvas = { width: 0, height: 0, style: {}, getContext: () => context,
    getBoundingClientRect: () => bounds, setPointerCapture: vi.fn() };
  canvasElement.props.ref.current = canvas as unknown as HTMLCanvasElement;
  vi.stubGlobal("window", { devicePixelRatio: 2 });
  hooks.effects.forEach((effect) => effect());
  const pointer = (handler: "onPointerDown" | "onPointerMove" | "onPointerUp" | "onPointerCancel", point: readonly [number, number]) => {
    canvasElement.props[handler]?.({ currentTarget: canvas, pointerId: 7,
      clientX: bounds.left + point[0], clientY: bounds.top + point[1] } as unknown as React.PointerEvent<HTMLCanvasElement>);
  };
  return { onLasso, onSelection, onCluster, drawnUnits, canvas, context, pointer };
}

describe("ProbeLayout lasso pointer integration", () => {
  it.each([1, 3])("maps an actual closed pointer path to physical unit IDs at depth zoom %s", (zoom) => {
    const probe = mountProbe(zoom);
    expect(probe.drawnUnits).toHaveLength(3);
    expect(probe.context.setTransform).toHaveBeenCalledWith(2, 0, 0, 2, 0, 0);
    expect(probe.canvas.height).toBe(377 * zoom * 2);
    // Follow the rendered dot locations, so drawing and hit testing must agree.
    const [first, middle, last] = probe.drawnUnits;
    const left = (first[0] + middle[0]) / 2, right = (middle[0] + last[0]) / 2;
    const bottom = (first[1] + middle[1]) / 2, top = (middle[1] + last[1]) / 2;
    const path: Array<[number, number]> = [[left, bottom], [right, bottom], [right, top], [left, top], [left, bottom]];
    probe.pointer("onPointerDown", path[0]);
    path.slice(1, -1).forEach((point) => probe.pointer("onPointerMove", point));
    probe.pointer("onPointerUp", path.at(-1)!);
    expect(probe.canvas.setPointerCapture).toHaveBeenCalledWith(7);
    expect(probe.onLasso).toHaveBeenCalledTimes(1);
    const vertices = probe.onLasso.mock.calls[0][0];
    expect(probeUnitsInPolygon(geometry, vertices)).toEqual([42]);
    const expected = [[22.5, 150], [50, 150], [50, 250], [22.5, 250], [22.5, 150]];
    vertices.forEach(([x, y], index) => { expect(x).toBeCloseTo(expected[index][0]); expect(y).toBeCloseTo(expected[index][1]); });
    expect(probe.onSelection).not.toHaveBeenCalled();
    expect(probe.onCluster).not.toHaveBeenCalled();
  });

  it("cancels an unfinished lasso without emitting a selection", () => {
    const probe = mountProbe(3);
    probe.pointer("onPointerDown", probe.drawnUnits[0]);
    probe.pointer("onPointerMove", probe.drawnUnits[1]);
    probe.pointer("onPointerCancel", probe.drawnUnits[1]);
    probe.pointer("onPointerUp", probe.drawnUnits[2]);
    expect(probe.onLasso).not.toHaveBeenCalled();
  });
});
