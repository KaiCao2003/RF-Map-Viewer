import Darwin
import Darwin.crt_externs
import Dispatch
import Foundation

enum WaveformChannelMode: String, CaseIterable, Codable, Identifiable, Hashable, Sendable {
    case sameXColumn = "same_x_column"
    case sameShank = "same_shank"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .sameXColumn: "Same x column"
        case .sameShank: "Same shank"
        }
    }
}

struct WaveformChannel: Equatable, Sendable {
    let channelIndex: Int
    let channelID: Int
    let rawChannelIndex: Int
    let xMicrometers: Double
    let yMicrometers: Double
    let shankID: Int
}

struct WaveformUnitSummary: Equatable, Sendable {
    let unitIndex: Int
    let unitID: Int
    let quality: String
    let totalSpikeCount: Int
    let selectedSpikeCount: Int
    let timeCoveragePercent: Double
    let bestChannelIndex: Int
    let bestChannelID: Int
    let bestChannelXMicrometers: Double
    let bestChannelYMicrometers: Double
    let maxPeakToPeakMicrovolts: Double
    let unitDataDirectory: String
}

struct WaveformPayload: Equatable, Sendable {
    let sourceDirectory: URL
    let summary: WaveformUnitSummary
    let mode: WaveformChannelMode
    let baselineEndMilliseconds: Double
    let timesMilliseconds: [Double]
    let timeEdgesMilliseconds: [Double]
    /// Channel-major values: local channel, then time sample.
    let valuesMicrovolts: [[Double]]
    let channels: [WaveformChannel]
    let bestChannelRow: Int
    let amplitudeLimitMicrovolts: Double

    var channelLabels: [String] {
        channels.map { "ch \($0.channelID)" }
    }
}

enum WaveformArtifactError: LocalizedError, Equatable {
    case invalidData(String)
    case missingUnit(Int, scope: String)
    case decompression(String)

    var errorDescription: String? {
        switch self {
        case .invalidData(let message), .decompression(let message):
            message
        case .missingUnit(let unitID, let scope):
            "Unit \(unitID) is not available in this \(scope) waveform artifact."
        }
    }
}

enum POSIXGzipRunnerError: LocalizedError {
    case invalidTimeout
    case setup(String)
    case spawn(String)
    case wait(String)
    case timedOut(TimeInterval)
    case failed(String)
    case output(String)

    var errorDescription: String? {
        switch self {
        case .invalidTimeout:
            "gzip timeout must be finite and positive."
        case .setup(let detail):
            "Could not prepare gzip: \(detail)"
        case .spawn(let detail):
            "Could not start gzip: \(detail)"
        case .wait(let detail):
            "Could not wait for gzip: \(detail)"
        case .timedOut(let seconds):
            "gzip exceeded its \(String(format: "%.1f", seconds))-second timeout."
        case .failed(let detail):
            detail
        case .output(let detail):
            "Could not read gzip output: \(detail)"
        }
    }
}

/// Runs the system gzip without Foundation `Process` or `Pipe`. Child stdout
/// and stderr are regular files, so neither side can block on pipe capacity or
/// EOF ownership. The child is always reaped, including the bounded timeout
/// path used for corrupt or otherwise non-terminating inputs.
enum POSIXGzipRunner {
    private static let executable = "/usr/bin/gzip"
    private static let pollIntervalMicroseconds: useconds_t = 10_000

    static func run(
        arguments: [String],
        timeout: TimeInterval = 10
    ) throws -> Data {
        guard timeout.isFinite, timeout > 0 else {
            throw POSIXGzipRunnerError.invalidTimeout
        }

        let fileManager = FileManager.default
        let captureDirectory = fileManager.temporaryDirectory
            .appendingPathComponent("rfmapping-gzip-\(UUID().uuidString)", isDirectory: true)
        do {
            try fileManager.createDirectory(
                at: captureDirectory,
                withIntermediateDirectories: false
            )
        } catch {
            throw POSIXGzipRunnerError.setup(error.localizedDescription)
        }
        defer { try? fileManager.removeItem(at: captureDirectory) }

        let standardOutputURL = captureDirectory.appendingPathComponent("stdout")
        let standardErrorURL = captureDirectory.appendingPathComponent("stderr")
        var standardInputDescriptor = Darwin.open("/dev/null", O_RDONLY)
        var standardOutputDescriptor = standardOutputURL.path.withCString {
            Darwin.open($0, O_WRONLY | O_CREAT | O_TRUNC, mode_t(S_IRUSR | S_IWUSR))
        }
        var standardErrorDescriptor = standardErrorURL.path.withCString {
            Darwin.open($0, O_WRONLY | O_CREAT | O_TRUNC, mode_t(S_IRUSR | S_IWUSR))
        }
        defer {
            closeDescriptor(&standardInputDescriptor)
            closeDescriptor(&standardOutputDescriptor)
            closeDescriptor(&standardErrorDescriptor)
        }
        guard standardInputDescriptor >= 0 else {
            throw POSIXGzipRunnerError.setup(posixMessage(errno))
        }
        guard standardOutputDescriptor >= 0 else {
            throw POSIXGzipRunnerError.setup(posixMessage(errno))
        }
        guard standardErrorDescriptor >= 0 else {
            throw POSIXGzipRunnerError.setup(posixMessage(errno))
        }

        var fileActions: posix_spawn_file_actions_t?
        let initializationResult = posix_spawn_file_actions_init(&fileActions)
        guard initializationResult == 0 else {
            throw POSIXGzipRunnerError.setup(posixMessage(initializationResult))
        }
        defer { _ = posix_spawn_file_actions_destroy(&fileActions) }
        try addFileAction(
            &fileActions,
            source: standardInputDescriptor,
            destination: STDIN_FILENO
        )
        try addFileAction(
            &fileActions,
            source: standardOutputDescriptor,
            destination: STDOUT_FILENO
        )
        try addFileAction(
            &fileActions,
            source: standardErrorDescriptor,
            destination: STDERR_FILENO
        )

        var allocatedArguments: [UnsafeMutablePointer<CChar>] = []
        defer { allocatedArguments.forEach { free($0) } }
        for value in [executable] + arguments {
            guard let duplicate = strdup(value) else {
                throw POSIXGzipRunnerError.setup("Could not allocate gzip arguments.")
            }
            allocatedArguments.append(duplicate)
        }
        var argv: [UnsafeMutablePointer<CChar>?] = allocatedArguments.map { Optional($0) }
        argv.append(nil)

        var processID: pid_t = 0
        let spawnResult = argv.withUnsafeMutableBufferPointer { buffer in
            executable.withCString { executablePath in
                posix_spawn(
                    &processID,
                    executablePath,
                    &fileActions,
                    nil,
                    buffer.baseAddress,
                    _NSGetEnviron()!.pointee
                )
            }
        }
        closeDescriptor(&standardInputDescriptor)
        closeDescriptor(&standardOutputDescriptor)
        closeDescriptor(&standardErrorDescriptor)
        guard spawnResult == 0 else {
            throw POSIXGzipRunnerError.spawn(posixMessage(spawnResult))
        }

        let waitStatus = try waitForExit(processID, timeout: timeout)
        let errorData: Data
        do {
            errorData = try Data(contentsOf: standardErrorURL)
        } catch {
            throw POSIXGzipRunnerError.output(
                "stderr could not be read: \(error.localizedDescription)"
            )
        }
        let errorText = String(data: errorData, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if let exitCode = normalExitCode(waitStatus) {
            guard exitCode == 0 else {
                throw POSIXGzipRunnerError.failed(
                    "gzip exited with status \(exitCode)"
                        + (errorText?.isEmpty == false ? ": \(errorText!)" : ".")
                )
            }
        } else {
            let signal = waitStatus & 0x7f
            throw POSIXGzipRunnerError.failed(
                "gzip terminated by signal \(signal)"
                    + (errorText?.isEmpty == false ? ": \(errorText!)" : ".")
            )
        }

        do {
            return try Data(contentsOf: standardOutputURL)
        } catch {
            throw POSIXGzipRunnerError.output(error.localizedDescription)
        }
    }

    private static func addFileAction(
        _ fileActions: inout posix_spawn_file_actions_t?,
        source: Int32,
        destination: Int32
    ) throws {
        let duplicateResult = posix_spawn_file_actions_adddup2(
            &fileActions,
            source,
            destination
        )
        guard duplicateResult == 0 else {
            throw POSIXGzipRunnerError.setup(posixMessage(duplicateResult))
        }
        if source != destination {
            let closeResult = posix_spawn_file_actions_addclose(&fileActions, source)
            guard closeResult == 0 else {
                throw POSIXGzipRunnerError.setup(posixMessage(closeResult))
            }
        }
    }

    private static func waitForExit(
        _ processID: pid_t,
        timeout: TimeInterval
    ) throws -> Int32 {
        let timeoutNanoseconds = UInt64(min(timeout * 1_000_000_000, Double(UInt64.max)))
        let start = DispatchTime.now().uptimeNanoseconds
        var status: Int32 = 0
        while true {
            let result = waitpid(processID, &status, WNOHANG)
            if result == processID { return status }
            if result == -1 {
                let waitError = errno
                if waitError == EINTR { continue }
                terminateAndReap(processID)
                throw POSIXGzipRunnerError.wait(posixMessage(waitError))
            }
            let elapsed = DispatchTime.now().uptimeNanoseconds - start
            if elapsed >= timeoutNanoseconds {
                terminateAndReap(processID)
                throw POSIXGzipRunnerError.timedOut(timeout)
            }
            _ = usleep(pollIntervalMicroseconds)
        }
    }

    private static func terminateAndReap(_ processID: pid_t) {
        if Darwin.kill(processID, SIGKILL) == -1, errno != ESRCH { return }
        var status: Int32 = 0
        while true {
            let result = waitpid(processID, &status, 0)
            if result == processID || (result == -1 && errno == ECHILD) { return }
            if result == -1 && errno == EINTR { continue }
            return
        }
    }

    private static func normalExitCode(_ status: Int32) -> Int32? {
        guard status & 0x7f == 0 else { return nil }
        return (status >> 8) & 0xff
    }

    private static func closeDescriptor(_ descriptor: inout Int32) {
        guard descriptor >= 0 else { return }
        _ = Darwin.close(descriptor)
        descriptor = -1
    }

    private static func posixMessage(_ code: Int32) -> String {
        String(cString: strerror(code))
    }
}

private struct WaveformManifest: Decodable {
    struct Recording: Decodable {
        let samplingFrequencyHz: Double
        let numFrames: Int
        let durationMinutes: Double

        enum CodingKeys: String, CodingKey {
            case samplingFrequencyHz = "sampling_frequency_hz"
            case numFrames = "num_frames"
            case durationMinutes = "duration_minutes"
        }
    }

    struct Units: Decodable {
        let scope: String
        let count: Int
    }

    struct Waveform: Decodable {
        let numberBefore: Int
        let numberSamples: Int

        enum CodingKeys: String, CodingKey {
            case numberBefore = "nbefore"
            case numberSamples = "num_samples"
        }
    }

    struct Files: Decodable {
        let units: String
    }

    let schemaName: String
    let schemaVersion: Int
    let recording: Recording
    let units: Units
    let waveform: Waveform
    let files: Files

    enum CodingKeys: String, CodingKey {
        case schemaName = "schema_name"
        case schemaVersion = "schema_version"
        case recording, units, waveform, files
    }
}

private struct WaveformArtifactLocation: Equatable, Sendable {
    let directory: URL
    let scopeRoot: URL
}

/// Independent, read-only reader for the published schema-v4 waveform
/// artifact. The templates are loaded lazily and cached by unit ID.
final class WaveformArtifactStore: @unchecked Sendable {
    static let schemaName = "rfmapping-spikeinterface-waveforms"
    static let schemaVersion = 4
    static let localChannelCount = 5
    static let defaultBaselineEndMilliseconds = -0.25

    let sourceDirectory: URL
    let channels: [WaveformChannel]
    let timesMilliseconds: [Double]
    let timeEdgesMilliseconds: [Double]
    let unitScope: String
    let unitSummaries: [Int: WaveformUnitSummary]

    private let scopeRoot: URL
    private let sampleIndices: [Int]
    private let unitsURL: URL
    private let templateCacheCapacity: Int
    private let cacheLock = NSLock()
    private var templateCache: [Int: [[Double]]] = [:]
    private var templateCacheOrder: [Int] = []

    convenience init(directory: URL) throws {
        try self.init(directory: directory, scopeRoot: directory, templateCacheCapacity: 8)
    }

    init(
        directory: URL,
        scopeRoot: URL,
        templateCacheCapacity: Int = 8
    ) throws {
        guard templateCacheCapacity >= 1 else {
            throw WaveformArtifactError.invalidData(
                "Waveform template cache capacity must be positive."
            )
        }
        self.templateCacheCapacity = templateCacheCapacity
        let resolvedScope = scopeRoot.resolvingSymlinksInPath().standardizedFileURL
        let resolvedDirectory = directory.resolvingSymlinksInPath().standardizedFileURL
        guard Self.isWithin(resolvedDirectory, root: resolvedScope),
              Self.isDirectory(resolvedDirectory) else {
            throw WaveformArtifactError.invalidData(
                "Waveform artifact must be a directory inside the recording data scope."
            )
        }
        self.scopeRoot = resolvedScope
        sourceDirectory = resolvedDirectory

        let manifestURL = try Self.confinedFile(
            named: "manifest.json",
            inside: resolvedDirectory,
            scopeRoot: resolvedScope
        )
        let manifest: WaveformManifest
        do {
            manifest = try JSONDecoder().decode(
                WaveformManifest.self,
                from: Data(contentsOf: manifestURL, options: .mappedIfSafe)
            )
        } catch {
            throw WaveformArtifactError.invalidData(
                "manifest.json is not valid schema-v4 JSON: \(error.localizedDescription)"
            )
        }
        guard manifest.schemaName == Self.schemaName else {
            throw WaveformArtifactError.invalidData(
                "Unsupported waveform schema name: \(manifest.schemaName)."
            )
        }
        guard manifest.schemaVersion == Self.schemaVersion else {
            throw WaveformArtifactError.invalidData(
                "Unsupported waveform schema version: \(manifest.schemaVersion)."
            )
        }
        guard ["all", "good", "present_good"].contains(manifest.units.scope),
              manifest.units.count >= 0 else {
            throw WaveformArtifactError.invalidData(
                "manifest.units must contain a supported scope and non-negative count."
            )
        }
        guard manifest.recording.samplingFrequencyHz.isFinite,
              manifest.recording.samplingFrequencyHz > 0,
              manifest.recording.numFrames >= 1,
              manifest.recording.durationMinutes.isFinite,
              manifest.recording.durationMinutes > 0 else {
            throw WaveformArtifactError.invalidData(
                "manifest.recording values must be positive and finite."
            )
        }
        guard manifest.waveform.numberSamples >= 2,
              manifest.waveform.numberBefore >= 0,
              manifest.waveform.numberBefore <= manifest.waveform.numberSamples else {
            throw WaveformArtifactError.invalidData(
                "manifest.waveform has invalid nbefore or num_samples values."
            )
        }
        unitScope = manifest.units.scope

        let channelsURL = try Self.confinedFile(
            named: "channels.csv",
            inside: resolvedDirectory,
            scopeRoot: resolvedScope
        )
        let channelRows = try WaveformCSVTable(
            url: channelsURL,
            exactHeader: [
                "channel_index", "channel_id", "raw_channel_index",
                "x_um", "y_um", "shank_id",
            ]
        ).rows
        guard !channelRows.isEmpty else {
            throw WaveformArtifactError.invalidData(
                "channels.csv must contain at least one channel."
            )
        }
        var parsedChannels: [WaveformChannel] = []
        var seenChannelIDs: Set<Int> = []
        for (offset, row) in channelRows.enumerated() {
            let channel = WaveformChannel(
                channelIndex: try Self.integer(row["channel_index"], label: "channel_index"),
                channelID: try Self.integer(row["channel_id"], label: "channel_id"),
                rawChannelIndex: try Self.integer(row["raw_channel_index"], label: "raw_channel_index"),
                xMicrometers: try Self.finiteDouble(row["x_um"], label: "x_um"),
                yMicrometers: try Self.finiteDouble(row["y_um"], label: "y_um"),
                shankID: try Self.integer(row["shank_id"], label: "shank_id")
            )
            guard channel.channelIndex == offset else {
                throw WaveformArtifactError.invalidData(
                    "channels.csv channel_index must be contiguous and row ordered."
                )
            }
            guard seenChannelIDs.insert(channel.channelID).inserted else {
                throw WaveformArtifactError.invalidData(
                    "channels.csv contains duplicate channel_id \(channel.channelID)."
                )
            }
            parsedChannels.append(channel)
        }
        channels = parsedChannels

        let timeURL = try Self.confinedFile(
            named: "waveform_time.csv",
            inside: resolvedDirectory,
            scopeRoot: resolvedScope
        )
        let timeRows = try WaveformCSVTable(
            url: timeURL,
            exactHeader: ["sample_index", "sample_offset", "time_ms"]
        ).rows
        guard timeRows.count == manifest.waveform.numberSamples else {
            throw WaveformArtifactError.invalidData(
                "waveform_time.csv row count does not match manifest.waveform.num_samples."
            )
        }
        var parsedSampleIndices: [Int] = []
        var sampleOffsets: [Int] = []
        var parsedTimes: [Double] = []
        for (offset, row) in timeRows.enumerated() {
            let sampleIndex = try Self.integer(row["sample_index"], label: "sample_index")
            let sampleOffset = try Self.integer(row["sample_offset"], label: "sample_offset")
            let time = try Self.finiteDouble(row["time_ms"], label: "time_ms")
            guard sampleIndex == offset else {
                throw WaveformArtifactError.invalidData(
                    "waveform_time.csv sample_index must be contiguous and row ordered."
                )
            }
            parsedSampleIndices.append(sampleIndex)
            sampleOffsets.append(sampleOffset)
            parsedTimes.append(time)
        }
        guard sampleOffsets.first == -manifest.waveform.numberBefore,
              zip(sampleOffsets, sampleOffsets.dropFirst()).allSatisfy({ pair in
                  pair.1 == pair.0 + 1
              }),
              zip(parsedTimes, parsedTimes.dropFirst()).allSatisfy({ pair in
                  pair.0 < pair.1
              }) else {
            throw WaveformArtifactError.invalidData(
                "waveform_time.csv offsets or time_ms values do not match the manifest."
            )
        }
        sampleIndices = parsedSampleIndices
        timesMilliseconds = parsedTimes
        let timeSteps = zip(parsedTimes, parsedTimes.dropFirst()).map { pair in
            pair.1 - pair.0
        }.sorted()
        let middleStep = timeSteps.count / 2
        let medianStep = timeSteps.count.isMultiple(of: 2)
            ? (timeSteps[middleStep - 1] + timeSteps[middleStep]) / 2
            : timeSteps[middleStep]
        timeEdgesMilliseconds = [parsedTimes[0] - medianStep / 2]
            + zip(parsedTimes, parsedTimes.dropFirst()).map { pair in
                (pair.0 + pair.1) / 2
            }
            + [parsedTimes[parsedTimes.count - 1] + medianStep / 2]

        unitsURL = try Self.confinedRelativeFile(
            manifest.files.units,
            inside: resolvedDirectory,
            scopeRoot: resolvedScope,
            label: "manifest.files.units"
        )
        let unitRows = try WaveformCSVTable(
            url: unitsURL,
            exactHeader: [
                "unit_index", "unit_id", "quality", "total_spike_count",
                "selected_spike_count", "time_coverage_percent",
                "best_channel_index", "best_channel_id", "best_channel_x_um",
                "best_channel_y_um", "max_ptp_uv", "unit_data_dir",
            ]
        ).rows
        guard unitRows.count == manifest.units.count else {
            throw WaveformArtifactError.invalidData(
                "units.csv row count does not match manifest.units.count."
            )
        }
        var summaries: [Int: WaveformUnitSummary] = [:]
        for (offset, row) in unitRows.enumerated() {
            let directoryName = row["unit_data_dir"] ?? ""
            guard Self.isSafeRelativePath(directoryName) else {
                throw WaveformArtifactError.invalidData(
                    "unit_data_dir must stay within the waveform artifact."
                )
            }
            let summary = WaveformUnitSummary(
                unitIndex: try Self.integer(row["unit_index"], label: "unit_index"),
                unitID: try Self.integer(row["unit_id"], label: "unit_id"),
                quality: (row["quality"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines),
                totalSpikeCount: try Self.integer(row["total_spike_count"], label: "total_spike_count"),
                selectedSpikeCount: try Self.integer(row["selected_spike_count"], label: "selected_spike_count"),
                timeCoveragePercent: try Self.finiteDouble(row["time_coverage_percent"], label: "time_coverage_percent"),
                bestChannelIndex: try Self.integer(row["best_channel_index"], label: "best_channel_index"),
                bestChannelID: try Self.integer(row["best_channel_id"], label: "best_channel_id"),
                bestChannelXMicrometers: try Self.finiteDouble(row["best_channel_x_um"], label: "best_channel_x_um"),
                bestChannelYMicrometers: try Self.finiteDouble(row["best_channel_y_um"], label: "best_channel_y_um"),
                maxPeakToPeakMicrovolts: try Self.finiteDouble(row["max_ptp_uv"], label: "max_ptp_uv"),
                unitDataDirectory: directoryName
            )
            guard summary.unitIndex == offset,
                  !summary.quality.isEmpty,
                  summary.totalSpikeCount >= 0,
                  summary.selectedSpikeCount >= 0,
                  summary.selectedSpikeCount <= summary.totalSpikeCount,
                  (0.0...100.0 + 1e-9).contains(summary.timeCoveragePercent),
                  channels.indices.contains(summary.bestChannelIndex),
                  summary.maxPeakToPeakMicrovolts >= 0 else {
                throw WaveformArtifactError.invalidData(
                    "units.csv row \(offset + 2) contains invalid summary values."
                )
            }
            let best = channels[summary.bestChannelIndex]
            guard summary.bestChannelID == best.channelID,
                  abs(summary.bestChannelXMicrometers - best.xMicrometers) <= 1e-6,
                  abs(summary.bestChannelYMicrometers - best.yMicrometers) <= 1e-6 else {
                throw WaveformArtifactError.invalidData(
                    "units.csv best-channel fields do not match channels.csv."
                )
            }
            guard summaries[summary.unitID] == nil else {
                throw WaveformArtifactError.invalidData(
                    "units.csv contains duplicate unit_id \(summary.unitID)."
                )
            }
            summaries[summary.unitID] = summary
        }
        unitSummaries = summaries
    }

    static func discover(
        forRFURL sourceURL: URL,
        fileManager: FileManager = .default
    ) throws -> WaveformArtifactStore? {
        guard let location = discoverLocation(forRFURL: sourceURL, fileManager: fileManager) else {
            return nil
        }
        return try WaveformArtifactStore(
            directory: location.directory,
            scopeRoot: location.scopeRoot
        )
    }

    func summary(for unitID: Int) throws -> WaveformUnitSummary {
        guard let summary = unitSummaries[unitID] else {
            throw WaveformArtifactError.missingUnit(unitID, scope: unitScope)
        }
        return summary
    }

    func payload(
        for unitID: Int,
        mode: WaveformChannelMode,
        localChannelCount: Int = WaveformArtifactStore.localChannelCount,
        baselineEndMilliseconds: Double = WaveformArtifactStore.defaultBaselineEndMilliseconds
    ) throws -> WaveformPayload {
        guard localChannelCount >= 1,
              baselineEndMilliseconds.isFinite else {
            throw WaveformArtifactError.invalidData(
                "Waveform local-channel count and baseline endpoint are invalid."
            )
        }
        let summary = try summary(for: unitID)
        let template = try template(for: summary)
        let baselineRows = timesMilliseconds.indices.filter {
            timesMilliseconds[$0] <= baselineEndMilliseconds
        }
        guard !baselineRows.isEmpty else {
            throw WaveformArtifactError.invalidData(
                "No waveform samples are at or before \(baselineEndMilliseconds) ms."
            )
        }
        var corrected = template
        for channelIndex in channels.indices {
            let baseline = baselineRows.reduce(0.0) {
                $0 + template[$1][channelIndex]
            } / Double(baselineRows.count)
            for sampleIndex in template.indices {
                corrected[sampleIndex][channelIndex] -= baseline
            }
        }
        let selected = localChannelIndices(
            bestChannelIndex: summary.bestChannelIndex,
            mode: mode,
            count: localChannelCount
        )
        guard let bestRow = selected.firstIndex(of: summary.bestChannelIndex) else {
            throw WaveformArtifactError.invalidData(
                "Local channel selection lost the best channel."
            )
        }
        let amplitude = max(
            corrected.lazy.flatMap { $0 }.map { abs($0) }.max() ?? 0,
            // NumPy float64 epsilon, matching the Python/Web contract for a
            // constant baseline-corrected waveform.
            Double.ulpOfOne
        )
        return WaveformPayload(
            sourceDirectory: sourceDirectory,
            summary: summary,
            mode: mode,
            baselineEndMilliseconds: baselineEndMilliseconds,
            timesMilliseconds: timesMilliseconds,
            timeEdgesMilliseconds: timeEdgesMilliseconds,
            valuesMicrovolts: selected.map { channelIndex in
                corrected.map { $0[channelIndex] }
            },
            channels: selected.map { channels[$0] },
            bestChannelRow: bestRow,
            amplitudeLimitMicrovolts: amplitude
        )
    }

    func sharedAmplitudeLimit(
        unitIDs: [Int],
        mode: WaveformChannelMode
    ) -> Double? {
        unitIDs.compactMap {
            try? payload(for: $0, mode: mode).amplitudeLimitMicrovolts
        }.max()
    }

    func sourceURLs(for unitID: Int) throws -> [URL] {
        let summary = try summary(for: unitID)
        return [
            sourceDirectory.appendingPathComponent("manifest.json"),
            sourceDirectory.appendingPathComponent("channels.csv"),
            sourceDirectory.appendingPathComponent("waveform_time.csv"),
            unitsURL,
            try templateURL(for: summary),
        ]
    }

    private func localChannelIndices(
        bestChannelIndex: Int,
        mode: WaveformChannelMode,
        count: Int
    ) -> [Int] {
        let best = channels[bestChannelIndex]
        let candidates = channels.indices.filter { index in
            switch mode {
            case .sameXColumn:
                abs(channels[index].xMicrometers - best.xMicrometers) <= 1e-6
            case .sameShank:
                channels[index].shankID == best.shankID
            }
        }
        let nearest = candidates.filter { $0 != bestChannelIndex }.sorted { left, right in
            let leftDistance = hypot(
                channels[left].xMicrometers - best.xMicrometers,
                channels[left].yMicrometers - best.yMicrometers
            )
            let rightDistance = hypot(
                channels[right].xMicrometers - best.xMicrometers,
                channels[right].yMicrometers - best.yMicrometers
            )
            return leftDistance == rightDistance ? left < right : leftDistance < rightDistance
        }
        let selected = [bestChannelIndex]
            + Array(nearest.prefix(max(0, count - 1)))
        return selected.sorted { left, right in
            let leftChannel = channels[left]
            let rightChannel = channels[right]
            if leftChannel.yMicrometers != rightChannel.yMicrometers {
                return leftChannel.yMicrometers > rightChannel.yMicrometers
            }
            if leftChannel.xMicrometers != rightChannel.xMicrometers {
                return leftChannel.xMicrometers < rightChannel.xMicrometers
            }
            return left < right
        }
    }

    private func template(for summary: WaveformUnitSummary) throws -> [[Double]] {
        cacheLock.lock()
        let cached = templateCache[summary.unitID]
        if cached != nil {
            templateCacheOrder.removeAll { $0 == summary.unitID }
            templateCacheOrder.append(summary.unitID)
        }
        cacheLock.unlock()
        if let cached { return cached }

        let url = try templateURL(for: summary)
        let data: Data
        do {
            data = try POSIXGzipRunner.run(
                arguments: ["-dc", "--", url.path]
            )
        } catch {
            throw WaveformArtifactError.decompression(
                "Could not decompress \(url.lastPathComponent): \(error.localizedDescription)"
            )
        }
        guard var text = String(data: data, encoding: .utf8) else {
            throw WaveformArtifactError.invalidData(
                "\(url.lastPathComponent) must decompress to UTF-8 CSV."
            )
        }
        if text.first == "\u{FEFF}" { text.removeFirst() }
        let expectedHeader = ["sample_index"]
            + channels.indices.map { String(format: "chidx_%03d_uv", $0) }
        let table = try WaveformCSVTable(
            text: text,
            filename: url.lastPathComponent,
            exactHeader: expectedHeader
        )
        guard table.rows.count == timesMilliseconds.count else {
            throw WaveformArtifactError.invalidData(
                "\(url.lastPathComponent) row count does not match waveform_time.csv."
            )
        }
        var values = Array(
            repeating: Array(repeating: 0.0, count: channels.count),
            count: timesMilliseconds.count
        )
        for (sampleIndex, row) in table.rows.enumerated() {
            guard try Self.integer(row["sample_index"], label: "sample_index")
                    == sampleIndices[sampleIndex] else {
                throw WaveformArtifactError.invalidData(
                    "\(url.lastPathComponent) sample_index does not match waveform_time.csv."
                )
            }
            for channelIndex in channels.indices {
                values[sampleIndex][channelIndex] = try Self.finiteDouble(
                    row[String(format: "chidx_%03d_uv", channelIndex)],
                    label: "channel \(channelIndex) amplitude"
                )
            }
        }
        cacheLock.lock()
        templateCache[summary.unitID] = values
        templateCacheOrder.removeAll { $0 == summary.unitID }
        templateCacheOrder.append(summary.unitID)
        while templateCacheOrder.count > templateCacheCapacity {
            templateCache[templateCacheOrder.removeFirst()] = nil
        }
        cacheLock.unlock()
        return values
    }

    private func templateURL(for summary: WaveformUnitSummary) throws -> URL {
        try Self.confinedRelativeFile(
            summary.unitDataDirectory + "/template_uv.csv.gz",
            inside: sourceDirectory,
            scopeRoot: scopeRoot,
            label: "unit template"
        )
    }

    private static func discoverLocation(
        forRFURL sourceURL: URL,
        fileManager: FileManager
    ) -> WaveformArtifactLocation? {
        guard let probeName = HDTuningDiscovery.probeName(forRFURL: sourceURL) else {
            return nil
        }
        let source = sourceURL.standardizedFileURL
        var parents: [URL] = []
        var current = source.deletingLastPathComponent()
        while true {
            parents.append(current)
            let parent = current.deletingLastPathComponent()
            if parent.path == current.path { break }
            current = parent
        }
        guard let sourceParent = parents.first else { return nil }
        let session = parents.first { HDTuningDiscovery.isSessionName($0.lastPathComponent) }
        let dataBoundary = session.flatMap { session in
            parents.first {
                $0.lastPathComponent == "data"
                    && $0.deletingLastPathComponent().standardizedFileURL
                        == session.standardizedFileURL
            }
        } ?? parents.first { $0.lastPathComponent == "data" }
        let boundary = dataBoundary ?? session ?? sourceParent
        guard let boundaryIndex = parents.firstIndex(where: {
            $0.standardizedFileURL == boundary.standardizedFileURL
        }) else { return nil }
        let bases = parents[...boundaryIndex]
        let resolvedBoundary = boundary.resolvingSymlinksInPath().standardizedFileURL
        for base in bases {
            for candidate in [
                base.appendingPathComponent("waveform", isDirectory: true)
                    .appendingPathComponent(probeName, isDirectory: true),
                base.appendingPathComponent("data", isDirectory: true)
                    .appendingPathComponent("waveform", isDirectory: true)
                    .appendingPathComponent(probeName, isDirectory: true),
                base.appendingPathComponent(probeName, isDirectory: true),
                base.lastPathComponent == probeName
                    ? base
                    : base.appendingPathComponent("__not_an_artifact__", isDirectory: true),
            ] {
                let resolved = candidate.resolvingSymlinksInPath().standardizedFileURL
                guard isWithin(resolved, root: resolvedBoundary),
                      isDirectory(resolved),
                      fileManager.fileExists(
                        atPath: resolved.appendingPathComponent("manifest.json").path
                      ) else { continue }
                return WaveformArtifactLocation(
                    directory: resolved,
                    scopeRoot: resolvedBoundary
                )
            }
        }
        return nil
    }

    private static func confinedFile(
        named filename: String,
        inside directory: URL,
        scopeRoot: URL
    ) throws -> URL {
        try confinedRelativeFile(
            filename,
            inside: directory,
            scopeRoot: scopeRoot,
            label: filename
        )
    }

    private static func confinedRelativeFile(
        _ relativePath: String,
        inside directory: URL,
        scopeRoot: URL,
        label: String
    ) throws -> URL {
        guard isSafeRelativePath(relativePath) else {
            throw WaveformArtifactError.invalidData(
                "\(label) must be a non-empty relative path inside the waveform artifact."
            )
        }
        let components = relativePath.split(separator: "/").map(String.init)
        let candidate = components.reduce(directory) {
            $0.appendingPathComponent($1)
        }.resolvingSymlinksInPath().standardizedFileURL
        guard isWithin(candidate, root: directory),
              isWithin(candidate, root: scopeRoot),
              let values = try? candidate.resourceValues(forKeys: [.isRegularFileKey]),
              values.isRegularFile == true else {
            throw WaveformArtifactError.invalidData(
                "Required waveform file was not found inside the artifact: \(relativePath)."
            )
        }
        return candidate
    }

    private static func isSafeRelativePath(_ value: String) -> Bool {
        guard !value.isEmpty, !value.hasPrefix("/"), !value.contains("\\") else {
            return false
        }
        let components = value.split(separator: "/", omittingEmptySubsequences: false)
        return components.allSatisfy { !$0.isEmpty && $0 != "." && $0 != ".." }
    }

    private static func isWithin(_ candidate: URL, root: URL) -> Bool {
        let candidatePath = candidate.resolvingSymlinksInPath().standardizedFileURL.path
        let rootPath = root.resolvingSymlinksInPath().standardizedFileURL.path
        return candidatePath == rootPath || candidatePath.hasPrefix(rootPath + "/")
    }

    private static func isDirectory(_ url: URL) -> Bool {
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory)
            && isDirectory.boolValue
    }

    private static func finiteDouble(_ value: String?, label: String) throws -> Double {
        guard let value,
              let parsed = Double(value.trimmingCharacters(in: .whitespacesAndNewlines)),
              parsed.isFinite else {
            throw WaveformArtifactError.invalidData("\(label) must be finite.")
        }
        return parsed
    }

    private static func integer(_ value: String?, label: String) throws -> Int {
        guard let value,
              let parsed = Int(value.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            throw WaveformArtifactError.invalidData("\(label) must be an integer.")
        }
        return parsed
    }
}

private struct WaveformCSVTable {
    let rows: [[String: String]]

    init(url: URL, exactHeader: [String]) throws {
        let data: Data
        do {
            data = try Data(contentsOf: url, options: .mappedIfSafe)
        } catch {
            throw WaveformArtifactError.invalidData(
                "\(url.lastPathComponent) could not be read: \(error.localizedDescription)"
            )
        }
        guard var text = String(data: data, encoding: .utf8) else {
            throw WaveformArtifactError.invalidData(
                "\(url.lastPathComponent) must be UTF-8 encoded."
            )
        }
        if text.first == "\u{FEFF}" { text.removeFirst() }
        try self.init(
            text: text,
            filename: url.lastPathComponent,
            exactHeader: exactHeader
        )
    }

    init(text: String, filename: String, exactHeader: [String]) throws {
        let records = try Self.parse(text, filename: filename)
        guard records.first == exactHeader else {
            throw WaveformArtifactError.invalidData(
                "\(filename) header must be \(exactHeader.joined(separator: ","))."
            )
        }
        var decoded: [[String: String]] = []
        for (offset, fields) in records.dropFirst().enumerated() {
            guard fields.count == exactHeader.count else {
                throw WaveformArtifactError.invalidData(
                    "\(filename) row \(offset + 2) has the wrong column count."
                )
            }
            decoded.append(Dictionary(uniqueKeysWithValues: zip(exactHeader, fields)))
        }
        rows = decoded
    }

    private static func parse(_ text: String, filename: String) throws -> [[String]] {
        var records: [[String]] = []
        var record: [String] = []
        var field = ""
        var inQuotes = false
        let scalars = text.unicodeScalars
        var index = scalars.startIndex

        func finishField() {
            record.append(field)
            field.removeAll(keepingCapacity: true)
        }
        func finishRecord() {
            finishField()
            records.append(record)
            record.removeAll(keepingCapacity: true)
        }

        while index < scalars.endIndex {
            let scalar = scalars[index]
            let next = scalars.index(after: index)
            if inQuotes {
                if scalar == "\"" {
                    if next < scalars.endIndex, scalars[next] == "\"" {
                        field.append("\"")
                        index = scalars.index(after: next)
                        continue
                    }
                    inQuotes = false
                } else {
                    field.unicodeScalars.append(scalar)
                }
            } else {
                switch scalar {
                case "\"" where field.isEmpty:
                    inQuotes = true
                case ",":
                    finishField()
                case "\n":
                    finishRecord()
                case "\r":
                    finishRecord()
                    if next < scalars.endIndex, scalars[next] == "\n" {
                        index = scalars.index(after: next)
                        continue
                    }
                default:
                    field.unicodeScalars.append(scalar)
                }
            }
            index = next
        }
        guard !inQuotes else {
            throw WaveformArtifactError.invalidData(
                "\(filename) contains an unterminated quoted field."
            )
        }
        if !field.isEmpty || !record.isEmpty { finishRecord() }
        return records
    }
}
