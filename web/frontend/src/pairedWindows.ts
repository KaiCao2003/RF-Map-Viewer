import { useEffect, useRef, useState } from "react";
import type { DatasetMeta, ViewState } from "./types";

type StateKey = keyof ViewState;
const FIELD_GROUPS: StateKey[][] = [
  ["clusterId"], ["valueMode"], ["activeTimeCenterMs"],
  ["timelineStartMs", "timelineEndMs", "timelineAnchorMs"],
  ["rfStartMs", "rfEndMs", "rfWindowMode", "rfBStartMs", "rfBEndMs",
    "rfSumStartMs", "rfSumEndMs", "rfAStartMs", "rfAEndMs"],
  ["timeResolutionMs"], ["xBins"], ["yBins"], ["smoothRadius"],
  ["flipY"], ["palette"], ["polarRadius"], ["polarLayout"], ["rgbMode"],
  ["selectedCellYMidpoint", "selectedCellXMidpoint"],
  ["timelineScrollFraction"], ["selectedTab"],
];

export function changedPairFields(before: ViewState, after: ViewState): Partial<ViewState> {
  const entries = FIELD_GROUPS.filter((group) => group.some((key) => before[key] !== after[key]))
    .flatMap((group) => group.map((key) => [key, after[key]]));
  return Object.fromEntries(entries);
}

export interface PairMessage {
  version: 1;
  sender: string;
  revision: number;
  kind: "join" | "presence" | "state" | "leave";
  units: number[];
  patch?: Partial<ViewState>;
}

interface Peer { revision: number; seen: number; units: number[] }

/** All opted-in tabs navigate recorded IDs, including IDs absent in this file.
 * Only view coordinates/settings cross the channel; sources and counts stay local.
 */
export class PairSession {
  private revision = 0;
  private peers = new Map<string, Peer>();
  private applyingRemote = false;

  constructor(
    private readonly id: string,
    private state: ViewState,
    private units: number[],
    private readonly send: (message: PairMessage) => void,
    private readonly apply: (patch: Partial<ViewState>) => void,
    private readonly membership: (unitIDs: number[], peerCount: number) => void,
    private readonly now: () => number = Date.now,
  ) {}

  private emit(kind: PairMessage["kind"], patch?: Partial<ViewState>) {
    this.send({ version: 1, sender: this.id, revision: ++this.revision, kind, units: this.units, patch });
  }

  join() {
    this.emit("join", Object.fromEntries(FIELD_GROUPS.flat().map((key) => [key, this.state[key]])));
    this.reportMembership();
  }

  leave() { this.emit("leave"); }

  updateState(next: ViewState) {
    const patch = changedPairFields(this.state, next);
    this.state = next;
    // A receiver may clamp shared coordinates to its own axes. Do not echo
    // those clamps into the source window or create a synchronization loop.
    if (this.applyingRemote) { this.applyingRemote = false; return; }
    if (Object.keys(patch).length) this.emit("state", patch);
  }

  updateUnits(units: number[]) {
    this.units = units;
    this.emit("presence");
    this.reportMembership();
  }

  heartbeat() {
    // Background tabs can throttle timers to once a minute. Allow missed
    // heartbeats before removing a tab that did not send an explicit leave.
    const expired = [...this.peers].filter(([, peer]) => this.now() - peer.seen > 180_000);
    expired.forEach(([id]) => this.peers.delete(id));
    if (expired.length) this.reportMembership();
    this.emit("presence");
  }

  receive(message: PairMessage) {
    if (message?.version !== 1 || typeof message.sender !== "string" || message.sender === this.id
      || !Number.isSafeInteger(message.revision) || !Array.isArray(message.units)
      || !message.units.every(Number.isSafeInteger)) return;
    const previous = this.peers.get(message.sender);
    if (previous && message.revision <= previous.revision) return;
    if (message.kind === "leave") {
      this.peers.delete(message.sender);
      this.reportMembership();
      return;
    }
    if (!["join", "presence", "state"].includes(message.kind)) return;
    this.peers.set(message.sender, { revision: message.revision, seen: this.now(), units: message.units });
    if (!previous || previous.units.join(",") !== message.units.join(",")) this.reportMembership();
    if (message.kind === "join") this.emit("presence");
    if ((message.kind === "join" || message.kind === "state") && message.patch) {
      const patch = Object.fromEntries(FIELD_GROUPS.flat()
        .filter((key) => Object.prototype.hasOwnProperty.call(message.patch, key))
        .map((key) => [key, message.patch![key]])) as Partial<ViewState>;
      if (Object.keys(patch).length) {
        this.applyingRemote = true;
        this.apply(patch);
      }
    }
  }

  private reportMembership() {
    const union = [...new Set([...this.units, ...[...this.peers.values()].flatMap((peer) => peer.units)])]
      .sort((a, b) => a - b);
    this.membership(union, this.peers.size);
  }
}

export function usePairedWindows(
  meta: DatasetMeta | null,
  state: ViewState | null,
  unitIDs: number[],
  onPatch: (patch: Partial<ViewState>) => void,
) {
  const supported = typeof BroadcastChannel !== "undefined";
  const [enabled, setEnabled] = useState(false);
  const [members, setMembers] = useState({ unitIDs: [] as number[], peerCount: 0 });
  const session = useRef<PairSession | null>(null);
  const current = useRef({ state, unitIDs, onPatch });
  current.current = { state, unitIDs, onPatch };
  const unitKey = unitIDs.join(",");

  useEffect(() => {
    if (!enabled || !supported || !meta?.id || !current.current.state) return;
    const channel = new BroadcastChannel("rfmapping-pair-windows-v1");
    const active = new PairSession(crypto.randomUUID(), current.current.state, current.current.unitIDs,
      (message) => channel.postMessage(message),
      (patch) => current.current.onPatch(patch),
      (ids, peerCount) => setMembers({ unitIDs: ids, peerCount }));
    session.current = active;
    channel.onmessage = (event: MessageEvent<PairMessage>) => active.receive(event.data);
    const timer = window.setInterval(() => active.heartbeat(), 15_000);
    const leave = () => active.leave();
    window.addEventListener("pagehide", leave);
    active.join();
    return () => {
      active.leave();
      window.removeEventListener("pagehide", leave);
      window.clearInterval(timer);
      channel.close();
      session.current = null;
    };
  }, [enabled, supported, meta?.id]);

  useEffect(() => { if (state) session.current?.updateState(state); }, [state]);
  useEffect(() => { session.current?.updateUnits(current.current.unitIDs); }, [unitKey]);

  return { enabled, setEnabled, supported, peerCount: enabled ? members.peerCount : 0,
    unitIDs: enabled ? members.unitIDs : null };
}
