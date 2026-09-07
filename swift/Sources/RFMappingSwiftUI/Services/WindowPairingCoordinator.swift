import Foundation
import Observation

enum WindowPairingEligibility: Equatable, Sendable {
    case noSecondWindow(loadedWindowCount: Int)
    case matching(loadedWindowCount: Int)
    case mismatch(loadedWindowCount: Int)

    var canEnable: Bool {
        if case .matching = self { return true }
        return false
    }

    var loadedWindowCount: Int {
        switch self {
        case .noSecondWindow(let count), .matching(let count), .mismatch(let count):
            count
        }
    }
}

/// Coordinates viewer state without retaining window-owned stores. All entry
/// points are main-actor isolated because SwiftUI windows and their stores are
/// main-thread state.
@MainActor
@Observable
final class WindowPairingCoordinator {
    static let shared = WindowPairingCoordinator()

    private final class WeakStore {
        weak var value: RFMappingStore?

        init(_ value: RFMappingStore) {
            self.value = value
        }
    }

    @ObservationIgnored private var stores: [UUID: WeakStore] = [:]
    @ObservationIgnored private var canonicalState: ViewerSyncState?
    @ObservationIgnored private var expectedAppliedStates: [UUID: ViewerSyncState] = [:]
    @ObservationIgnored private var applyingStateToStoreIDs: Set<UUID> = []
    @ObservationIgnored private var cachedEligibility: WindowPairingEligibility =
        .noSecondWindow(loadedWindowCount: 0)

    private(set) var isPairingEnabled = false
    private(set) var statusRevision = 0

    var eligibility: WindowPairingEligibility {
        _ = statusRevision
        return cachedEligibility
    }

    var loadedWindowCount: Int {
        _ = statusRevision
        return cachedEligibility.loadedWindowCount
    }

    func register(_ store: RFMappingStore, id: UUID) {
        removeDeallocatedStores()
        let isNewRegistration = stores[id]?.value !== store
        stores[id] = WeakStore(store)
        store.pairingDataDidChange = { [weak self, weak store] in
            guard let self, let store else { return }
            self.dataDidChange(in: store, id: id)
        }
        store.unitQualityVisibilityDidChange = { [weak self, weak store] reason in
            guard let self, let store else { return }
            self.unitQualityVisibilityDidChange(in: store, id: id, reason: reason)
        }

        guard isNewRegistration else { return }
        refreshEligibility()
        statusRevision &+= 1
        reevaluatePairing(afterDataChangeIn: store, id: id)
    }

    func unregister(id: UUID) {
        if let store = stores.removeValue(forKey: id)?.value {
            store.pairingDataDidChange = nil
            store.unitQualityVisibilityDidChange = nil
        }
        expectedAppliedStates[id] = nil
        refreshEligibility()
        statusRevision &+= 1
        validateActivePairing()
    }

    func setPairingEnabled(_ enabled: Bool, sourceID: UUID) {
        removeDeallocatedStores()
        guard enabled else {
            disablePairing()
            return
        }
        guard cachedEligibility.canEnable,
              let source = stores[sourceID]?.value,
              source.data != nil else { return }

        isPairingEnabled = true
        updatePairedUnitUnion()
        let state = source.viewerSyncState
        canonicalState = state
        expectedAppliedStates.removeAll(keepingCapacity: true)
        expectedAppliedStates[sourceID] = state
        broadcast(state, fields: .all, excluding: sourceID)
        reconcilePairedQualityUnionAndSelection()
        statusRevision &+= 1
    }

    /// Called by a window's `onChange` observer after SwiftUI has coalesced a
    /// user interaction and any normalization it triggered.
    func synchronizedStateDidChange(_ state: ViewerSyncState, from sourceID: UUID) {
        let baseline = expectedAppliedStates[sourceID]
        if let baseline, baseline.matchesAppliedState(state) {
            // Retain the expectation until a genuine user change. AppKit can
            // report the restored scroll position once more after SwiftUI has
            // observed the applied value; keeping this guard prevents a
            // fractional-pixel scroll echo between differently sized windows.
            expectedAppliedStates[sourceID] = state
            return
        }
        guard isPairingEnabled,
              stores[sourceID]?.value?.data != nil else { return }
        guard cachedEligibility.canEnable else {
            disablePairing()
            return
        }

        let changedFields = baseline.map(state.changedFields) ?? .all
        guard !changedFields.isEmpty else { return }
        expectedAppliedStates[sourceID] = state
        canonicalState = canonicalState?.merging(state, fields: changedFields) ?? state
        broadcast(state, fields: changedFields, excluding: sourceID)
        if changedFields.contains(.plotRange) {
            // Every target now evaluates the same physical RF window on its
            // own source bins. Rebuild the union only after those final local
            // quality pools exist, rather than mixing new source state with
            // stale target ranges.
            reconcilePairedQualityUnionAndSelection()
        }
    }

    func statusText() -> String {
        switch eligibility {
        case .noSecondWindow:
            return "Open another loaded viewer window to enable sync."
        case .matching(let count):
            if isPairingEnabled {
                return "\(count) windows paired across \(sortedUnitIDUnion().count) unit IDs. Missing IDs display N/A."
            }
            return "\(count) loaded windows are ready to pair by unit-ID union."
        case .mismatch:
            return "Sync unavailable: a loaded window has no units."
        }
    }

    private func dataDidChange(in store: RFMappingStore, id: UUID) {
        guard stores[id]?.value === store else { return }
        expectedAppliedStates[id] = nil
        refreshEligibility()
        statusRevision &+= 1
        reevaluatePairing(afterDataChangeIn: store, id: id)
    }

    private func unitQualityVisibilityDidChange(
        in store: RFMappingStore,
        id: UUID,
        reason: UnitQualityVisibilityChangeReason
    ) {
        guard stores[id]?.value === store else { return }
        guard !applyingStateToStoreIDs.contains(id) else { return }
        statusRevision &+= 1
        guard isPairingEnabled else { return }
        switch reason {
        case .filterSettings:
            // Filter settings are window-local and are not part of
            // ViewerSyncState, so their union changes must take effect now.
            reconcilePairedQualityUnionAndSelection()
        case .plotRange:
            // The source's synchronized-state observer will broadcast the new
            // range to every target, then perform one final union rebuild.
            break
        }
    }

    private func reevaluatePairing(afterDataChangeIn store: RFMappingStore, id: UUID) {
        guard isPairingEnabled else { return }
        guard cachedEligibility.canEnable else {
            disablePairing()
            return
        }
        guard store.data != nil, var canonicalState else { return }
        updatePairedUnitUnion()
        let union = sortedUnitIDUnion()
        if !union.contains(canonicalState.unitID), let replacement = union.first {
            let replacementState = canonicalState.replacingUnitID(replacement)
            canonicalState = replacementState
            self.canonicalState = replacementState
        }
        for (targetID, targetStore) in loadedStores() {
            apply(canonicalState, to: targetStore, id: targetID)
        }
        reconcilePairedQualityUnionAndSelection()
    }

    private func validateActivePairing() {
        guard isPairingEnabled else { return }
        if !cachedEligibility.canEnable {
            disablePairing()
        } else {
            reconcilePairedQualityUnionAndSelection()
        }
    }

    private func disablePairing() {
        guard isPairingEnabled || canonicalState != nil || !expectedAppliedStates.isEmpty else {
            return
        }
        isPairingEnabled = false
        canonicalState = nil
        expectedAppliedStates.removeAll(keepingCapacity: true)
        loadedStores().forEach { $0.value.setPairedUnitIDs(nil) }
        statusRevision &+= 1
    }

    private func broadcast(
        _ state: ViewerSyncState,
        fields: ViewerSyncFields,
        excluding sourceID: UUID
    ) {
        for (id, store) in loadedStores() where id != sourceID {
            if fields == .timelineScroll {
                store.applyTimelineScrollFraction(state.timelineScrollFraction)
                expectedAppliedStates[id] = store.viewerSyncState
            } else {
                apply(state, fields: fields, to: store, id: id)
            }
        }
    }

    private func apply(
        _ state: ViewerSyncState,
        fields: ViewerSyncFields = .all,
        to store: RFMappingStore,
        id: UUID
    ) {
        applyingStateToStoreIDs.insert(id)
        defer { applyingStateToStoreIDs.remove(id) }
        store.applyViewerSyncState(state, fields: fields)
        expectedAppliedStates[id] = store.viewerSyncState
    }

    private func currentEligibility() -> WindowPairingEligibility {
        let loaded = loadedStores()
        guard loaded.count >= 2 else {
            return .noSecondWindow(loadedWindowCount: loaded.count)
        }
        let allHaveUnits = loaded.allSatisfy { !($0.value.data?.unitPool.isEmpty ?? true) }
        return allHaveUnits
            ? .matching(loadedWindowCount: loaded.count)
            : .mismatch(loadedWindowCount: loaded.count)
    }

    private func sortedUnitIDUnion() -> [Int] {
        Array(Set(loadedStores().flatMap { $0.value.qualityFilteredUnitIDs })).sorted()
    }

    private func updatePairedUnitUnion() {
        guard isPairingEnabled else { return }
        let union = sortedUnitIDUnion()
        loadedStores().forEach { $0.value.setPairedUnitIDs(union) }
    }

    /// Rebuilds the shared union from complete local quality pools and then
    /// resolves one canonical shared selection. Callers that apply plot-range
    /// state must invoke this only after every target has finished applying it.
    private func reconcilePairedQualityUnionAndSelection() {
        guard isPairingEnabled else { return }
        updatePairedUnitUnion()
        let loaded = loadedStores()
        let union = sortedUnitIDUnion()
        guard let canonicalState, let first = union.first else {
            // `setPairedUnitIDs([])` has already moved every viewer into its
            // nonfatal empty state. Record that applied state to prevent a
            // synthetic fallback unit ID from echoing as a user selection.
            for (id, store) in loaded {
                expectedAppliedStates[id] = store.viewerSyncState
            }
            return
        }
        let targetUnitID = union.contains(canonicalState.unitID)
            ? canonicalState.unitID
            : first
        let replacementState = canonicalState.replacingUnitID(targetUnitID)
        self.canonicalState = replacementState
        for (id, store) in loaded {
            apply(replacementState, fields: .unit, to: store, id: id)
        }
    }

    private func refreshEligibility() {
        cachedEligibility = currentEligibility()
    }

    private func loadedStores() -> [(key: UUID, value: RFMappingStore)] {
        stores.compactMap { id, box in
            guard let store = box.value, store.data != nil else { return nil }
            return (key: id, value: store)
        }
    }

    private func removeDeallocatedStores() {
        let deadIDs = stores.compactMap { id, box in box.value == nil ? id : nil }
        guard !deadIDs.isEmpty else { return }
        deadIDs.forEach {
            stores[$0] = nil
            expectedAppliedStates[$0] = nil
        }
        refreshEligibility()
        statusRevision &+= 1
        validateActivePairing()
    }
}
