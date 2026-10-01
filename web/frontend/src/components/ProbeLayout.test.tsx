import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import ProbeLayout, { nearestProbeUnit } from "./ProbeLayout";
import type { ProbeGeometry } from "../types";

const geometry: ProbeGeometry = {
  probe: "ProbeA",
  channels: [{ channelId: 0, x: 12, y: 100, shank: 0 }],
  units: [
    { unitId: 11, x: 10, y: 90 },
    { unitId: 22, x: null, y: null },
  ],
};

function render(currentClusterId: number): string {
  return renderToStaticMarkup(
    <ProbeLayout
      geometry={geometry}
      availableUnitIds={[11, 22]}
      currentClusterId={currentClusterId}
      selection={null}
      onSelection={() => undefined}
      onCluster={() => undefined}
    />,
  );
}

describe("ProbeLayout", () => {
  it("keeps the probe canvas and labels a selected missing position as NaN", () => {
    const html = render(22);
    expect(html).toContain("ProbeA channel and unit layout");
    expect(html).toContain("Cluster 22 position");
    expect(html).toContain("NaN");
  });

  it("does not show the NaN label for a positioned unit", () => {
    expect(render(11)).not.toContain("probe-position-missing");
  });

  it("selects the nearest unit even when the click is outside the hover radius", () => {
    const transform = {
      xMin: 0, xMax: 100, yMin: 0, yMax: 100,
      left: 0, top: 0, width: 100, height: 100,
    };
    const units = [{ unitId: 11, x: 10, y: 90 }, { unitId: 33, x: 80, y: 20 }];
    expect(nearestProbeUnit(units, transform, 100, 100)).toBe(33);
    expect(nearestProbeUnit(units, transform, 100, 100, 11)).toBeNull();
    expect(nearestProbeUnit(units, transform, 9, 11, 11)).toBe(11);
    expect(nearestProbeUnit([], transform, 100, 100)).toBeNull();
  });

  it("describes nearest-unit clicks and dragged region filters", () => {
    const html = render(11);
    expect(html).toContain("Click to select the nearest unit; drag to filter a region.");
    expect(html).not.toContain("160 × 75");
  });
});
