import { describe, expect, it } from "vitest";
import { PairSession, changedPairFields } from "./pairedWindows";
import type { PairMessage } from "./pairedWindows";
import type { ViewState } from "./types";

const initial: ViewState = {
  clusterId: 42, valueMode: "Mean firing rate (Hz)", activeTimeCenterMs: 5,
  timelineStartMs: -100, timelineEndMs: 200, timelineAnchorMs: null,
  rfStartMs: 80, rfEndMs: 160, rfWindowMode: "difference", rfBStartMs: 0, rfBEndMs: 80,
  timeResolutionMs: 10, xBins: 3, yBins: 2, smoothRadius: 0, flipY: false,
  palette: "Gray", polarRadius: "Display bottom inner", polarLayout: false, rgbMode: false,
  selectedCellYMidpoint: null, selectedCellXMidpoint: null, timelineScrollFraction: 0, selectedTab: "rf",
};

function fixture(id = "local", units = [42, 99]) {
  const messages: PairMessage[] = [];
  const applied: Partial<ViewState>[] = [];
  const membership: Array<{ ids: number[]; peers: number }> = [];
  let time = 0;
  const session = new PairSession(id, initial, units, (message) => messages.push(message),
    (patch) => applied.push(patch), (ids, peers) => membership.push({ ids, peers }), () => time);
  return { session, messages, applied, membership, advance: (ms: number) => { time += ms; } };
}

describe("paired browser windows", () => {
  it("synchronizes both RF windows together without overwriting unrelated controls", () => {
    const patch = changedPairFields(initial, { ...initial, rfBEndMs: 120 });
    expect(patch).toMatchObject({ rfStartMs: 80, rfEndMs: 160, rfWindowMode: "difference", rfBStartMs: 0, rfBEndMs: 120 });
    expect(patch).not.toHaveProperty("palette");
    expect(patch).not.toHaveProperty("timelineStartMs");
  });

  it("joins with recorded unit IDs and a sorted union, including units absent locally", () => {
    const a = fixture(); const b = fixture("peer", [71, 42]);
    b.session.join(); a.session.receive(b.messages[0]);
    expect(a.membership.at(-1)).toEqual({ ids: [42, 71, 99], peers: 1 });
    a.session.receive({ version: 1, sender: "peer", revision: 2, kind: "state", units: [71, 42], patch: { clusterId: 71 } });
    expect(a.applied.at(-1)).toEqual({ clusterId: 71 });
    expect(a.messages[0].kind).toBe("presence");
  });

  it("does not echo receiver axis clamping or replay stale state", () => {
    const a = fixture();
    const remote: PairMessage = { version: 1, sender: "peer", revision: 4, kind: "state", units: [42], patch: { xBins: 20, rfEndMs: 300 } };
    a.session.receive(remote);
    a.session.updateState({ ...initial, xBins: 3, rfEndMs: 200 });
    expect(a.messages).toHaveLength(0);
    a.session.receive({ ...remote, revision: 3 });
    expect(a.applied).toHaveLength(1);
    a.session.updateState({ ...initial, xBins: 3, rfEndMs: 200, palette: "Viridis" });
    expect(a.messages.at(-1)?.patch).toEqual({ palette: "Viridis" });
  });

  it("updates the navigable union when peers filter, leave, or disappear", () => {
    const a = fixture();
    a.session.receive({ version: 1, sender: "peer", revision: 1, kind: "presence", units: [71] });
    a.session.updateUnits([42]);
    expect(a.membership.at(-1)?.ids).toEqual([42, 71]);
    a.session.receive({ version: 1, sender: "peer", revision: 2, kind: "leave", units: [] });
    expect(a.membership.at(-1)).toEqual({ ids: [42], peers: 0 });
    a.session.receive({ version: 1, sender: "gone", revision: 1, kind: "presence", units: [101] });
    a.advance(60_001); a.session.heartbeat();
    expect(a.membership.at(-1)).toEqual({ ids: [42], peers: 0 });
  });

  it("shares only view fields, never a dataset identity or source path", () => {
    const a = fixture();
    a.session.receive({ version: 1, sender: "peer", revision: 1, kind: "state", units: [42],
      patch: { palette: "Inferno", sourcePath: "/private/input" } as Partial<ViewState> });
    expect(a.applied).toEqual([{ palette: "Inferno" }]);
    a.session.join();
    expect(JSON.stringify(a.messages)).not.toContain("sourcePath");
  });
});
