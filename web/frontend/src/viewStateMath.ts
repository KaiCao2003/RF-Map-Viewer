import {
  groupIndexForSource,
  snappedResolutionMs,
  timeBounds,
  timeGroupForMs,
  timeGroupRangeForMs,
  timeGroups,
} from "./math";
import type { AxisGroup, DatasetMeta, ViewState } from "./types";

export function resolutionChangePatch(
  meta: DatasetMeta,
  current: ViewState,
  requestedResolutionMs: number,
): Partial<ViewState> {
  const previousGroups = timeGroups(meta, current.timeResolutionMs);
  const previousSelection = timeGroupRangeForMs(
    meta,
    previousGroups,
    current.timelineStartMs,
    current.timelineEndMs,
  );
  const sourceStart = previousGroups[previousSelection[0]][0];
  const sourceEnd = previousGroups[previousSelection[1]][1];
  const activeGroup = previousGroups[timeGroupForMs(meta, previousGroups, current.activeTimeCenterMs)];
  const activeSourceBin = Math.floor((activeGroup[0] + activeGroup[1]) / 2);
  const resolution = snappedResolutionMs(meta, requestedResolutionMs);
  const nextGroups = timeGroups(meta, resolution);
  const wasFull = previousSelection[0] === 0 && previousSelection[1] === previousGroups.length - 1;
  const nextStart = wasFull ? 0 : groupIndexForSource(nextGroups, sourceStart);
  const nextEnd = wasFull ? nextGroups.length - 1 : groupIndexForSource(nextGroups, sourceEnd);
  const nextActive = groupIndexForSource(nextGroups, activeSourceBin);
  const [selectionStart] = timeBounds(meta, nextGroups[nextStart]);
  const [, selectionEnd] = timeBounds(meta, nextGroups[nextEnd]);
  const activeBounds = timeBounds(meta, nextGroups[nextActive]);
  return {
    timeResolutionMs: resolution,
    activeTimeCenterMs: (activeBounds[0] + activeBounds[1]) / 2,
    timelineStartMs: selectionStart,
    timelineEndMs: selectionEnd,
    timelineAnchorMs: null,
  };
}

export function timelineSelectionPatch(
  meta: DatasetMeta,
  current: ViewState,
  groups: AxisGroup[],
  binIndex: number,
  extend: boolean,
): Partial<ViewState> {
  const bounds = timeBounds(meta, groups[binIndex]);
  const center = (bounds[0] + bounds[1]) / 2;
  if (!extend) {
    return {
      activeTimeCenterMs: center,
      timelineStartMs: bounds[0],
      timelineEndMs: bounds[1],
      timelineAnchorMs: center,
    };
  }
  const anchorIndex = current.timelineAnchorMs == null
    ? Math.max(0, groups.findIndex((group) => Math.abs(timeBounds(meta, group)[0] - current.timelineStartMs) < 1e-7))
    : timeGroupForMs(meta, groups, current.timelineAnchorMs);
  const low = Math.min(anchorIndex, binIndex);
  const high = Math.max(anchorIndex, binIndex);
  return {
    activeTimeCenterMs: center,
    timelineStartMs: timeBounds(meta, groups[low])[0],
    timelineEndMs: timeBounds(meta, groups[high])[1],
    timelineAnchorMs: center,
  };
}
