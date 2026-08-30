import { describe, expect, it } from "vitest";
import {
  navigationUnitIds,
  orderedQualityVisibleUnitIds,
  reconciledClusterId,
  userEnteredZeroSpikeSpatialBinThreshold,
} from "./unitFilter";

describe("zero-spike spatial-bin unit filtering", () => {
  it("preserves dataset order and intersects the Probe region", () => {
    const quality = orderedQualityVisibleUnitIds([41, 7, 88, 3], [88, 41], true);
    expect(quality).toEqual([41, 88]);
    expect(navigationUnitIds(quality, [3, 88])).toEqual([88]);
    expect(orderedQualityVisibleUnitIds([41, 7], [], false)).toEqual([41, 7]);
  });

  it("keeps a visible selection, falls back to the first, and reports empty", () => {
    expect(reconciledClusterId(88, [41, 88])).toBe(88);
    expect(reconciledClusterId(7, [41, 88])).toBe(41);
    expect(reconciledClusterId(7, [])).toBeNull();
  });

  it("clamps new user input to the current native spatial-bin count", () => {
    expect(userEnteredZeroSpikeSpatialBinThreshold(5, 4)).toBe(4);
    expect(userEnteredZeroSpikeSpatialBinThreshold(90_000, 200_000)).toBe(90_000);
    expect(userEnteredZeroSpikeSpatialBinThreshold(100_001, 200_000)).toBe(100_000);
    expect(userEnteredZeroSpikeSpatialBinThreshold(0, 4)).toBeNull();
    expect(userEnteredZeroSpikeSpatialBinThreshold(1.5, 4)).toBeNull();
  });
});
