import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import ProbeLayout from "./ProbeLayout";
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
});
