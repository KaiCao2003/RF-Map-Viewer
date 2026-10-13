import { describe, expect, it } from "vitest";
import { VIEWER_TABS } from "./viewTabs";

describe("viewer notebook tabs", () => {
  it("exposes the four canonical viewer tabs", () => {
    expect(VIEWER_TABS).toEqual([
      { key: "rf", label: "RF", short: "1" },
      { key: "delay", label: "Delay / RGB", short: "2" },
      { key: "timeline", label: "Timeline", short: "3" },
      { key: "results", label: "RF Results", short: "4" },
    ]);
  });
});
