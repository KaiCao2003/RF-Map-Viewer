import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        HStack(spacing: 0) {
            SidebarView(store: store)
                .frame(width: 318)
            Divider()
            mainContent
        }
        .fileImporter(
            isPresented: $store.isImporting,
            allowedContentTypes: [.json],
            allowsMultipleSelection: false
        ) { result in
            switch result {
            case .success(let urls):
                if let url = urls.first {
                    store.loadJSON(url)
                }
            case .failure(let error):
                store.errorMessage = error.localizedDescription
            }
        }
        .fileExporter(
            isPresented: $store.isExporting,
            document: store.exportDocument,
            contentType: .rfCSV,
            defaultFilename: store.exportFilename
        ) { result in
            if case .failure(let error) = result {
                store.errorMessage = error.localizedDescription
            }
        }
        .alert(
            "RF Mapping Viewer",
            isPresented: Binding(
                get: { store.errorMessage != nil },
                set: { if !$0 { store.errorMessage = nil } }
            )
        ) {
            Button("OK") {
                store.errorMessage = nil
            }
        } message: {
            Text(store.errorMessage ?? "")
        }
    }

    @ViewBuilder
    private var mainContent: some View {
        if store.hasData {
            VStack(spacing: 0) {
                HeaderView(store: store)
                Divider()
                PlotTabsView(store: store)
            }
        } else {
            VStack(spacing: 16) {
                Text("RF Mapping Viewer")
                    .font(.title2.weight(.semibold))
                Button("Open JSON") {
                    store.isImporting = true
                }
                .keyboardShortcut("o", modifiers: [.command])
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}

private struct HeaderView: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(store.headerTitle)
                .font(.system(size: 17, weight: .semibold))
            Text(store.statusText)
                .font(.callout)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }
}

private struct PlotTabsView: View {
    @Bindable var store: RFMappingStore

    var body: some View {
        VStack(spacing: 0) {
            Picker("View", selection: $store.selectedTab) {
                ForEach(PlotTab.allCases) { tab in
                    Text(tab.rawValue).tag(tab)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding([.horizontal, .top], 12)
            .padding(.bottom, 8)

            Group {
                switch store.selectedTab {
                case .rf:
                    HeatmapView(store: store, kind: .rf)
                case .delay:
                    HeatmapView(store: store, kind: .delay)
                case .polar:
                    PolarMapView(store: store)
                case .timeline:
                    TimelineView(store: store)
                case .rgb:
                    RGBMapView(store: store)
                case .stack:
                    StackView(store: store)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}
