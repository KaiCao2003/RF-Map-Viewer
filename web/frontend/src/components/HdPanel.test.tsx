import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import HdPanel from "./HdPanel";
import type { HdDatasetArtifact, HdViewSettings } from "../types";

const settings: HdViewSettings = {
  plotMode: "auto",
  displayBins: 30,
  smoothing: true,
  sigmaDeg: 18,
  compareScale: true,
};

const rates = Array.from({ length: 180 }, () => 4);
const artifact: HdDatasetArtifact = {
  available: true,
  sourcePath: "/recording/tuning_curves.tc",
  occupancyTimeS: null,
  metadata: null,
  units: [{ unitId: 7, rates, spikeCounts: null, hdClass: 3 }],
};

function renderPanel(blocked = false) {
  return renderToStaticMarkup(
    <HdPanel
      artifact={artifact}
      clusterId={7}
      loading={false}
      error=""
      rfPolarLayout={false}
      blocked={blocked}
      collapsed={false}
      settings={settings}
      onSettingsChange={() => undefined}
      onToggleCollapsed={() => undefined}
      onChoosePath={() => undefined}
    />,
  );
}

describe("HdPanel", () => {
  it("shows Class 3 and a rate-only curve with shared scaling", () => {
    const html = renderPanel();

    expect(html).toContain('class="hd-class-badge class-3"');
    expect(html).toContain("HD 3");
    expect(html).toContain('aria-label="Head-direction tuning curve shown as a line plot"');
    expect(html).toContain("Peak scale 4 Hz");
    expect(html).not.toContain("HD curve is invalid");
  });

  it("clears the curve and class badge when the Probe filter has no units", () => {
    const html = renderPanel(true);

    expect(html).toContain("No units in Probe region");
    expect(html).not.toContain("hd-class-badge");
    expect(html).not.toContain("<canvas");
  });
});
