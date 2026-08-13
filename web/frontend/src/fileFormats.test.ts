import { describe, expect, it } from "vitest";
import { acceptsRemoteFile, hasArtifactExtension } from "./fileFormats";

describe("input file aliases", () => {
  it("accepts current and legacy RF mapping names", () => {
    expect(acceptsRemoteFile("/data/session.rfmap", "rf-json")).toBe(true);
    expect(acceptsRemoteFile("/data/session.JSON", "rf-json")).toBe(true);
    expect(acceptsRemoteFile("/data/tuning_curves.json", "rf-json")).toBe(false);
    expect(acceptsRemoteFile("/data/session.tc", "rf-json")).toBe(false);
  });

  it("accepts .tc and .probe aliases with their exact legacy names", () => {
    expect(acceptsRemoteFile("/data/custom.tc", "tuning-json")).toBe(true);
    expect(acceptsRemoteFile("/data/tuning_curves.json", "tuning-json")).toBe(true);
    expect(acceptsRemoteFile("/data/other.json", "tuning-json")).toBe(false);
    expect(acceptsRemoteFile("/data/custom.probe", "positions-csv")).toBe(true);
    expect(acceptsRemoteFile("/data/positions.csv", "positions-csv")).toBe(true);
    expect(acceptsRemoteFile("/data/channels.csv", "positions-csv")).toBe(false);
  });

  it("recognizes artifact suffixes entered into the path field", () => {
    for (const path of ["a.rfmap", "a.json", "a.tc", "a.probe", "a.csv"]) {
      expect(hasArtifactExtension(path)).toBe(true);
    }
    expect(hasArtifactExtension("/data/folder")).toBe(false);
  });
});
