// NFS Most Wanted Native - Setup.
//
// A small Mac app in front of the setup backend (tools/setup.py). The player chooses an installed PC game folder and,
// optionally, their own Xbox 360 copy; the backend builds the native app on this Mac. The app carries no game files:
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
/// Xbox 360 copy, plus 2.8 GB for an unpacked .zip.
let requiredFreeGB = 12.0

enum SetupEvent: Equatable {
    case stage(id: String, title: String)
    case progress(id: String, done: Int, total: Int)
    case warn(id: String, msg: String)
    case error(id: String, msg: String, fix: String, log: String)
    case features(x360: Bool, missing: [String])
    case done(app: String)
}

func parseEvent(_ line: String) -> SetupEvent? {
    let trimmed = line.trimmingCharacters(in: .whitespaces)
    guard trimmed.hasPrefix("{"), let data = trimmed.data(using: .utf8),
          let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
          let ev = obj["ev"] as? String else { return nil }
    func str(_ k: String) -> String { obj[k] as? String ?? "" }
    func int(_ k: String) -> Int { (obj[k] as? NSNumber)?.intValue ?? 0 }
    switch ev {
    case "stage": return .stage(id: str("id"), title: str("title"))
    case "progress": return .progress(id: str("id"), done: int("done"), total: int("total"))
    case "warn": return .warn(id: str("id"), msg: str("msg"))
    case "error": return .error(id: str("id"), msg: str("msg"), fix: str("fix"), log: str("log"))
    case "features": return .features(x360: (obj["x360"] as? Bool) ?? false, missing: (obj["missing"] as? [String]) ?? [])
    case "done": return .done(app: str("app"))
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

enum RunMode { case check, build }

struct RunState {
    var currentStage = ""
    var currentTitle = ""
    var seen: [String] = []
    var progress: [String: Double] = [:]
    var warnings: [String] = []
    var error: FailureInfo?
    var features: (x360: Bool, missing: [String])?
    var appPath: String?

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
        if cancelled { return .cancelled }
        if let e = error { return .failed(e) }
        guard status == 0 else {
            let tail = stderrTail.isEmpty ? "The setup stopped (exit \(status))." : stderrTail
            return .failed(FailureInfo(stage: currentStage, message: tail,
                                       fix: "Open the log to see why, then try again.", log: ""))
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

/// Unpacks the bundled source (and the FFmpeg tarball the build needs) into the support folder, once per version.
func prepareSource() throws {
    let fm = FileManager.default
    let version = bundledVersion()
    let src = sourceDir()
    let marker = src.appendingPathComponent(".source-version")
    if (try? String(contentsOf: marker, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines) == version,
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
    try version.write(to: tmp.appendingPathComponent(".source-version"), atomically: true, encoding: .utf8)
    try? fm.removeItem(at: src)
    try fm.createDirectory(at: supportDir(), withIntermediateDirectories: true)
    try fm.moveItem(at: tmp, to: src)
}

func backendArguments(mode: RunMode, pc: URL, x360: URL?, dest: URL, work: URL) -> [String] {
    var a = ["-I", sourceDir().appendingPathComponent("tools/setup.py").path,
             mode == .check ? "--check" : "--build", "--pc", pc.path]
    if let x = x360 { a += ["--x360", x.path] }
    a += ["--dest", dest.path, "--work", work.path, "--events"]
    return a
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
func forceStopAllowed(stage: String) -> Bool { stage != "install" }

enum QuitPlan: String { case now, cancelAndWait = "cancel-wait", askThenCancelAndWait = "ask-cancel-wait" }

/// Quitting with any backend alive (a check or a build) cancels it and waits for it to exit; a build asks first.
func quitPlan(childActive: Bool, building: Bool) -> QuitPlan {
    guard childActive else { return .now }
    return building ? .askThenCancelAndWait : .cancelAndWait
}

// MARK: - App model

enum Page: Int { case welcome, pc, x360, review, build, finished }

final class Setup: ObservableObject {
    static let shared = Setup()

    @Published var page: Page = .welcome
    @Published var tools = hasCommandLineTools()
    @Published var installingTools = false
    @Published var pc: URL?
    @Published var pcStatus = ""
    @Published var pcOK = false
    @Published var x360: URL?
    @Published var x360Status = ""
    @Published var features: (x360: Bool, missing: [String])?
    @Published var dest: URL
    @Published var checking = false
    @Published var running = false
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
    var enoughSpace: Bool { (freeGB ?? requiredFreeGB) >= requiredFreeGB }

    func pickFolder(message: String, files: Bool = false, types: [UTType] = []) -> URL? {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = files
        if !types.isEmpty { panel.allowedContentTypes = types }
        panel.allowsMultipleSelection = false
        panel.message = message
        panel.prompt = "Choose"
        return panel.runModal() == .OK ? panel.url : nil
    }

    func choosePC() {
        // The game folder, or the Black Edition .zip as downloaded (Setup unpacks a copy; the .zip is only read).
        guard let url = pickFolder(message: "Choose the Need for Speed: Most Wanted folder (the one with speed.exe), or the Black Edition .zip you downloaded.",
                                   files: true, types: [.zip]) else { return }
        pc = url; features = nil; x360 = nil; x360Status = ""
        runCheck(forX360: false)
    }

    func chooseX360() {
        guard let url = pickFolder(message: "Choose your Xbox 360 copy: its folder, or the .iso file.", files: true) else { return }
        x360 = url
        runCheck(forX360: true)
    }

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
    private func launch(mode: RunMode, pc: URL, x360: URL?, finish: @escaping (Outcome, RunState) -> Void) {
        state = RunState(); lines = []; stderrTail = []; splitter = LineSplitter(); cancelled = false; stopping = false
        let python = bundledPython()
        let work = workDir()
        do {
            try prepareSource()
            try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
            let env = childEnvironment(inherited: ProcessInfo.processInfo.environment, python: python,
                                       roots: work.appendingPathComponent("roots.txt").path)
            let c = try ChildProcess(exe: python, args: backendArguments(mode: mode, pc: pc, x360: x360, dest: dest, work: work),
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
                else { self.pcStatus = pc.pathExtension.lowercased() == "zip" ? "Your game .zip can be used." : "Your game folder can be used."; self.pcOK = true }
            case let .failed(f):
                let text = [f.message, f.fix].filter { !$0.isEmpty }.joined(separator: "\n")
                if forX360 { self.x360Status = text; self.x360 = nil } else { self.pcStatus = text; self.pcOK = false }
            case .cancelled: break
            }
        }
    }

    func build() {
        guard let pc, !running else { return }
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
    @State private var showReadMe = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text("NFS Most Wanted Native Setup").font(.title).bold()
                Spacer()
                Button("Read Me") { showReadMe = true }
            }
            .sheet(isPresented: $showReadMe) { ReadMeView(shown: $showReadMe) }
            switch s.page {
            case .welcome: welcome
            case .pc: pcPage
            case .x360: x360Page
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
            if let next { Button(nextTitle) { s.page = next }.keyboardShortcut(.defaultAction)
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
        }
    }

    var pcPage: some View {
        VStack(alignment: .leading, spacing: 12) {
            GroupBox(label: Text("Your PC game").bold()) {
                VStack(alignment: .leading, spacing: 8) {
                    Picker("", selection: .constant(0)) {
                        Text("Installed PC game folder or .zip").tag(0)
                        Text("PC disc image").tag(1)
                    }.pickerStyle(.radioGroup).labelsHidden()
                    Text("PC disc image: Not available yet; choose an installed PC game folder.")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Text(s.pc?.path ?? "The folder with speed.exe, or the Black Edition .zip (version 1.3).")
                            .lineLimit(2).truncationMode(.middle).foregroundStyle(s.pc == nil ? .secondary : .primary)
                        Spacer()
                        Button("Choose…") { s.choosePC() }.disabled(s.checking)
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
            nav(back: .pc, next: .review, nextEnabled: true)
        }
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
                    row("PC game", s.pc?.path ?? "")
                    row("Xbox 360 copy", s.x360?.path ?? "None (PC game only)")
                    HStack {
                        row("Install in", s.dest.path)
                        Button("Change…") { s.chooseDest() }
                    }
                    let free = s.freeGB.map { String(format: "%.0f GB free", $0) } ?? "free space unknown"
                    Label("Needs about \(Int(requiredFreeGB)) GB while building; \(free) there.",
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
                Button("Back") { s.page = .x360 }
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
                ProgressView(value: s.state.overall)
                HStack {
                    Text(s.state.currentTitle.isEmpty ? "Starting…" : s.state.currentTitle).bold()
                    Spacer()
                    TimelineView(.periodic(from: .now, by: 1)) { _ in Text(s.elapsedText()).monospacedDigit() }
                }
                stageList
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
            Label("The build stopped" + (f.stage.isEmpty ? "." : " at: \(stageTable.first { $0.id == f.stage }?.title ?? f.stage)."),
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
            Button("Back") { s.page = .review; s.outcome = nil }
            Button("Build again") { s.build() }.buttonStyle(.borderedProminent)
        }
    }

    var finished: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("The game is ready (\(s.elapsedText())).", systemImage: "checkmark.circle.fill").foregroundStyle(.green).font(.headline)
            Text(s.installedPath).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            Text("The first launch takes a little longer while the Mac prepares the game's shaders.").font(.caption).foregroundStyle(.secondary)
            // Install-stage notes for the player, e.g. the 3.x career copy (backend migrate_3x_saves).
            ForEach(s.state.warnings.filter { $0.hasPrefix("install: ") }.map { String($0.dropFirst(9)) }, id: \.self) { note in
                Label(note, systemImage: "info.circle").font(.callout).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button("Open") { s.openGame() }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent)
                Button("Show in Finder") { s.showGame() }
                Button("Show Log") { s.showLog() }
                Spacer()
            }
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
    let mode: RunMode = a[3] == "build" ? .build : .check
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
        if args.count > 1, args[1] == "--protocol-test" { exit(protocolTest(Array(args.dropFirst(2)))) }
        if args.count > 1, args[1] == "--cancel-test" { cancelTest() }
        if args.count > 1, args[1] == "--install-cancel-test" { installCancelTest(Array(args.dropFirst(2))) }
        if args.count > 1, args[1] == "--quit-policy-test" {
            print(jsonString(["idle": quitPlan(childActive: false, building: false).rawValue,
                              "checking": quitPlan(childActive: true, building: false).rawValue,
                              "building": quitPlan(childActive: true, building: true).rawValue]))
            exit(0)
        }
        if args.count > 1, args[1] == "--env-test" {
            print(jsonString(childEnvironment(inherited: ProcessInfo.processInfo.environment, python: "/py", roots: "/roots.txt")))
            exit(0)
        }
        SetupApp.main()
    }
}
