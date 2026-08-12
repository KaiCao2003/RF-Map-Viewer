import { describe, expect, it } from "vitest";
import { responseRangeForPalette, rgbComposite } from "./palette";

describe("RF palette response range", () => {
  it("preserves finite min/max for Gray", () => {
    expect(responseRangeForPalette(4, 11, "Gray")).toEqual([4, 11]);
  });

  it("uses a physical zero baseline for Viridis and Inferno", () => {
    expect(responseRangeForPalette(4, 11, "Viridis")).toEqual([0, 11]);
    expect(responseRangeForPalette(4, 11, "Inferno")).toEqual([0, 11]);
  });

  it("distinguishes missing RGB cells from valid zero and preserves the real response scale", () => {
    expect(rgbComposite(null, null, null, 2, -100, 200)).toBe("#e6e8eb");
    expect(rgbComposite(0, null, 0, 2, -100, 200)).toBe("#000000");
    expect(rgbComposite(2, -100, 0, 2, -100, 200)).toBe("rgb(255, 0, 0)");
    expect(rgbComposite(0.5, -100, 0, 1, -100, 200)).toBe("rgb(128, 0, 0)");
  });
});
