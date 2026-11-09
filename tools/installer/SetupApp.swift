// NFS Most Wanted Native - Setup.
//
// A small Mac app in front of the setup backend (tools/setup.py). The player chooses an installed PC game folder (or
// its .zip, or the PC disc images with the 1.3 update) and, optionally, their own Xbox 360 copy; the backend builds the
// native app on this Mac. The app carries no game files:
// it ships this project's reviewed source (Resources/source.tar.gz), a relocatable Python (Resources/python) and the
// FFmpeg source tarball (Resources/third_party/dl), and talks to the backend only through its command line and its
// JSON-line events (the setup contract).
//
// Headless checks used by tests/setup (nothing opens a window, nothing touches the player's folders):
//   Setup --protocol-test STREAM_FILE EXIT_STATUS CHUNK_BYTES check|build [cancelled]   -> outcome as JSON
//   Setup --env-test                                                                    -> child environment as JSON
//   Setup --cancel-test            starts a shell with two background sleeps, cancels it, reports survivors as JSON
//   Setup --install-cancel-test STAGE GRACE MARKER   cancels a fake backend in STAGE whose TERM handler takes 2*GRACE s
//                                                    to "restore" (writes MARKER); reports exit status and the marker
//   Setup --quit-policy-test       what quitting does when idle, while checking and while building, as JSON

import AppKit
import SwiftUI
import UniformTypeIdentifiers
import CryptoKit

// The oldest macOS this Setup promises is chosen in build_installer.sh (MIN_MACOS), the single place that sets it.

// MARK: - Protocol (pure logic, no UI)

let stageTable: [(id: String, title: String)] = [
    ("preflight", "Checking your Mac and your game"),
    ("pc", "Reading the PC game"),
    ("x360", "Reading your Xbox 360 copy"),
    ("recompile", "Translating the game code"),
    ("tables", "Building data tables"),
    ("audio", "Building the audio code"),
    ("shaders", "Building the graphics code"),
    ("ffmpeg", "Building the video decoder"),
    ("runtime", "Linking the game runtime"),
    ("assets", "Preparing game data"),
    ("bundle", "Building and signing the app"),
    ("install", "Installing the app"),
]

/// Shown to the player and used to refuse a full disk. Measured peak: 9.4 GB with a game folder and an
/// Xbox 360 copy, plus 2.8 GB for an unpacked .zip or disc set.
let requiredFreeGB = 12.0

// Optional extras use a separate terminal result. Source recognition never
// implies that a converted pack has passed the packaged approval policy.
struct RecognizedCar: Codable, Equatable {
    var car_hash: String
    var status: String
}
struct CarRecognition: Codable, Equatable {
    var game: String
    var platform: String
    var approval_available: Bool
    var output_verified: Bool
    var cars: [RecognizedCar]
}
struct CarVolume: Codable, Equatable {
    var device: String
    var paths: [String]
    var extraction_bytes: Int
    var staging_bytes: Int?
    var output_bytes: Int?
    var retention_bytes: Int
    var margin_bytes: Int
    var available_bytes: Int
    var required_bytes: Int?
    var sufficient: Bool?
}
struct PlannedCar: Codable, Equatable {
    var slot: Int
    var name: String
    var car_hash: String
    var approved: Bool
    var reused: Bool
    var reason: String
}
struct CarPlan: Codable, Equatable {
    var measured: Bool
    var approved: Bool
    var volumes: [CarVolume]
    var cars: [PlannedCar]
}
struct CarProgress: Codable, Equatable {
    var phase: String
    var slot: Int
    var done: Int
    var total: Int
}
struct InstalledCar: Codable, Equatable {
    var slot: Int
    var name: String
    var state: String
    var selected: Bool
}
struct RetainedGeneration: Codable, Equatable {
    var generation: String
    var verified: Bool
}
struct CarResult: Codable, Equatable {
    var operation: String
    var verified: Bool
    var generation: String
    var message: String
    var selection: [Int]
    var cars: [InstalledCar]
    var generations: [RetainedGeneration]
}

func exactKeys(_ object: [String: Any], _ names: String) -> Bool {
    Set(object.keys) == Set(names.split(separator: " ").map(String.init))
}
func validGeneration(_ value: String) -> Bool {
    value.count == 66 && value.hasPrefix("g-") && value.dropFirst(2).allSatisfy { "0123456789abcdef".contains($0) }
}

enum SetupEvent: Equatable {
    case stage(id: String, title: String)
    case progress(id: String, done: Int, total: Int)
    case warn(id: String, msg: String)
    case error(id: String, msg: String, fix: String, log: String)
    case features(x360: Bool, missing: [String])
    case done(app: String)
    case carsRecognition(CarRecognition)
    case carsPlan(CarPlan)
    case carsProgress(CarProgress)
    case carsResult(CarResult)
}

func parseEvent(_ line: String) -> SetupEvent? {
    let trimmed = line.trimmingCharacters(in: .whitespaces)
    guard trimmed.hasPrefix("{"), let data = trimmed.data(using: .utf8),
          let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
          let ev = obj["ev"] as? String else { return nil }
    let decoder = JSONDecoder()
    func str(_ k: String) -> String? { obj[k] as? String }
    switch ev {
    case "stage":
        guard exactKeys(obj, "ev id title"), let id = str("id"), let title = str("title"),
              (stageTable.map { $0.id } + ["stage", "cars"]).contains(id) else { return nil }
        return .stage(id: id, title: title)
    case "progress":
        struct Progress: Decodable { var id: String; var done: Int; var total: Int }
        guard exactKeys(obj, "ev id done total"), let row = try? decoder.decode(Progress.self, from: data),
              row.done >= 0, row.total >= 0 else { return nil }
        return .progress(id: row.id, done: row.done, total: row.total)
    case "warn":
        guard exactKeys(obj, "ev id msg"), let id = str("id"), let msg = str("msg") else { return nil }
        return .warn(id: id, msg: msg)
    case "error":
        guard exactKeys(obj, "ev id msg fix log"), let id = str("id"), let msg = str("msg"),
              let fix = str("fix"), let log = str("log") else { return nil }
        return .error(id: id, msg: msg, fix: fix, log: log)
    case "features":
        struct Features: Decodable { var x360: Bool; var missing: [String] }
        guard exactKeys(obj, "ev x360 missing"), let row = try? decoder.decode(Features.self, from: data) else { return nil }
        return .features(x360: row.x360, missing: row.missing)
    case "done":
        guard exactKeys(obj, "ev app"), let app = str("app") else { return nil }
        return .done(app: app)
    case "cars-recognition":
        guard exactKeys(obj, "ev game platform approval_available output_verified cars"),
              let row = try? decoder.decode(CarRecognition.self, from: data), row.game == "carbon",
              ["pc", "x360"].contains(row.platform), !row.output_verified,
              let raw = obj["cars"] as? [[String: Any]], raw.allSatisfy({ exactKeys($0, "car_hash status") }),
              row.cars.allSatisfy({ $0.car_hash.count == 8 && $0.car_hash.allSatisfy { "0123456789abcdef".contains($0) }
                  && ["importable", "missing", "unknown"].contains($0.status) }) else { return nil }
        return .carsRecognition(row)
    case "cars-plan":
        guard exactKeys(obj, "ev measured approved volumes cars"), let row = try? decoder.decode(CarPlan.self, from: data),
              let raw = obj["volumes"] as? [[String: Any]], raw.allSatisfy({ exactKeys($0,
                  "device paths extraction_bytes staging_bytes output_bytes retention_bytes margin_bytes available_bytes required_bytes sufficient") }),
              let cars = obj["cars"] as? [[String: Any]], cars.allSatisfy({ exactKeys($0, "slot name car_hash approved reused reason") }),
              row.cars.allSatisfy({ (0...75).contains($0.slot) && $0.car_hash.count == 8
                  && $0.car_hash.allSatisfy { "0123456789abcdef".contains($0) } }),
              row.volumes.allSatisfy({ v in
                  [v.extraction_bytes, v.staging_bytes ?? 0, v.output_bytes ?? 0, v.retention_bytes,
                   v.margin_bytes, v.available_bytes, v.required_bytes ?? 0].allSatisfy { $0 >= 0 }
              }) else { return nil }
        return .carsPlan(row)
    case "cars-progress":
        guard exactKeys(obj, "ev phase slot done total"), let row = try? decoder.decode(CarProgress.self, from: data),
              ["extracted", "converted", "prepared", "verification"].contains(row.phase),
              (-1...75).contains(row.slot), row.done >= 0, row.total >= 0 else { return nil }
        return .carsProgress(row)
    case "cars-result":
        guard exactKeys(obj, "ev operation verified generation message selection cars generations"),
              let row = try? decoder.decode(CarResult.self, from: data),
              ["check", "import", "list", "remove", "restore"].contains(row.operation),
              row.generation.isEmpty || validGeneration(row.generation),
              !["import", "remove", "restore"].contains(row.operation) || validGeneration(row.generation),
              row.selection.allSatisfy({ (0...75).contains($0) }),
              let raw = obj["cars"] as? [[String: Any]], raw.allSatisfy({ exactKeys($0, "slot name state selected") }),
              let gens = obj["generations"] as? [[String: Any]], gens.allSatisfy({ exactKeys($0, "generation verified") }),
              row.cars.allSatisfy({ (0...89).contains($0.slot) && ["ready", "absent"].contains($0.state) }),
              row.generations.allSatisfy({ !$0.verified || validGeneration($0.generation) }) else { return nil }
        return .carsResult(row)
    default: return nil
    }
}

/// Splits a byte stream into lines however the reads are cut (a JSON line may arrive in several pieces).
struct LineSplitter {
    private var pending = Data()
    mutating func feed(_ chunk: Data) -> [String] {
        pending.append(chunk)
        var out: [String] = []
        while let nl = pending.firstIndex(of: 0x0A) {
            out.append(String(decoding: pending[pending.startIndex..<nl], as: UTF8.self))
            pending = Data(pending[pending.index(after: nl)...])
        }
        return out
    }
    /// The last line when the stream ended without a newline.
    mutating func flush() -> String? {
        defer { pending = Data() }
        return pending.isEmpty ? nil : String(decoding: pending, as: UTF8.self)
    }
}

struct FailureInfo: Equatable {
    var stage: String
    var message: String
    var fix: String
    var log: String
}

enum Outcome: Equatable {
    case success(app: String?)      // app is nil for a check (nothing was built)
    case cancelled
    case failed(FailureInfo)
}

enum RunMode: String {
    case check, build, exportApp, carsCheck, carsImport, carsList, carsRemove, carsRestore
    var carOperation: String? {
        switch self {
        case .carsCheck: return "check"
        case .carsImport: return "import"
        case .carsList: return "list"
        case .carsRemove: return "remove"
        case .carsRestore: return "restore"
        default: return nil
        }
    }
}

struct RunState {
    var currentStage = ""
    var currentTitle = ""
    var seen: [String] = []
    var progress: [String: Double] = [:]
    var warnings: [String] = []
    var error: FailureInfo?
    var features: (x360: Bool, missing: [String])?
    var appPath: String?
    var recognition: CarRecognition?
    var carPlan: CarPlan?
    var carProgress: CarProgress?
    var carResult: CarResult?

    mutating func apply(_ ev: SetupEvent) {
        switch ev {
        case let .stage(id, title):
            currentStage = id; currentTitle = title
            if !seen.contains(id) { seen.append(id) }
        case let .progress(id, done, total):
            if total > 0 { progress[id] = max(0, min(1, Double(done) / Double(total))) }
        case let .warn(id, msg): warnings.append("\(id): \(msg)")
        case let .error(id, msg, fix, log): error = FailureInfo(stage: id, message: msg, fix: fix, log: log)
        case let .features(x360, missing): features = (x360, missing)
        case let .done(app): appPath = app.isEmpty ? nil : app
        case let .carsRecognition(row): recognition = row
        case let .carsPlan(row): carPlan = row
        case let .carsProgress(row): carProgress = row
        case let .carsResult(row): carResult = row
        }
    }

    /// 0...1 over the stage table: finished stages plus the running stage's own progress.
    var overall: Double {
        guard let i = stageTable.firstIndex(where: { $0.id == currentStage }) else { return 0 }
        return min(1, (Double(i) + (progress[currentStage] ?? 0)) / Double(stageTable.count))
    }

    /// The only place a run becomes a success. An exit status of 0 alone is not enough for a build: the backend must
    /// have said "done" with an app path, and that app must exist. Any error event fails the run whatever the status.
    func outcome(mode: RunMode, status: Int32, cancelled: Bool, stderrTail: String = "",
                 appExists: (String) -> Bool) -> Outcome {
        // A mutating extras result is emitted only after F-T commits. A late
        // click on Cancel cannot turn that completed transaction into failure.
        if let operation = mode.carOperation, ["import", "remove", "restore"].contains(operation),
           status == 0, error == nil, let result = carResult,
           result.operation == operation, result.verified, validGeneration(result.generation) {
            return .success(app: nil)
        }
        if cancelled { return .cancelled }
        if let e = error { return .failed(e) }
        guard status == 0 else {
            let tail = stderrTail.isEmpty ? "The setup stopped (exit \(status))." : stderrTail
            return .failed(FailureInfo(stage: currentStage, message: tail,
                                       fix: "Open the log to see why, then try again.", log: ""))
        }
        if let operation = mode.carOperation {
            if let result = carResult, result.operation == operation, !result.verified,
               operation != "list", !result.message.isEmpty {
                return .failed(FailureInfo(stage: "cars", message: result.message,
                                           fix: "Retry or skip extras. Your previous catalog is retained.", log: ""))
            }
            guard let result = carResult, result.operation == operation, (operation == "list" || result.verified) else {
                return .failed(FailureInfo(stage: "cars", message: "The extras operation ended without a verified result.",
                                           fix: "Retry or skip extras. Your previous catalog is retained.", log: ""))
            }
            return .success(app: nil)
        }
        if mode == .check { return .success(app: nil) }
        guard let app = appPath else {
            return .failed(FailureInfo(stage: currentStage,
                                       message: "The setup ended without installing the app.",
                                       fix: "Open the log to see why, then try again.", log: ""))
        }
        guard appExists(app) else {
            return .failed(FailureInfo(stage: currentStage,
                                       message: "The setup reported success, but the app is not at \(app).",
                                       fix: "Open the log, then try again.", log: ""))
        }
        return .success(app: app)
    }
}

/// The backend's environment is built from nothing but what it needs: no inherited NFS_*, BOOT_* or Python settings.
func childEnvironment(inherited: [String: String], python: String, roots: String) -> [String: String] {
    var env: [String: String] = [:]
    for (k, v) in inherited {
        if k == "HOME" || k == "TMPDIR" || k == "USER" || k == "LANG" || k.hasPrefix("LC_") || k.hasPrefix("NFSMW_SETUP_") {
            env[k] = v
        }
    }
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    env["TOOLS_PY"] = python
    env["NFS_ROOTS"] = roots
    env["NFS_PUBLIC"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env
}

// MARK: - Paths and probes

// NFSMW_SETUP_HOME (tests) relocates everything the same way tools/setup_kit/backend.py does: <home>/state, <home>/logs.
func setupHome() -> URL? {
    if let o = ProcessInfo.processInfo.environment["NFSMW_SETUP_HOME"], !o.isEmpty { return URL(fileURLWithPath: o) }
    return nil
}
func supportDir() -> URL {
    if let h = setupHome() { return h.appendingPathComponent("state") }
    return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/NFSMW-Setup")
}
func logsDir() -> URL {
    if let h = setupHome() { return h.appendingPathComponent("logs") }
    return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/NFSMW-Setup")
}
func sourceDir() -> URL { supportDir().appendingPathComponent("source") }
func workDir() -> URL { supportDir().appendingPathComponent("work") }

@discardableResult
func runQuick(_ exe: String, _ args: [String]) -> (status: Int32, output: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: exe)
    p.arguments = args
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = pipe
    do { try p.run() } catch { return (-1, "\(error)") }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return (p.terminationStatus, String(decoding: data, as: UTF8.self))
}

func hasCommandLineTools() -> Bool {
    runQuick("/usr/bin/xcode-select", ["-p"]).status == 0 && runQuick("/usr/bin/xcrun", ["--find", "clang"]).status == 0
}

func freeSpaceGB(at url: URL) -> Double? {
    var probe = url
    while !FileManager.default.fileExists(atPath: probe.path), probe.pathComponents.count > 1 { probe.deleteLastPathComponent() }
    guard let v = try? probe.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]),
          let bytes = v.volumeAvailableCapacityForImportantUsage else { return nil }
    return Double(bytes) / 1_000_000_000
}

func bundledPython() -> String {
    if let o = ProcessInfo.processInfo.environment["NFSMW_SETUP_PYTHON"], !o.isEmpty { return o }
    return Bundle.main.resourceURL?.appendingPathComponent("python/bin/python3").path ?? "/usr/bin/python3"
}

func bundledVersion() -> String {
    guard let url = Bundle.main.url(forResource: "source-version", withExtension: "txt"),
          let s = try? String(contentsOf: url, encoding: .utf8) else { return "dev" }
    return s.trimmingCharacters(in: .whitespacesAndNewlines)
}

/// This pin stays in the signed app; the extracted source cache is writable.
func bundledPrebuiltDigest() -> String? {
    guard let url = Bundle.main.url(forResource: "prebuilt-manifest", withExtension: "sha256"),
          let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
    let digest = text.trimmingCharacters(in: .whitespacesAndNewlines)
    return digest.count == 64 && digest.allSatisfy { "0123456789abcdef".contains($0) } ? digest : nil
}

func copyOptionalPrebuilt(from source: URL, to destination: URL) {
    let fm = FileManager.default
    do {
        try fm.copyItem(at: source, to: destination)
    } catch {
        // A partial copy must not prevent the required source build.
        try? fm.removeItem(at: destination)
    }
}

/// Refresh the source when its version or signed library pin changes.
func prepareSource() throws {
    let fm = FileManager.default
    let version = bundledVersion()
    let identity = version + "\nprebuilt=" + (bundledPrebuiltDigest() ?? "source")
    let src = sourceDir()
    let marker = src.appendingPathComponent(".source-version")
    if (try? String(contentsOf: marker, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines) == identity,
       fm.fileExists(atPath: src.appendingPathComponent("tools/setup.py").path) { return }
    guard let archive = Bundle.main.url(forResource: "source", withExtension: "tar.gz") else {
        throw NSError(domain: "Setup", code: 1, userInfo: [NSLocalizedDescriptionKey:
            "This Setup app is incomplete (no source inside). Download it again."])
    }
    let tmp = supportDir().appendingPathComponent("source.new")
    try? fm.removeItem(at: tmp)
    try fm.createDirectory(at: tmp, withIntermediateDirectories: true)
    let r = runQuick("/usr/bin/tar", ["-xzf", archive.path, "-C", tmp.path])
    guard r.status == 0 else {
        throw NSError(domain: "Setup", code: 2, userInfo: [NSLocalizedDescriptionKey: "Could not unpack the setup: \(r.output)"])
    }
    if let dl = Bundle.main.resourceURL?.appendingPathComponent("third_party/dl"), fm.fileExists(atPath: dl.path) {
        let to = tmp.appendingPathComponent("third_party/dl")
        try fm.createDirectory(at: to, withIntermediateDirectories: true)
        for name in try fm.contentsOfDirectory(atPath: dl.path) {
            try fm.copyItem(at: dl.appendingPathComponent(name), to: to.appendingPathComponent(name))
        }
    }
    if let prebuilt = Bundle.main.resourceURL?.appendingPathComponent("prebuilt"), fm.fileExists(atPath: prebuilt.path) {
        copyOptionalPrebuilt(from: prebuilt, to: tmp.appendingPathComponent("prebuilt"))
    }
    try identity.write(to: tmp.appendingPathComponent(".source-version"), atomically: true, encoding: .utf8)
    try? fm.removeItem(at: src)
    try fm.createDirectory(at: supportDir(), withIntermediateDirectories: true)
    try fm.moveItem(at: tmp, to: src)
}

struct X360Extras {
    var arrest = true
    var textures = true
    var ui = true
    var names: [String] { [("arrest", arrest), ("textures", textures), ("ui", ui)].filter { $0.1 }.map { $0.0 } }
    var bytes: Int { (arrest ? 239_000_000 : 0) + (textures ? 253_000_000 : 0) + (ui ? 39_000_000 : 0) }
    var summary: String {
        let labels = ["arrest": "Arrest scenes", "textures": "HD textures", "ui": "Menu art"]
        return names.isEmpty ? "None (PC assets)" : names.compactMap { labels[$0] }.joined(separator: ", ")
    }
    func requiredGB(hasX360: Bool) -> Double {
        let shared = textures && !arrest ? 146_000_000 : 0
        return requiredFreeGB - Double(531_000_000 - (hasX360 ? bytes + shared : 0)) / 1_000_000_000
    }
    static func load(_ data: Data?) -> X360Extras {
        guard let data, let saved = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let names = saved["x360_extras"] as? [String], Set(names).count == names.count,
              names.allSatisfy({ ["arrest", "textures", "ui"].contains($0) }) else { return X360Extras() }
        return X360Extras(arrest: names.contains("arrest"), textures: names.contains("textures"), ui: names.contains("ui"))
    }
}

func backendArguments(mode: RunMode, pc: URL, x360: URL?, dest: URL, work: URL,
                      pcUpdate: URL? = nil, pcExe: URL? = nil, pcDiscs: [URL] = [], x360Extras: X360Extras? = nil) -> [String] {
    if mode == .exportApp {
        return ["-I", sourceDir().appendingPathComponent("tools/setup.py").path,
                "--export-all-in-one", "--app", pc.path, "--dest", dest.path, "--work", work.path, "--events"]
    }
    var a = ["-I", sourceDir().appendingPathComponent("tools/setup.py").path,
             mode == .check ? "--check" : "--build", "--pc", pc.path]
    if let u = pcUpdate { a += ["--pc-update", u.path] }
    if let e = pcExe { a += ["--pc-exe", e.path] }
    for d in pcDiscs { a += ["--pc-disc", d.path] }
    if let x = x360 { a += ["--x360", x.path] }
    if let extras = x360Extras { a += ["--x360-extras", extras.names.joined(separator: ",")] }
    a += ["--dest", dest.path, "--work", work.path, "--events"]
    if let digest = bundledPrebuiltDigest() { a += ["--prebuilt-manifest-sha256", digest] }
    return a
}

// Generated from the policy-validated metadata by test_car_release.py --generate-selections.
let carSelectionsSHA256 = "7deace4b65e40706c1907e625228ffd4d516c4b8046474a53b5e4fa093442107"

struct CarChoice: Identifiable {
    let platform: String
    let slot: Int
    let folder: String
    let spec: [String: Any]
    var id: String { platform + ":" + String(slot) }
    var name: String {
        let names = ["300C": "Chrysler 300C", "997GT3RS": "Porsche 911 GT3 RS",
                     "BRERA": "Alfa Romeo Brera", "CCX": "Koenigsegg CCX",
                     "CORVETTEZ06": "Chevrolet Corvette Z06", "EUROPA": "Lotus Europa S",
                     "JAGUARXK": "Jaguar XK", "MR2": "Toyota MR2", "R32": "Volkswagen Golf R32", "R8": "Audi R8"]
        return names[folder] ?? folder
    }
    var carHash: String {
        var hash: UInt32 = 0xffffffff
        for byte in ("CARS/" + folder).replacingOccurrences(of: "/", with: "\\").utf8 {
            hash = hash &* 33 &+ UInt32(byte)
        }
        return String(format: "%08x", hash)
    }
    func reason(_ recognition: CarRecognition?) -> String? {
        guard let recognition else { return "Choose a Carbon source to check availability." }
        guard recognition.platform == platform else {
            return "Needs a Carbon " + (platform == "pc" ? "PC" : "Xbox 360") + " source; skipped."
        }
        let status = recognition.cars.first { $0.car_hash == carHash }?.status
        if status == "importable" { return nil }
        return status == "unknown" ? "Source files differ from the reviewed copy; skipped." : "Missing from this Carbon copy; skipped."
    }
    static func load(_ url: URL) throws -> [CarChoice] {
        let data = try Data(contentsOf: url)
        guard SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == carSelectionsSHA256,
              let rows = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            throw NSError(domain: "Setup", code: 1, userInfo: [NSLocalizedDescriptionKey: "The bundled car selections failed verification."])
        }
        return try rows.map { row in
            guard let origin = row["origin"] as? [String: String], let platform = origin["platform"],
                  let folder = origin["folder"], let slot = row["slot"] as? Int else {
                throw NSError(domain: "Setup", code: 1, userInfo: [NSLocalizedDescriptionKey: "The bundled car selections are incomplete."])
            }
            var spec = row.filter { ["slot", "display", "bindings", "donors", "parts"].contains($0.key) }
            spec["folder"] = folder
            return CarChoice(platform: platform, slot: slot, folder: folder, spec: spec)
        }
    }
}

func writeCarSelection(choices: [CarChoice], selected: Set<String>, recognition: CarRecognition?, work: URL) throws -> URL? {
    let rows = choices.filter { selected.contains($0.id) && $0.reason(recognition) == nil }
        .sorted { $0.slot < $1.slot }.map { $0.spec }
    guard !rows.isEmpty else { return nil }
    try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
    let url = work.appendingPathComponent("car-selection.json")
    try JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys]).write(to: url, options: .atomic)
    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    return url
}

func carArguments(mode: RunMode, carbon: URL?, selection: URL?, work: URL, mw: URL? = nil,
                  slots: [Int] = [], generation: String? = nil, measure: Bool = false, repair: Bool = false) -> [String] {
    guard let operation = mode.carOperation else { return [] }
    if operation == "import" && selection == nil { return [] }
    var args = ["-I", sourceDir().appendingPathComponent("tools/setup.py").path, "--cars-" + operation]
    if operation == "remove" { args += slots.map(String.init) }
    if operation == "restore", let generation { args.append(generation) }
    if operation == "check" || operation == "import" {
        if let carbon { args += ["--carbon", carbon.path] }
        if let selection { args += ["--cars-spec", selection.path] }
        if let mw { args += ["--cars-mw-pc", mw.path] }
        if measure { args.append("--cars-measure") }
        if repair { args.append("--cars-repair") }
    }
    args += ["--work", work.appendingPathComponent("extras").path, "--events"]
    return args
}

/// Keep optional work separate from the install result, including retry and skip.
struct ExtrasFlow {
    var busy = false
    var skipped = false
    var completed = false
    var message = "Choose a Carbon source, or skip extras."
    mutating func begin() { busy = true; skipped = false; completed = false; message = "Checking extras…" }
    mutating func finish(_ outcome: Outcome) {
        busy = false
        switch outcome {
        case .success: completed = true; message = "Extras operation completed."
        case .cancelled: completed = false; message = "Extras cancelled. Retry or skip."
        case let .failed(f): completed = false; message = f.message + "\n" + f.fix
        }
    }
    mutating func skip() { guard !busy else { return }; skipped = true; message = "Extras skipped." }
}

/// The disc numbers a failed check could not find ("Disc 2, 3 of the set (4 discs) was not found ..."), so the page can
/// offer a choice for each; empty for any other message.
func missingDiscNumbers(_ message: String) -> [Int] {
    let start = message.index(message.startIndex, offsetBy: 5, limitedBy: message.endIndex) ?? message.endIndex
    guard message.hasPrefix("Disc "), let end = message.range(of: " of the set", range: start..<message.endIndex)
    else { return [] }  // searched after the prefix: a disc named "Disc of the set.iso" is no list
    let list = message[start..<end.lowerBound]
    let numbers = list.split(separator: ",").map { Int($0.trimmingCharacters(in: .whitespaces)) }
    return numbers.contains(nil) ? [] : numbers.compactMap { $0 }
}

// MARK: - Child process in its own process group

final class ChildProcess {
    let pid: pid_t
    private let outFD: Int32, errFD: Int32

    init(exe: String, args: [String], env: [String: String], cwd: String?) throws {
        var o: [Int32] = [0, 0], e: [Int32] = [0, 0]
        guard pipe(&o) == 0, pipe(&e) == 0 else { throw NSError(domain: "Setup", code: 3) }
        var fa: posix_spawn_file_actions_t?
        posix_spawn_file_actions_init(&fa)
        posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0)
        posix_spawn_file_actions_adddup2(&fa, o[1], 1)
        posix_spawn_file_actions_adddup2(&fa, e[1], 2)
        for fd in [o[0], o[1], e[0], e[1]] { posix_spawn_file_actions_addclose(&fa, fd) }
        if let cwd { posix_spawn_file_actions_addchdir_np(&fa, cwd) }
        var attr: posix_spawnattr_t?
        posix_spawnattr_init(&attr)
        posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETPGROUP))
        posix_spawnattr_setpgroup(&attr, 0)        // a new group led by the child: Cancel signals all of it
        let argv = ([exe] + args).map { strdup($0) } + [nil]
        let envp = env.map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer { argv.forEach { free($0) }; envp.forEach { free($0) } }
        var child: pid_t = 0
        let rc = posix_spawn(&child, exe, &fa, &attr, argv, envp)
        posix_spawn_file_actions_destroy(&fa)
        posix_spawnattr_destroy(&attr)
        close(o[1]); close(e[1])
        guard rc == 0 else {
            close(o[0]); close(e[0])
            throw NSError(domain: "Setup", code: Int(rc), userInfo: [NSLocalizedDescriptionKey:
                "Could not start the setup (\(String(cString: strerror(rc)))): \(exe)"])
        }
        pid = child; outFD = o[0]; errFD = e[0]
    }

    /// Reads both pipes on background threads, then waits for the child; `done` runs once on the main queue.
    func run(onOut: @escaping (Data) -> Void, onErr: @escaping (Data) -> Void, done: @escaping (Int32) -> Void) {
        let group = DispatchGroup()
        func pump(_ fd: Int32, _ sink: @escaping (Data) -> Void) {
            group.enter()
            DispatchQueue.global().async {
                var buf = [UInt8](repeating: 0, count: 65536)
                while true {
                    let n = read(fd, &buf, buf.count)
                    if n <= 0 { break }
                    sink(Data(buf[0..<n]))
                }
                close(fd); group.leave()
            }
        }
        pump(outFD, onOut); pump(errFD, onErr)
        let pid = self.pid
        DispatchQueue.global().async {
            var st: Int32 = 0
            while waitpid(pid, &st, 0) < 0 && errno == EINTR {}
            let code: Int32 = (st & 0x7f) == 0 ? (st >> 8) & 0xff : 128 + (st & 0x7f)
            group.notify(queue: .main) { done(code) }
        }
    }

    /// Stops the whole group: TERM now, KILL after a grace period if anything is left and `forceAllowed()` still
    /// says so then (checked on the main queue at the deadline, so a run that has entered the install meanwhile is
    /// left to finish its rollback).
    func cancel(grace: Double = 5, forceAllowed: @escaping () -> Bool = { true }) {
        killpg(pid, SIGTERM)
        let g = pid
        DispatchQueue.main.asyncAfter(deadline: .now() + grace) { if forceAllowed() { killpg(g, SIGKILL) } }
    }
}

/// No SIGKILL during the install transaction: after TERM the backend is putting the previous app, roots and
/// preferences back and must be allowed to finish (an interrupted one is still recovered by the next run's journal).
func forceStopAllowed(stage: String) -> Bool { stage != "install" && stage != "cars" }

enum QuitPlan: String { case now, cancelAndWait = "cancel-wait", askThenCancelAndWait = "ask-cancel-wait" }

/// Quitting with any backend alive (a check or a build) cancels it and waits for it to exit; a build asks first.
func quitPlan(childActive: Bool, building: Bool) -> QuitPlan {
    guard childActive else { return .now }
    return building ? .askThenCancelAndWait : .cancelAndWait
}

// MARK: - App model

enum Page: Int { case welcome, pc, x360, extras, review, build, finished }

/// An installed game folder (or its .zip), or the PC disc images, which also take the 1.3 update and, as both carry a
/// copy-protected speed.exe, a speed.exe that has the 1.3 code.
enum PCKind: Int { case folder, disc }

final class Setup: ObservableObject {
    static let shared = Setup()

    @Published var showReadMe = false
    @Published var page: Page = .welcome
    @Published var tools = hasCommandLineTools()
    @Published var installingTools = false
    @Published var pcKind = PCKind.folder
    @Published var pc: URL?
    @Published var pcUpdate: URL?
    @Published var pcExe: URL?
    @Published var pcDiscs: [Int: URL] = [:]   // discs chosen one by one (not beside the first), by the number asked for
    @Published var missingDiscs: [Int] = []
    @Published var pcStatus = ""
    @Published var pcOK = false
    @Published var x360: URL?
    @Published var x360Status = ""
    @Published var x360Extras = X360Extras.load(try? Data(contentsOf: supportDir().appendingPathComponent("source-choices.json")))
    @Published var carbon: URL?
    @Published var carChoices: [CarChoice] = []
    @Published var selectedCarIDs: Set<String> = []
    @Published var extras = ExtrasFlow()
    @Published var managingExtras = false
    @Published var installedCars: CarResult?
    @Published var features: (x360: Bool, missing: [String])?
    @Published var dest: URL
    @Published var checking = false
    @Published var running = false
    @Published var exporting = false
    @Published var stopping = false
    @Published var state = RunState()
    @Published var outcome: Outcome?
    @Published var lines: [String] = []
    @Published var started = Date()

    private var child: ChildProcess?
    private var activity: NSObjectProtocol?
    private var cancelled = false
    private var whenStopped: [() -> Void] = []
    private var splitter = LineSplitter()
    private var stderrTail: [String] = []
    private var lastLog = ""

    init() {
        let apps = URL(fileURLWithPath: "/Applications")
        dest = FileManager.default.isWritableFile(atPath: apps.path) ? apps
            : FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications")
    }

    var installedApp: URL { dest.appendingPathComponent("NFS Most Wanted Native.app") }
    var freeGB: Double? { freeSpaceGB(at: dest) }
    var requiredGB: Double { x360Extras.requiredGB(hasX360: x360 != nil) }
    var enoughSpace: Bool { (freeGB ?? requiredGB) >= requiredGB }

    func pickFolder(message: String, files: Bool = false, types: [UTType] = [], folders: Bool = true) -> URL? {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = folders
        panel.canChooseFiles = files
        if !types.isEmpty { panel.allowedContentTypes = types }
        panel.allowsMultipleSelection = false
        panel.message = message
        panel.prompt = "Choose"
        return panel.runModal() == .OK ? panel.url : nil
    }

    // A file inside the game folder (speed.exe) stands for that folder; a .zip stays itself.
    func pcRoot(_ url: URL) -> URL {
        var dir: ObjCBool = false
        if FileManager.default.fileExists(atPath: url.path, isDirectory: &dir), !dir.boolValue,
           url.pathExtension.lowercased() != "zip" { return url.deletingLastPathComponent() }
        return url
    }

    func choosePC() {
        // The game folder, or a .zip of it (Setup unpacks a copy; the .zip is only read).
        guard var url = pickFolder(message: "Choose the Need for Speed: Most Wanted folder (the one with speed.exe), speed.exe itself, or a .zip of that folder.",
                                   // .folder too: with allowedContentTypes set, the panel greys out "Choose" for any
                                   // folder that is not listed (issue #19: the 3.x game folder could not be chosen).
                                   // .exe: once inside the game folder, "Choose" stays grey until a file is picked,
                                   // and players then pick speed.exe.
                                   files: true, types: [.folder, .zip, .exe]) else { return }
        url = pcRoot(url)
        pc = url; features = nil; x360 = nil; x360Status = ""
        runCheck(forX360: false)
    }

    func chooseKind(_ kind: PCKind) {
        guard kind != pcKind else { return }
        pcKind = kind; pc = nil; pcUpdate = nil; pcExe = nil; pcDiscs = [:]; missingDiscs = []; pcStatus = ""; pcOK = false
        features = nil
    }

    func chooseDisc() {
        // An .iso, or a disc mounted in Finder (a folder); the set's other discs are found beside it.
        let iso = [UTType(filenameExtension: "iso")].compactMap { $0 }
        guard let url = pickFolder(message: "Choose disc 1 of your PC game: its .iso image, or the disc mounted in Finder. Keep the images of the other discs in the same folder.",
                                   files: true, types: [.folder] + iso) else { return }
        pc = url; pcDiscs = [:]; missingDiscs = []; features = nil; x360 = nil; x360Status = ""
        runCheck(forX360: false)
    }

    /// A disc that is not beside the first one (another folder or drive). The backend reads its number from the disc.
    func chooseOtherDisc(_ number: Int) {
        let iso = [UTType(filenameExtension: "iso")].compactMap { $0 }
        guard let url = pickFolder(message: "Choose disc \(number) of your PC game: its .iso image, or the disc mounted in Finder.",
                                   files: true, types: [.folder] + iso) else { return }
        pcDiscs[number] = url
        if pc != nil { runCheck(forX360: false) }
    }

    var otherDiscRows: [Int] { Array(Set(missingDiscs).union(pcDiscs.keys)).sorted() }

    func chooseUpdate() {
        guard let url = pickFolder(message: "Choose the PC game's official 1.3 update (nfsmwpatch1.3.exe). Setup only reads it; it is never run.",
                                   files: true, types: [.exe]) else { return }
        pcUpdate = url
        if pc != nil { runCheck(forX360: false) }
    }

    func chooseExe() {
        guard let url = pickFolder(message: "Choose a speed.exe that has the version 1.3 code. The one on the discs and in the update is copy-protected.",
                                   files: true, types: [.exe]) else { return }
        pcExe = url
        if pc != nil { runCheck(forX360: false) }
    }

    func chooseX360() {
        guard let url = pickFolder(message: "Choose your Xbox 360 copy: its folder, or the .iso file.", files: true) else { return }
        x360 = url
        features = nil
        x360Status = "Copy selected. Your chosen extras will be checked when building."
    }

    func chooseCarbon() {
        guard !busy, let url = pickFolder(message: "Choose your Carbon game folder or its Xbox 360 ISO.", files: true) else { return }
        carbon = url
        state.recognition = nil
        runCars(.carsCheck)
    }

    func loadCarChoices() {
        do {
            try prepareSource()
            carChoices = try CarChoice.load(sourceDir().appendingPathComponent("tools/setup_kit/car_selections.json"))
        } catch { carChoices = []; extras.message = error.localizedDescription }
    }

    func selectCar(_ car: CarChoice, enabled: Bool) {
        if enabled { selectedCarIDs.insert(car.id) } else { selectedCarIDs.remove(car.id) }
        state.carPlan = nil
    }

    var availableCarSelection: Bool {
        carChoices.contains { selectedCarIDs.contains($0.id) && $0.reason(state.recognition) == nil }
    }

    func runCars(_ mode: RunMode, slots: [Int] = [], generation: String? = nil, measure: Bool = false, repair: Bool = false) {
        guard !busy else { return }
        let requiresSource = mode == .carsCheck || mode == .carsImport
        let selection: URL?
        do {
            selection = requiresSource ? try writeCarSelection(choices: carChoices, selected: selectedCarIDs,
                                                               recognition: state.recognition, work: workDir()) : nil
        } catch { extras.message = error.localizedDescription; return }
        if mode == .carsImport && selection == nil {
            extras.message = "No available cars are ticked. No cars were imported."
            return
        }
        extras.begin(); checking = true
        // The preceding PC check has already unpacked a ZIP/disc into pc_src.
        let mw = managingExtras ? nil : (pcKind == .disc || pc?.pathExtension.lowercased() == "zip"
            ? workDir().appendingPathComponent("pc_src") : pc)
        let args = carArguments(mode: mode, carbon: carbon, selection: selection,
                                work: workDir(), mw: requiresSource && selection != nil ? mw : nil,
                                slots: slots, generation: generation, measure: measure, repair: repair)
        launch(mode: mode, pc: pc ?? dest, x360: nil, arguments: args) { [weak self] outcome, state in
            guard let self else { return }
            self.checking = false
            self.extras.finish(outcome)
            if case .success = outcome {
                if let result = state.carResult, mode == .carsList { self.installedCars = result }
                if mode == .carsCheck, let recognition = state.recognition {
                    let count = recognition.cars.filter { $0.status == "importable" }.count
                    self.extras.message = "Recognized Carbon \(recognition.platform.uppercased()): \(count) verified source pairs. "
                        + (state.carPlan?.approved == true ? "Selected outputs passed packaged approval. Import to activate them."
                           : recognition.approval_available ? "Packaged approval is available; selected outputs must still verify."
                           : "No packaged output approval is available. Import is unavailable.")
                }
            }
        }
    }

    func skipExtras() {
        guard !busy else { return }
        extras.skip(); page = managingExtras ? .welcome : .review
    }

    func manageExtras() { managingExtras = true; page = .extras; runCars(.carsList) }

    func chooseDest() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.canCreateDirectories = true
        panel.directoryURL = dest
        panel.message = "Where should the game app go?"
        panel.prompt = "Choose"
        if panel.runModal() == .OK, let url = panel.url { dest = url }
    }

    func installTools() {
        runQuick("/usr/bin/xcode-select", ["--install"])
        installingTools = true
        Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] t in
            guard let self else { t.invalidate(); return }
            if hasCommandLineTools() { self.tools = true; self.installingTools = false; t.invalidate() }
        }
    }

    // One backend run, check or build; all state changes happen on the main queue.
    private func launch(mode: RunMode, pc: URL, x360: URL?, destination: URL? = nil,
                        arguments: [String]? = nil,
                        finish: @escaping (Outcome, RunState) -> Void) {
        let previous = state
        state = RunState(); lines = []; stderrTail = []; splitter = LineSplitter(); cancelled = false; stopping = false
        if mode == .carsList {
            state.recognition = previous.recognition
            state.carPlan = previous.carPlan
        }
        let python = bundledPython()
        let work = workDir()
        do {
            try prepareSource()
            try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
            let env = childEnvironment(inherited: ProcessInfo.processInfo.environment, python: python,
                                       roots: work.appendingPathComponent("roots.txt").path)
            let disc = pcKind == .disc
            let c = try ChildProcess(exe: python, args: arguments ?? backendArguments(mode: mode, pc: pc, x360: x360, dest: destination ?? dest, work: work,
                                                                       pcUpdate: disc ? pcUpdate : nil, pcExe: disc ? pcExe : nil,
                                                                       pcDiscs: disc ? pcDiscs.keys.sorted().compactMap { pcDiscs[$0] } : [], x360Extras: x360Extras),
                                     env: env, cwd: sourceDir().path)
            child = c
            c.run(onOut: { [weak self] data in
                DispatchQueue.main.async { self?.consume(self?.splitter.feed(data) ?? []) }
            }, onErr: { [weak self] data in
                let text = String(decoding: data, as: UTF8.self)
                DispatchQueue.main.async {
                    self?.stderrTail.append(contentsOf: text.split(separator: "\n").map(String.init))
                    if let n = self?.stderrTail.count, n > 40 { self?.stderrTail.removeFirst(n - 40) }
                }
            }, done: { [weak self] status in
                guard let self else { return }
                if let rest = self.splitter.flush() { self.consume([rest]) }
                self.child = nil
                let o = self.state.outcome(mode: mode, status: status, cancelled: self.cancelled,
                                           stderrTail: self.stderrTail.suffix(4).joined(separator: "\n"),
                                           appExists: { FileManager.default.fileExists(atPath: $0) })
                finish(o, self.state)
                self.stopping = false
                let waiting = self.whenStopped
                self.whenStopped = []
                waiting.forEach { $0() }
            })
        } catch {
            let f = FailureInfo(stage: "", message: error.localizedDescription, fix: "Download Setup again.", log: "")
            finish(.failed(f), state)
        }
    }

    func consume(_ newLines: [String]) {
        for line in newLines where !line.isEmpty {
            lines.append(line)
            if let ev = parseEvent(line) {
                state.apply(ev)
                if case let .error(_, _, _, log) = ev, !log.isEmpty { lastLog = log }
            }
        }
        if lines.count > 3000 { lines.removeFirst(lines.count - 3000) }
    }

    func runCheck(forX360: Bool) {
        guard let pc else { return }
        checking = true
        if forX360 { x360Status = "Checking…" } else { pcStatus = "Checking…"; pcOK = false }
        launch(mode: .check, pc: pc, x360: forX360 ? x360 : nil) { [weak self] outcome, st in
            guard let self else { return }
            self.checking = false
            if let f = st.features { self.features = f }
            switch outcome {
            case .success:
                if forX360 { self.x360Status = "Your Xbox 360 copy can be used." }
                else {
                    self.pcStatus = self.pcKind == .disc ? "Your disc images can be used."
                        : pc.pathExtension.lowercased() == "zip" ? "Your game .zip can be used." : "Your game folder can be used."
                    // Notes on the game itself, e.g. no MOVIES folder (backend movie_note).
                    for note in st.warnings where note.hasPrefix("pc: ") { self.pcStatus += "\n" + note.dropFirst(4) }
                    self.pcOK = true
                }
            case let .failed(f):
                let text = [f.message, f.fix].filter { !$0.isEmpty }.joined(separator: "\n")
                if forX360 { self.x360Status = text; self.x360 = nil } else {
                    self.pcStatus = text; self.pcOK = false
                    if self.pcKind == .disc { self.missingDiscs = missingDiscNumbers(f.message) }
                }
            case .cancelled: break
            }
        }
    }

    func build() {
        guard let pc, !running else { return }
        exporting = false
        page = .build; running = true; outcome = nil; started = Date()
        activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .idleSystemSleepDisabled],
                                                         reason: "Building NFS Most Wanted Native")
        launch(mode: .build, pc: pc, x360: x360) { [weak self] outcome, _ in
            guard let self else { return }
            if let a = self.activity { ProcessInfo.processInfo.endActivity(a) }
            self.activity = nil
            self.running = false
            self.outcome = outcome
            if case .success = outcome { self.page = .finished }
        }
    }

    var busy: Bool { child != nil }

    func exportAllInOne() {
        guard !busy else { return }
        let source = NSOpenPanel()
        source.canChooseDirectories = false
        source.canChooseFiles = true
        source.allowedContentTypes = [.applicationBundle]
        source.allowsMultipleSelection = false
        source.directoryURL = URL(fileURLWithPath: installedPath).deletingLastPathComponent()
        source.message = "Choose the installed game app to export. Quit the game first."
        guard source.runModal() == .OK, let app = source.url else { return }
        let save = NSSavePanel()
        save.allowedContentTypes = [.applicationBundle]
        save.nameFieldStringValue = app.deletingPathExtension().lastPathComponent + " All-in-one.app"
        save.message = "Choose a new name. Keep the previous export to roll back. Saves and settings stay in Application Support."
        guard save.runModal() == .OK, let target = save.url else { return }
        exporting = true
        page = .build; running = true; outcome = nil; started = Date()
        activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .idleSystemSleepDisabled],
                                                         reason: "Exporting the all-in-one app")
        launch(mode: .exportApp, pc: app, x360: nil, destination: target) { [weak self] outcome, _ in
            guard let self else { return }
            if let a = self.activity { ProcessInfo.processInfo.endActivity(a) }
            self.activity = nil
            self.running = false
            self.outcome = outcome
            if case .success = outcome { self.page = .finished }
        }
    }

    func cancel() {
        guard let c = child else { return }
        cancelled = true
        stopping = true
        c.cancel(forceAllowed: { [weak self] in forceStopAllowed(stage: self?.state.currentStage ?? "") })
    }

    /// Cancels the running backend and calls `done` once it has exited; false when nothing is running.
    func stopThen(_ done: @escaping () -> Void) -> Bool {
        guard child != nil else { return false }
        whenStopped.append(done)
        cancel()
        return true
    }

    var installedPath: String { if case let .success(app?) = outcome { return app }; return installedApp.path }
    func openGame() { NSWorkspace.shared.open(URL(fileURLWithPath: installedPath)) }
    func showGame() { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: installedPath)]) }

    /// The run's own log when the backend named it, else the newest log in the logs folder.
    func showLog() {
        if !lastLog.isEmpty, FileManager.default.fileExists(atPath: lastLog) {
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: lastLog)]); return
        }
        let dir = logsDir()
        let newest = (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey]))?
            .max { ((try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast)
                 < ((try? $1.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast) }
        if let newest { NSWorkspace.shared.activateFileViewerSelecting([newest]) }
        else { NSWorkspace.shared.open(dir.deletingLastPathComponent()) }
    }

    func copyLog() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(lines.joined(separator: "\n"), forType: .string)
    }

    func elapsedText() -> String {
        let s = Int(Date().timeIntervalSince(started))
        return s >= 3600 ? String(format: "%d h %02d min", s / 3600, (s % 3600) / 60) : String(format: "%d min %02d s", s / 60, s % 60)
    }
}

// MARK: - Views

func readMeText() -> String {
    guard let url = Bundle.main.url(forResource: "ReadMe", withExtension: "txt"),
          let text = try? String(contentsOf: url, encoding: .utf8) else { return "Setup could not find its Read Me." }
    return text
}

struct ReadMeView: View {
    @Binding var shown: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ScrollView { Text(readMeText()).font(.system(size: 12, design: .monospaced)).textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading) }
            HStack { Spacer(); Button("Done") { shown = false }.keyboardShortcut(.defaultAction) }
        }.padding(20).frame(width: 720, height: 500)
    }
}

struct ContentView: View {
    @ObservedObject var s: Setup

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text("NFS Most Wanted Native Setup").font(.title).bold()
                Spacer()
                Button("Read Me") { s.showReadMe = true }
            }
            .sheet(isPresented: $s.showReadMe) { ReadMeView(shown: $s.showReadMe) }
            switch s.page {
            case .welcome: welcome
            case .pc: pcPage
            case .x360: x360Page
            case .extras: extrasPage
            case .review: review
            case .build: buildPage
            case .finished: finished
            }
        }
        .padding(20)
        .frame(width: 640)
    }

    func nav(back: Page?, next: Page?, nextEnabled: Bool, nextTitle: String = "Continue") -> some View {
        HStack {
            if let back { Button("Back") { s.page = back }.disabled(s.checking) }
            Spacer()
            if let next { Button(nextTitle) { if next == .pc { s.managingExtras = false }; s.page = next }.keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent).disabled(!nextEnabled || s.checking) }
        }
    }

    var welcome: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Builds the native Mac version of Need for Speed: Most Wanted (2005) on this Mac, from your own copy of the PC game. "
                 + "It contains no game files, everything happens on this Mac, and your game folder is never changed.")
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            GroupBox(label: Text("Apple's developer tools").bold()) {
                HStack {
                    if s.tools { Label("Installed.", systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
                    else if s.installingTools { Label("Finish Apple's installer window; this continues by itself.", systemImage: "hourglass") }
                    else { Text("Needed to build the game (free, from Apple).") }
                    Spacer()
                    if !s.tools && !s.installingTools { Button("Install…") { s.installTools() } }
                }.padding(4)
            }
            nav(back: nil, next: .pc, nextEnabled: s.tools)
            Button("Manage installed extras…") { s.manageExtras() }.disabled(s.busy)
            Button("Export all-in-one app…") { s.exportAllInOne() }.disabled(s.busy)
            Text("Back up an existing install with its game files inside one app. Re-export from Setup to update.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    var pcPage: some View {
        VStack(alignment: .leading, spacing: 12) {
            GroupBox(label: Text("Your PC game").bold()) {
                VStack(alignment: .leading, spacing: 8) {
                    Picker("", selection: Binding(get: { s.pcKind }, set: { s.chooseKind($0) })) {
                        Text("Installed PC game folder or .zip").tag(PCKind.folder)
                        Text("PC disc images").tag(PCKind.disc)
                    }.pickerStyle(.radioGroup).labelsHidden().disabled(s.checking)
                    if s.pcKind == .folder {
                        HStack {
                            Text(s.pc?.path ?? "The folder with speed.exe, or a .zip of it (version 1.3).")
                                .lineLimit(2).truncationMode(.middle).foregroundStyle(s.pc == nil ? .secondary : .primary)
                            Spacer()
                            Button("Choose…") { s.choosePC() }.disabled(s.checking)
                        }
                    } else {
                        Text("Setup installs the game from your discs into its own work folder, without Windows. It also needs the game's 1.3 update and, because the speed.exe on the discs and in the update is copy-protected, a speed.exe that has the 1.3 code.")
                            .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        choice("Discs", s.pc, "Disc 1 (.iso or mounted disc), the other discs beside it") { s.chooseDisc() }
                        ForEach(s.otherDiscRows, id: \.self) { n in
                            choice("Disc \(n)", s.pcDiscs[n], "Not found with the chosen disc: choose it") { s.chooseOtherDisc(n) }
                        }
                        choice("1.3 update", s.pcUpdate, "nfsmwpatch1.3.exe") { s.chooseUpdate() }
                        choice("speed.exe", s.pcExe, "A speed.exe with the version 1.3 code") { s.chooseExe() }
                    }
                    if !s.pcStatus.isEmpty {
                        Label(s.pcStatus, systemImage: s.pcOK ? "checkmark.circle.fill" : (s.checking ? "hourglass" : "exclamationmark.triangle.fill"))
                            .foregroundStyle(s.pcOK ? .green : (s.checking ? .secondary : .orange))
                            .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading).padding(4)
            }
            nav(back: .welcome, next: .x360, nextEnabled: s.pcOK)
        }
    }

    func choice(_ title: String, _ url: URL?, _ placeholder: String, _ action: @escaping () -> Void) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).bold().frame(width: 80, alignment: .leading)
            Text(url?.path ?? placeholder).lineLimit(2).truncationMode(.middle).foregroundStyle(url == nil ? .secondary : .primary)
            Spacer()
            Button("Choose…", action: action).disabled(s.checking)
        }
    }

    var x360Page: some View {
        VStack(alignment: .leading, spacing: 12) {
            GroupBox(label: Text("Your Xbox 360 copy (optional)").bold()) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("If you own the Xbox 360 version, choose its folder or .iso to add its extra features. Skip this to build with the PC game only.")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    HStack {
                        Text(s.x360?.path ?? "None chosen").lineLimit(2).truncationMode(.middle)
                            .foregroundStyle(s.x360 == nil ? .secondary : .primary)
                        Spacer()
                        if s.x360 != nil { Button("Remove") { s.x360 = nil; s.x360Status = ""; s.runCheck(forX360: false) }.disabled(s.checking) }
                        Button("Choose…") { s.chooseX360() }.disabled(s.checking)
                    }
                    if !s.x360Status.isEmpty {
                        Label(s.x360Status, systemImage: s.checking ? "hourglass" : (s.x360 != nil ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"))
                            .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                    }
                    featureList
                }.frame(maxWidth: .infinity, alignment: .leading).padding(4)
            }
            nav(back: .pc, next: .extras, nextEnabled: true)
        }
    }

    var extrasPage: some View {
        VStack(alignment: .leading, spacing: 10) {
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if !s.managingExtras {
                        GroupBox("Xbox 360 extras") {
                            VStack(alignment: .leading, spacing: 5) {
                                Toggle("Arrest scenes — 146 MB sources + about 93 MB scenes", isOn: $s.x360Extras.arrest)
                                Toggle("HD textures — about 253 MB", isOn: $s.x360Extras.textures)
                                Toggle("Menu art — about 39 MB", isOn: $s.x360Extras.ui)
                                Text(s.x360 == nil ? "Choose a Most Wanted Xbox 360 copy on the previous page to install these extras." : "Unticked extras use PC assets. Choices are saved when installation succeeds.")
                                    .font(.caption).foregroundStyle(.secondary)
                            }.frame(maxWidth: .infinity, alignment: .leading).padding(4)
                            .disabled(s.busy)
                        }
                    }
                    Text("Extras from your other games").font(.headline)
                    Text("Optional cars from your own Carbon PC or Xbox 360 copy. Choose this independently of your Most Wanted copies. Later-game cars remain Off until enabled in game settings.")
                        .font(.caption).foregroundStyle(.secondary)
                    choice("Carbon source", s.carbon, "Folder or ISO", s.chooseCarbon)
                    ForEach(["pc", "x360"], id: \.self) { platform in
                        GroupBox(platform == "pc" ? "Carbon PC cars" : "Carbon Xbox 360 cars") {
                            VStack(alignment: .leading, spacing: 4) {
                                ForEach(s.carChoices.filter { $0.platform == platform }) { car in
                                    Toggle(car.name, isOn: Binding(get: { s.selectedCarIDs.contains(car.id) },
                                                                  set: { s.selectCar(car, enabled: $0) }))
                                    if let reason = car.reason(s.state.recognition) {
                                        Text(reason).font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                            }.frame(maxWidth: .infinity, alignment: .leading).padding(4)
                        }.disabled(s.busy)
                    }
                    Text("All cars start unticked. Tick the cars you want, then check and import them. Missing sources are skipped.")
                        .font(.caption).foregroundStyle(.secondary)
                    Text(s.extras.message).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    if let recognition = s.state.recognition, s.state.carPlan == nil {
                        ScrollView {
                            VStack(alignment: .leading) {
                                ForEach(recognition.cars, id: \.car_hash) { car in
                                    Text("\(car.car_hash): \(car.status == "importable" ? "source verified; output needs approval" : car.status)")
                                }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }.frame(maxHeight: 100)
                    }
                    if let plan = s.state.carPlan {
                        ScrollView {
                            VStack(alignment: .leading) {
                                ForEach(plan.cars, id: \.slot) { car in
                                    Text(car.name + ": " + (car.approved ? "Approved and importable" : car.reason))
                                        .font(.caption)
                                }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }.frame(maxHeight: 80)
                        ForEach(plan.volumes, id: \.device) { volume in
                            Text(volume.required_bytes.map { "Extras on volume \(volume.device): \(ByteCountFormatter.string(fromByteCount: Int64($0), countStyle: .file)) required." }
                                 ?? "Extras on volume \(volume.device): conversion output size is unknown until measured.")
                                .font(.caption)
                        }
                        Text(plan.approved ? "Selected outputs approved." : "Selected outputs have not passed packaged approval.")
                            .font(.caption)
                    }
                    if let progress = s.state.carProgress {
                        Text("\(progress.phase) \(progress.slot >= 0 ? "— slot \(progress.slot)" : ""): \(progress.done)/\(progress.total)")
                            .font(.caption).monospacedDigit()
                    }
                    HStack {
                        Button("Retry check") { s.runCars(.carsCheck, measure: true) }.disabled(s.busy || s.carbon == nil)
                        Button("Import selected extras") { s.runCars(.carsImport) }
                            .disabled(s.busy || s.carbon == nil || !s.availableCarSelection || s.state.carPlan?.approved != true)
                        Button("Retry import") { s.runCars(.carsImport, repair: true) }
                            .disabled(s.busy || s.carbon == nil || !s.availableCarSelection || s.state.recognition?.approval_available != true)
                        Button("Refresh installed extras") { s.runCars(.carsList) }.disabled(s.busy)
                        if s.busy { Button("Cancel extras") { s.cancel() }.disabled(s.stopping) }
                    }
                    if let installed = s.installedCars {
                        if !installed.message.isEmpty { Text(installed.message).font(.caption).foregroundStyle(.orange) }
                        ScrollView {
                            VStack(alignment: .leading) {
                                ForEach(installed.cars.filter { $0.state == "ready" }, id: \.slot) { car in
                                    HStack {
                                        Text("\(car.name) (slot \(car.slot)) — \(car.selected ? "selected" : "retained")")
                                        Spacer()
                                        if car.selected { Button("Remove") { s.runCars(.carsRemove, slots: [car.slot]) }.disabled(s.busy) }
                                    }
                                }
                                ForEach(installed.generations.filter { $0.verified }, id: \.generation) { generation in
                                    HStack {
                                        Text("Retained \(generation.generation.prefix(14))…").font(.caption)
                                        Button("Restore") { s.runCars(.carsRestore, generation: generation.generation) }.disabled(s.busy)
                                    }
                                }
                            }
                        }.frame(maxHeight: 100)
                    }
                }
            }
            HStack {
                Button("Back") { s.page = s.managingExtras ? .welcome : .x360 }.disabled(s.busy)
                Spacer()
                Button("Skip car extras for now") { s.skipExtras() }.disabled(s.busy)
                Button(s.managingExtras ? "Done" : "Continue to Review") { s.page = s.managingExtras ? .welcome : .review }
                    .disabled(s.busy)
            }
        }.onAppear { s.loadCarChoices() }
    }

    @ViewBuilder var featureList: some View {
        if let f = s.features {
            if f.x360 && f.missing.isEmpty {
                Label("Every Xbox 360 feature is available.", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
            } else if !f.missing.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    Text(f.x360 ? "Not available with this copy:" : "Not available without an Xbox 360 copy:").bold()
                    ForEach(f.missing, id: \.self) { Text("• " + $0) }
                }.font(.callout)
            }
        }
    }

    var review: some View {
        VStack(alignment: .leading, spacing: 12) {
            GroupBox(label: Text("Review").bold()) {
                VStack(alignment: .leading, spacing: 6) {
                    row(s.pcKind == .disc ? "PC discs" : "PC game", s.pc?.path ?? "")
                    if s.pcKind == .disc {
                        ForEach(s.pcDiscs.keys.sorted(), id: \.self) { n in row("Disc \(n)", s.pcDiscs[n]?.path ?? "") }
                        if let u = s.pcUpdate { row("1.3 update", u.path) }
                        if let e = s.pcExe { row("speed.exe", e.path) }
                    }
                    row("Xbox 360 copy", s.x360?.path ?? "None (PC game only)")
                    row("Xbox 360 extras", s.x360 == nil ? "PC assets" : s.x360Extras.summary)
                    row("Other-game extras", s.extras.skipped ? "Skipped" : s.extras.message)
                    HStack {
                        row("Install in", s.dest.path)
                        Button("Change…") { s.chooseDest() }
                    }
                    let free = s.freeGB.map { String(format: "%.0f GB free", $0) } ?? "free space unknown"
                    Label("Needs about \(String(format: "%.1f", s.requiredGB)) GB while building; \(free) there.",
                          systemImage: s.enoughSpace ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(s.enoughSpace ? .green : .orange)
                    if s.enoughSpace == false { Text("Free some space or choose another place.").font(.caption) }
                    if FileManager.default.fileExists(atPath: s.installedApp.path) {
                        Text("NFS Most Wanted Native is already there. A 3.x copy is kept as \"NFS Most Wanted Native 3.x\"; a 4.0 copy is replaced only after the new one is complete.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Text("Keep the Mac plugged in; it stays awake while this runs.").font(.caption).foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, alignment: .leading).padding(4)
            }
            HStack {
                Button("Back") { s.page = .extras }
                Spacer()
                Button("Build") { s.build() }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent).disabled(!s.enoughSpace || s.running)
            }
        }
    }

    func row(_ k: String, _ v: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(k).bold().frame(width: 100, alignment: .leading)
            Text(v).lineLimit(2).truncationMode(.middle)
            Spacer()
        }
    }

    var buildPage: some View {
        VStack(alignment: .leading, spacing: 10) {
            if case let .failed(f)? = s.outcome { failure(f) }
            else if case .cancelled? = s.outcome {
                Label("Stopped. Anything already installed was left as it was.", systemImage: "stop.circle").font(.headline)
                backOrAgain
            } else {
                if s.exporting { ProgressView() }
                else { ProgressView(value: s.state.overall) }
                HStack {
                    Text(s.state.currentTitle.isEmpty ? "Starting…" : s.state.currentTitle).bold()
                    Spacer()
                    TimelineView(.periodic(from: .now, by: 1)) { _ in Text(s.elapsedText()).monospacedDigit() }
                }
                if s.exporting {
                    Text("Copying your installed game files into a new app, then signing it. Saves and settings stay in Application Support.")
                        .font(.callout).foregroundStyle(.secondary)
                } else { stageList }
                if s.stopping {
                    Label(s.state.currentStage == "install" ? "Restoring your previous version…" : "Stopping…",
                          systemImage: "hourglass")
                }
                HStack { Button("Show Log") { s.showLog() }; Spacer(); Button("Cancel") { s.cancel() }.disabled(!s.running || s.stopping) }
            }
        }
    }

    var stageList: some View {
        let cur = stageTable.firstIndex { $0.id == s.state.currentStage } ?? -1
        return VStack(alignment: .leading, spacing: 3) {
            ForEach(Array(stageTable.enumerated()), id: \.offset) { i, st in
                let skipped = i < cur && !s.state.seen.contains(st.id)
                HStack {
                    Image(systemName: i < cur ? (skipped ? "minus.circle" : "checkmark.circle.fill") : (i == cur ? "arrow.right.circle.fill" : "circle"))
                        .foregroundStyle(i < cur ? Color.green : (i == cur ? Color.accentColor : Color.secondary))
                    Text(st.title).foregroundStyle(i > cur ? .secondary : .primary)
                    if i == cur, let p = s.state.progress[st.id] { Text("\(Int(p * 100))%").monospacedDigit().foregroundStyle(.secondary) }
                    if skipped { Text("not needed").font(.caption).foregroundStyle(.secondary) }
                }
            }
        }.padding(6).frame(maxWidth: .infinity, alignment: .leading)
    }

    func failure(_ f: FailureInfo) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label((s.exporting ? "The export stopped" : "The build stopped") + (f.stage.isEmpty ? "." : " at: \(stageTable.first { $0.id == f.stage }?.title ?? f.stage)."),
                  systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange).font(.headline)
            Text(f.message).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            if !f.fix.isEmpty { Text(f.fix).fixedSize(horizontal: false, vertical: true).foregroundStyle(.secondary) }
            Text("Nothing was replaced.").font(.caption).foregroundStyle(.secondary)
            backOrAgain
        }
    }

    var backOrAgain: some View {
        HStack {
            Button("Show Log") { s.showLog() }
            Button("Copy Log") { s.copyLog() }
            Spacer()
            Button("Back") { s.page = s.exporting ? .welcome : .review; s.outcome = nil }
            if s.exporting {
                Button("Export again…") { s.exportAllInOne() }.buttonStyle(.borderedProminent)
            } else {
                Button("Build again") { s.build() }.buttonStyle(.borderedProminent)
            }
        }
    }

    var finished: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("The game is ready (\(s.elapsedText())).", systemImage: "checkmark.circle.fill").foregroundStyle(.green).font(.headline)
            Text(s.installedPath).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            Text("The first launch takes a little longer while the Mac prepares the game's shaders.").font(.caption).foregroundStyle(.secondary)
            // Install-stage notes for the player, e.g. the 3.x career copy (backend migrate_3x_saves).
            ForEach(s.state.warnings.filter { $0.hasPrefix("install: ") || $0.hasPrefix("bundle: ") || $0.hasPrefix("pc: ") }
                .map { String($0.drop(while: { $0 != " " }).dropFirst()) }, id: \.self) { note in
                Label(note, systemImage: "info.circle").font(.callout).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button("Open") { s.openGame() }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent)
                Button("Show in Finder") { s.showGame() }
                Button("Show Log") { s.showLog() }
                Spacer()
            }
            Button("Manage installed extras…") { s.manageExtras() }.disabled(s.busy)
            Button("Export all-in-one app…") { s.exportAllInOne() }.disabled(s.busy)
        }
    }
}

struct SetupApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    var body: some Scene {
        WindowGroup { ContentView(s: Setup.shared) }.windowResizability(.contentSize)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        let s = Setup.shared
        switch quitPlan(childActive: s.busy, building: s.running) {
        case .now:
            return .terminateNow
        case .askThenCancelAndWait:
            let alert = NSAlert()
            alert.messageText = "Stop building the game?"
            alert.informativeText = "The build stops now. Anything already installed is left as it was."
            alert.addButton(withTitle: "Stop and Quit")
            alert.addButton(withTitle: "Keep Building")
            guard alert.runModal() == .alertFirstButtonReturn else { return .terminateCancel }
        case .cancelAndWait:
            break
        }
        // Quit only after the backend has exited (and restored anything it had started replacing).
        return s.stopThen { NSApp.reply(toApplicationShouldTerminate: true) } ? .terminateLater : .terminateNow
    }
}

// MARK: - Entry (window, or a headless check)

func jsonString(_ obj: Any) -> String {
    let d = (try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])) ?? Data()
    return String(decoding: d, as: UTF8.self)
}

func protocolTest(_ a: [String]) -> Int32 {
    // STREAM_FILE EXIT_STATUS CHUNK_BYTES check|build [cancelled]
    guard a.count >= 4, let data = FileManager.default.contents(atPath: a[0]),
          let status = Int32(a[1]), let chunk = Int(a[2]), chunk > 0 else { return 2 }
    guard let mode = RunMode(rawValue: a[3]) else { return 2 }
    var splitter = LineSplitter()
    var st = RunState()
    var lines: [String] = []
    var i = 0
    while i < data.count {
        lines += splitter.feed(data.subdata(in: i..<min(i + chunk, data.count)))
        i += chunk
    }
    if let rest = splitter.flush() { lines.append(rest) }
    for l in lines { if let ev = parseEvent(l) { st.apply(ev) } }
    let o = st.outcome(mode: mode, status: status, cancelled: a.count > 4 && a[4] == "cancelled",
                       appExists: { FileManager.default.fileExists(atPath: $0) })
    var out: [String: Any] = ["lines": lines.count, "stage": st.currentStage, "warnings": st.warnings.count,
                              "overall": (st.overall * 1000).rounded() / 1000]
    switch o {
    case let .success(app): out["outcome"] = "success"; out["app"] = app ?? NSNull()
    case .cancelled: out["outcome"] = "cancelled"
    case let .failed(f): out["outcome"] = "failed"; out["message"] = f.message; out["fix"] = f.fix; out["failedStage"] = f.stage
    }
    if let f = st.features { out["features"] = ["x360": f.x360, "missing": f.missing] }
    if let r = st.recognition { out["recognition"] = ["platform": r.platform, "approval": r.approval_available, "count": r.cars.count] }
    if let r = st.carResult { out["carOperation"] = r.operation; out["carSelection"] = r.selection }
    if let p = st.carProgress { out["carPhase"] = p.phase; out["carSlot"] = p.slot; out["carBytes"] = p.done }
    if let p = st.carPlan {
        out["carApproved"] = p.approved; out["carVolumes"] = p.volumes.count
        out["approvedCars"] = p.cars.filter { $0.approved }.map { $0.slot }
    }
    print(jsonString(out))
    return 0
}

/// Starts a shell that leaves two background sleeps, cancels it like the Cancel button, and lists what is still alive.
func cancelTest() -> Never {
    var seen = Data()
    guard let c = try? ChildProcess(exe: "/bin/sh", args: ["-c", "sleep 300 & echo $!; sleep 300 & echo $!; wait"],
                                    env: ["PATH": "/usr/bin:/bin"], cwd: nil) else { exit(2) }
    c.run(onOut: { seen.append($0) }, onErr: { _ in }, done: { status in
        let pids = String(decoding: seen, as: UTF8.self).split(separator: "\n").compactMap { Int32($0) }
        usleep(300_000)
        let alive = pids.filter { kill($0, 0) == 0 }
        print(jsonString(["exit": Int(status), "started": pids.count, "alive": alive.map { Int($0) }]))
        exit(0)
    })
    DispatchQueue.global().asyncAfter(deadline: .now() + 1) { c.cancel() }
    dispatchMain()
}

/// A fake backend that enters STAGE and, on TERM, takes 2*GRACE seconds to "restore" before writing MARKER.
func installCancelTest(_ a: [String]) -> Never {
    guard a.count >= 3, let grace = Double(a[1]) else { exit(2) }
    let stage = a[0], marker = a[2]
    let script = "printf '{\"ev\":\"stage\",\"id\":\"%s\",\"title\":\"t\"}\\n' \"$1\"; " +
        "trap 'sleep \"$3\"; echo restored > \"$2\"; exit 130' TERM; while :; do sleep 0.1; done"
    guard let c = try? ChildProcess(exe: "/bin/sh", args: ["-c", script, "fake-backend", stage, marker, String(2 * grace)],
                                    env: ["PATH": "/usr/bin:/bin"], cwd: nil) else { exit(2) }
    var splitter = LineSplitter()
    var st = RunState()
    var sent = false
    c.run(onOut: { data in
        DispatchQueue.main.async {
            for l in splitter.feed(data) { if let ev = parseEvent(l) { st.apply(ev) } }
            if !sent && st.currentStage == stage {
                sent = true
                c.cancel(grace: grace, forceAllowed: { forceStopAllowed(stage: st.currentStage) })
            }
        }
    }, onErr: { _ in }, done: { status in
        print(jsonString(["exit": Int(status), "restored": FileManager.default.fileExists(atPath: marker)]))
        exit(0)
    })
    dispatchMain()
}

@main
struct Entry {
    static func main() {
        let args = CommandLine.arguments
        if args.count == 6, args[1] == "--car-selections-test" {
            do {
                let choices = try CarChoice.load(URL(fileURLWithPath: args[2]))
                let recognition = try JSONDecoder().decode(CarRecognition.self, from: Data(args[4].utf8))
                let selected = Set(try JSONDecoder().decode([String].self, from: Data(args[5].utf8)))
                let work = URL(fileURLWithPath: args[3])
                let selection = try writeCarSelection(choices: choices, selected: selected, recognition: recognition, work: work)
                let command = carArguments(mode: .carsImport, carbon: URL(fileURLWithPath: "/carbon"),
                                           selection: selection, work: work)
                print(jsonString(["arguments": command, "names": choices.map { $0.name },
                                  "ids": choices.map { $0.id }, "defaults": Array(Setup().selectedCarIDs),
                                  "reasons": choices.map { $0.reason(recognition) ?? "" },
                                  "spec": selection?.path ?? ""]))
                exit(0)
            } catch { fputs((error.localizedDescription + "\n"), stderr); exit(1) }
        }
        if args.count > 1, args[1] == "--protocol-test" { exit(protocolTest(Array(args.dropFirst(2)))) }
        if args.count > 1, args[1] == "--cancel-test" { cancelTest() }
        if args.count > 1, args[1] == "--install-cancel-test" { installCancelTest(Array(args.dropFirst(2))) }
        if args.count > 1, args[1] == "--quit-policy-test" {
            print(jsonString(["idle": quitPlan(childActive: false, building: false).rawValue,
                              "checking": quitPlan(childActive: true, building: false).rawValue,
                              "building": quitPlan(childActive: true, building: true).rawValue]))
            exit(0)
        }
        if args.count > 1, args[1] == "--x360-extras-test" {
            let data = args.count > 2 ? args[2].data(using: .utf8) : nil
            let chosen = X360Extras.load(data)
            let command = backendArguments(mode: .build, pc: URL(fileURLWithPath: "/pc"), x360: URL(fileURLWithPath: "/x360"),
                                           dest: URL(fileURLWithPath: "/dest"), work: URL(fileURLWithPath: "/work"), x360Extras: chosen)
            print(jsonString(["names": chosen.names, "bytes": chosen.bytes, "requiredGB": chosen.requiredGB(hasX360: true), "arguments": command]))
            exit(0)
        }
        if args.count > 1, args[1] == "--extras-flow-test" {
            var flow = ExtrasFlow()
            flow.begin()
            flow.skip()
            let busySkipRefused = !flow.skipped
            flow.finish(.failed(FailureInfo(stage: "cars", message: "failed", fix: "retry", log: "")))
            let failureIsOptional = !flow.completed && !flow.busy
            flow.begin()
            let retryReset = !flow.completed && !flow.skipped && flow.busy
            flow.finish(.cancelled)
            let cancelled = !flow.completed && !flow.busy
            flow.skip()
            let skipped = flow.skipped
            flow.begin()
            flow.finish(.success(app: nil))
            let completed = flow.completed
            let retryAfterSkip = !flow.skipped
            flow.begin()
            let retryAfterSuccess = !flow.completed && flow.busy
            print(jsonString(["busySkipRefused": busySkipRefused, "failureIsOptional": failureIsOptional,
                              "retryReset": retryReset, "cancelled": cancelled, "skipped": skipped,
                              "completed": completed, "retryAfterSuccess": retryAfterSuccess, "retryAfterSkip": retryAfterSkip, "pageOrder": Page.x360.rawValue < Page.extras.rawValue && Page.extras.rawValue < Page.review.rawValue]))
            exit(0)
        }
        if args.count > 1, args[1] == "--env-test" {
            print(jsonString(childEnvironment(inherited: ProcessInfo.processInfo.environment, python: "/py", roots: "/roots.txt")))
            exit(0)
        }
        SetupApp.main()
    }
}
