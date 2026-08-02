import SwiftUI

struct ViewerSettingsView: View {
    @Bindable var preferences: ViewerPreferences

    var body: some View {
        Form {
            Section("HD tuning curve") {
                Toggle("Show beside the RF map", isOn: $preferences.showTuningCurve)
                Toggle(
                    "Find tuning_curves.json automatically",
                    isOn: $preferences.autoLoadTuningCurve
                )
                .disabled(!preferences.showTuningCurve)

                Picker("Plot style", selection: $preferences.tuningPlotMode) {
                    ForEach(TuningPlotMode.allCases) { mode in
                        Text(mode.rawValue).tag(mode)
                    }
                }

                Picker("Arrangement", selection: $preferences.tuningLayout) {
                    ForEach(TuningLayout.allCases) { layout in
                        Text(layout.rawValue).tag(layout)
                    }
                }

                Picker("Displayed bins", selection: $preferences.tuningDisplayBins) {
                    ForEach(ViewerPreferences.tuningBinChoices, id: \.self) { count in
                        Text("\(count)").tag(count)
                    }
                }
            }

            Section("Scientific display") {
                Toggle("Circular Gaussian smoothing", isOn: $preferences.tuningSmoothing)
                Stepper(
                    value: $preferences.tuningSmoothingDegrees,
                    in: 1...90,
                    step: 1
                ) {
                    LabeledContent("Smoothing width") {
                        Text("σ = \(preferences.tuningSmoothingDegrees, format: .number.precision(.fractionLength(0...1)))°")
                            .monospacedDigit()
                    }
                }
                .disabled(!preferences.tuningSmoothing)

                Toggle(
                    "Compare cells on one shared 0–peak Hz scale",
                    isOn: $preferences.tuningCompareScale
                )

                Text(
                    preferences.tuningCompareScale
                        ? "Every attached cell uses the same radial or y-axis scale."
                        : "Each cell starts at 0 Hz and uses its own peak so low-rate tuning remains legible."
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }

            Section {
                Button("Restore Tuning Defaults") {
                    preferences.restoreTuningDefaults()
                }
            }
        }
        .formStyle(.grouped)
        .padding(16)
        .frame(width: 520, height: 500)
    }
}
