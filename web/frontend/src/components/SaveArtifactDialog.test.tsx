import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import SaveArtifactDialog from "./SaveArtifactDialog";

describe("displayed-data export destination", () => {
  it("shows the configured output root rather than a deployment example", () => {
    const html = renderToStaticMarkup(<SaveArtifactDialog
      title="Export Displayed" exportRoot="/mnt/reports/csv" value="unit.csv" extension=".csv"
      busy={false} error="" overwritePending={false}
      onChange={() => undefined} onClose={() => undefined} onSubmit={() => undefined}
    />);
    expect(html).toContain("/mnt/reports/csv");
    expect(html).not.toContain("/srv/rfmapping/exports");
  });
});
