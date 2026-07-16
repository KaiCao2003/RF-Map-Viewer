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

        guard isNewRegistration else { return }
        refreshEligibility()
        statusRevision &+= 1
        reevaluatePairing(afterDataChangeIn: store, id: id)
    }

    func unregister(id: UUID) {
        if let store = stores.removeValue(forKey: id)?.value {
            store.pairingDataDidChange = nil
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

        let state = source.viewerSyncState
        canonicalState = state
        isPairingEnabled = true
        expectedAppliedStates.removeAll(keepingCapacity: true)
        expectedAppliedStates[sourceID] = state
        broadcast(state, fields: .all, excluding: sourceID)
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
    }

    func statusText() -> String {
        switch eligibility {
        case .noSecondWindow:
            return "Open another loaded viewer window to enable sync."
        case .matching(let count):
            if isPairingEnabled {
                return "\(count) windows paired. Changes in any paired window sync to the others."
            }
            return "\(count) loaded windows have matching ordered unit lists."
        case .mismatch:
            return "Sync unavailable: loaded windows have different ordered unit lists."
        }
    }

    private func dataDidChange(in store: RFMappingStore, id: UUID) {
        guard stores[id]?.value === store else { return }
        expectedAppliedStates[id] = nil
        refreshEligibility()
        statusRevision &+= 1
        reevaluatePairing(afterDataChangeIn: store, id: id)
    }

    private func reevaluatePairing(afterDataChangeIn store: RFMappingStore, id: UUID) {
        guard isPairingEnabled else { return }
        guard cachedEligibility.canEnable else {
            disablePairing()
            return
        }
        guard store.data != nil, let canonicalState else { return }
        apply(canonicalState, to: store, id: id)
    }

    private func validateActivePairing() {
        guard isPairingEnabled else { return }
        if !cachedEligibility.canEnable {
            disablePairing()
        }
    }

    private func disablePairing() {
        guard isPairingEnabled || canonicalState != nil || !expectedAppliedStates.isEmpty else {
            return
        }
        isPairingEnabled = false
        canonicalState = nil
        expectedAppliedStates.removeAll(keepingCapacity: true)
        statusRevision &+= 1
    }

    private func broadcast(
        _ state: ViewerSyncState,
        fields: ViewerSyncFields,
        excluding sourceID: UUID,
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
        store.applyViewerSyncState(state, fields: fields)
        expectedAppliedStates[id] = store.viewerSyncState
    }

    private func currentEligibility() -> WindowPairingEligibility {
        let loaded = loadedStores()
        guard loaded.count >= 2 else {
            return .noSecondWindow(loadedWindowCount: loaded.count)
        }
        guard let firstUnits = loaded.first?.value.data?.unitPool else {
            return .noSecondWindow(loadedWindowCount: loaded.count)
        }
        let allMatch = loaded.dropFirst().allSatisfy { $0.value.data?.unitPool == firstUnits }
        return allMatch
            ? .matching(loadedWindowCount: loaded.count)
            : .mismatch(loadedWindowCount: loaded.count)
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
