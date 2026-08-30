export function orderedQualityVisibleUnitIds(
  unitPool: ReadonlyArray<number>,
  reportedVisibleUnitIds: ReadonlyArray<number>,
  enabled: boolean,
): number[] {
  if (!enabled) return [...unitPool];
  const reported = new Set(reportedVisibleUnitIds);
  return unitPool.filter((unitId) => reported.has(unitId));
}

export function navigationUnitIds(
  qualityVisibleUnitIds: ReadonlyArray<number>,
  probeFilteredUnitIds: ReadonlyArray<number> | null,
): number[] {
  if (probeFilteredUnitIds == null) return [...qualityVisibleUnitIds];
  const probe = new Set(probeFilteredUnitIds);
  return qualityVisibleUnitIds.filter((unitId) => probe.has(unitId));
}

export function reconciledClusterId(
  currentClusterId: number,
  visibleUnitIds: ReadonlyArray<number>,
): number | null {
  return visibleUnitIds.includes(currentClusterId)
    ? currentClusterId
    : visibleUnitIds[0] ?? null;
}

export function userEnteredZeroSpikeSpatialBinThreshold(
  value: number,
  spatialBinCount: number,
): number | null {
  if (!Number.isInteger(value) || value < 1) return null;
  const dynamicMaximum = Math.max(
    1,
    Math.min(100_000, Math.floor(spatialBinCount)),
  );
  return Math.min(value, dynamicMaximum);
}
