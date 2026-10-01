import { describe, expect, it } from "vitest";
import { PairSession, changedPairFields } from "./pairedWindows";
import type { PairMessage, PairViewState } from "./pairedWindows";
import type { ViewState } from "./types";

const initial: ViewState = {
  clusterId: 42, valueMode: "Mean firing rate (Hz)", activeTimeCenterMs: 5,
  timelineStartMs: -100, timelineEndMs: 200, timelineAnchorMs: null,
  rfStartMs: 80, rfEndMs: 160, rfWindowMode: "difference", rfBStartMs: 0, rfBEndMs: 80,
  timeResolutionMs: 10, xBins: 3, yBins: 2, smoothRadius: 0, flipY: false,
  palette: "Gray", polarRadius: "Display bottom inner", polarLayout: false, rgbMode: false,
  selectedCellYMidpoint: null, selectedCellXMidpoint: null, timelineScrollFraction: 0, selectedTab: "rf",
};

function fixture(id = "local", units = [42, 99], state: PairViewState = initial) {
  const messages: PairMessage[] = [];
  const applied: Partial<PairViewState>[] = [];
  const membership: Array<{ ids: number[]; peers: number }> = [];
  let time = 0;
  const session = new PairSession(id, state, units, (message) => messages.push(message),
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

  it("compares HD controls by value and synchronizes optional views together", () => {
    const state: PairViewState = {
      ...initial,
      hdSettings: { plotMode: "auto", displayBins: 30, smoothing: true, sigmaDeg: 18, compareScale: false },
      showHd: true, showProbe: true, showWaveform: true,
      waveformChannelMode: "same_x_column", hdLayout: "side-by-side",
    };
    expect(changedPairFields(state, { ...state, hdSettings: { ...state.hdSettings! } })).toEqual({});
    expect(changedPairFields(state, { ...state, showProbe: false })).toEqual({
      showHd: true, showProbe: false, showWaveform: true,
    });
    const hdSettings = { ...state.hdSettings!, displayBins: 60, compareScale: true };
    expect(changedPairFields(state, { ...state, hdSettings })).toEqual({ hdSettings });
    expect(changedPairFields(state, { ...state, waveformChannelMode: "same_shank", hdLayout: "stacked" }))
      .toEqual({ waveformChannelMode: "same_shank", hdLayout: "stacked" });
  });

  it("receives companion controls and does not echo them after applying", () => {
    const a = fixture();
    const patch = {
      hdSettings: { plotMode: "polar" as const, displayBins: 60, smoothing: false, sigmaDeg: 12, compareScale: true },
      showHd: false, showProbe: true, showWaveform: false,
      waveformChannelMode: "same_shank" as const, hdLayout: "stacked" as const,
      tuningSession: 2, unitFilterEnabled: false, zeroSpikeSpatialBinThreshold: 3,
      autoLoadHd: false, autoLoadProbe: true,
    };
    a.session.receive({ version: 1, sender: "peer", revision: 1, kind: "state", units: [42], patch });
    expect(a.applied).toEqual([patch]);
    const updated = { ...initial, ...patch };
    a.session.updateState(updated);
    expect(a.messages).toHaveLength(0);
    a.session.updateState({ ...updated, hdSettings: { ...patch.hdSettings, sigmaDeg: 18 } });
    expect(a.messages.at(-1)?.patch).toEqual({ hdSettings: { ...patch.hdSettings, sigmaDeg: 18 } });
  });

  it("synchronizes tuning source, native-bin filtering, and automatic loading preferences", () => {
    const state: PairViewState = {
      ...initial, tuningSession: 1, unitFilterEnabled: true, zeroSpikeSpatialBinThreshold: 1,
      autoLoadHd: true, autoLoadProbe: true,
    };
    expect(changedPairFields(state, { ...state, tuningSession: 3 })).toEqual({ tuningSession: 3 });
    expect(changedPairFields(state, { ...state, zeroSpikeSpatialBinThreshold: 5 })).toEqual({
      unitFilterEnabled: true, zeroSpikeSpatialBinThreshold: 5,
    });
    expect(changedPairFields(state, { ...state, unitFilterEnabled: false })).toEqual({
      unitFilterEnabled: false, zeroSpikeSpatialBinThreshold: 1,
    });
    expect(changedPairFields(state, { ...state, autoLoadHd: false })).toEqual({
      autoLoadHd: false, autoLoadProbe: true,
    });
    const a = fixture("local", [42], state);
    a.session.join();
    expect(a.messages[0].patch).toMatchObject({
      tuningSession: 1, unitFilterEnabled: true, zeroSpikeSpatialBinThreshold: 1,
      autoLoadHd: true, autoLoadProbe: true,
    });
  });

  it("joins with companion settings only when supplied by the viewer", () => {
    const oldViewer = fixture();
    oldViewer.session.join();
    expect(oldViewer.messages[0].patch).not.toHaveProperty("hdSettings");
    expect(oldViewer.messages[0].patch).not.toHaveProperty("showHd");
    expect(oldViewer.messages[0].patch).not.toHaveProperty("tuningSession");
    expect(oldViewer.messages[0].patch).not.toHaveProperty("unitFilterEnabled");
    const state: PairViewState = {
      ...initial,
      hdSettings: { plotMode: "line", displayBins: 30, smoothing: true, sigmaDeg: 18, compareScale: false },
      showHd: true, showProbe: false, showWaveform: true, hdLayout: "stacked",
    };
    const viewer = fixture("viewer", [42], state);
    viewer.session.join();
    expect(viewer.messages[0].patch).toMatchObject({ hdSettings: state.hdSettings, showProbe: false, hdLayout: "stacked" });
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
    a.advance(90_000); a.session.heartbeat();
    expect(a.membership.at(-1)?.ids).toEqual([42, 101]);
    a.advance(90_001); a.session.heartbeat();
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
