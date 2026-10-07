// NFS Most Wanted Native - Setup.
//
// A small Mac app that runs this repository's setup kit (setup.sh) for the player: choose your own installed copy
// of the PC game, and it builds the native app on this Mac. It contains no game code or data; it carries this
// repository's source (Resources/source.tar.gz) and unpacks it into ~/Library/NFSMW-Native-Setup/source.
// The one-time analysis of speed.exe, the setup's tools and its logs stay in that folder, so an update is quicker.
//
// Headless test: Setup --selftest GAME_FOLDER OUT_FOLDER   (same source preparation, check and build as the window)

import AppKit
import SwiftUI

let gameAppName = "NFS Most Wanted Native"
let supportDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/NFSMW-Native-Setup")
let workDir = supportDir.appendingPathComponent("source")
// What an update keeps: the analysis of speed.exe, the Python environment, the build cache, the game links.
let keptOnUpdate: Set<String> = ["analysis", ".venv", "build", "original", "dist"]

enum Phase { case idle, checking, building, done, failed, cancelled }

struct StepInfo { let number: Int; let name: String }

/// Parses setup.sh output lines like "3/7 Analysing speed.exe (once; a few minutes)".
func parseStep(_ line: String) -> StepInfo? {
    let t = line.trimmingCharacters(in: .whitespaces)
    guard t.count > 4, let n = Int(String(t.prefix(1))), t.dropFirst(1).hasPrefix("/7 ") else { return nil }
    return StepInfo(number: n, name: String(t.dropFirst(4)))
}

/// The share of the whole build each step usually takes (the analysis and the compile dominate).
let stepWeights: [Double] = [0, 0.01, 0.01, 0.55, 0.38, 0.02, 0.01, 0.02]

func overallProgress(step: Int, within: Double) -> Double {
    guard step >= 1 else { return 0 }
    let done = stepWeights.prefix(min(step, 7)).reduce(0, +)
    let w = step <= 7 ? stepWeights[step] : 0
    return min(1, done + w * max(0, min(1, within)))
}

/// The setup's environment: the system's tools first, and none of the developer test variables.
func setupEnvironment() -> [String: String] {
    var env = ProcessInfo.processInfo.environment.filter { key, _ in
        !key.hasPrefix("RECOMP_") && !key.hasPrefix("NFSMW_") && !key.hasPrefix("POPM_") && !key.hasPrefix("QA_")
            && !key.hasPrefix("PYTHON") && key != "VIRTUAL_ENV"
    }
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
    return env
}

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

func hasCommandLineTools() -> Bool { runQuick("/usr/bin/xcode-select", ["-p"]).status == 0 }

func bundledVersion() -> String {
    guard let url = Bundle.main.url(forResource: "source-version", withExtension: "txt"),
          let s = try? String(contentsOf: url, encoding: .utf8) else { return "dev" }
    return s.trimmingCharacters(in: .whitespacesAndNewlines)
}

/// Unpacks the bundled source into workDir, keeping the analysis, environment and build cache of an earlier run.
func prepareSource() throws {
    let fm = FileManager.default
    let version = bundledVersion()
    let marker = workDir.appendingPathComponent(".source-version")
    if (try? String(contentsOf: marker, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines) == version,
       fm.fileExists(atPath: workDir.appendingPathComponent("setup.sh").path) {
        return
    }
    guard let archive = Bundle.main.url(forResource: "source", withExtension: "tar.gz") else {
        throw NSError(domain: "Setup", code: 1, userInfo: [NSLocalizedDescriptionKey: "This Setup app is incomplete (no source). Download it again."])
    }
    let tmp = supportDir.appendingPathComponent("source.new")
    try? fm.removeItem(at: tmp)
    try fm.createDirectory(at: tmp, withIntermediateDirectories: true)
    let r = runQuick("/usr/bin/tar", ["-xzf", archive.path, "-C", tmp.path])
    guard r.status == 0 else {
        throw NSError(domain: "Setup", code: 2, userInfo: [NSLocalizedDescriptionKey: "Could not unpack the setup: \(r.output)"])
    }
    try fm.createDirectory(at: workDir, withIntermediateDirectories: true)
    for name in try fm.contentsOfDirectory(atPath: workDir.path) where !keptOnUpdate.contains(name) {
        try fm.removeItem(at: workDir.appendingPathComponent(name))
    }
    for name in try fm.contentsOfDirectory(atPath: tmp.path) where !keptOnUpdate.contains(name) {
        try fm.moveItem(at: tmp.appendingPathComponent(name), to: workDir.appendingPathComponent(name))
    }
    try? fm.removeItem(at: tmp)
    try version.write(to: marker, atomically: true, encoding: .utf8)
}

func setupArguments(game: URL, dest: URL, xenon: URL?, checkOnly: Bool) -> [String] {
    var a = [workDir.appendingPathComponent("setup.sh").path, game.path]
    if checkOnly { return a + ["--check-only"] }
    a += ["--out", dest.path]
    if let x = xenon { a += ["--xenon-effects", x.path] }
    return a
}

/// What went wrong, in the setup's own words: its SETUP STOPPED line or its last lines.
func failureSummary(_ lines: [String]) -> String {
    if let i = lines.lastIndex(where: { $0.contains("SETUP STOPPED") || $0.contains("can't be used") }) {
        let text = lines[i...].prefix(4).joined(separator: "\n").replacingOccurrences(of: "SETUP STOPPED: ", with: "")
        return text.contains("setup.log") ? text + "\nClick Show the log to see them." : text
    }
    return lines.suffix(6).joined(separator: "\n")
}

/// The last progress the setup log shows: the compile's [done/total], or the analysis's exported functions.
func logProgress(_ text: Substring) -> (fraction: Double?, detail: String?) {
    var fraction: Double?
    var detail: String?
    for line in text.split(separator: "\n").reversed() {
        if fraction == nil, line.hasPrefix("["), let close = line.firstIndex(of: "]") {
            let parts = line[line.index(after: line.startIndex)..<close].split(separator: "/")
            if parts.count == 2, let a = Double(parts[0]), let b = Double(parts[1]), b > 0 {
                fraction = a / b
                detail = "Compiling: \(Int(a)) of \(Int(b))"
                break
            }
        }
        if let r = line.range(of: "Exported "), line.contains("functions") {
            detail = "Analysing: " + line[r.lowerBound...].replacingOccurrences(of: ";", with: ",")
            break
        }
    }
    return (fraction, detail)
}

func readLogTail(_ url: URL, bytes: Int = 65536) -> Substring {
    guard let h = try? FileHandle(forReadingFrom: url) else { return "" }
    defer { try? h.close() }
    let size = (try? h.seekToEnd()) ?? 0
    try? h.seek(toOffset: size > UInt64(bytes) ? size - UInt64(bytes) : 0)
    let data = (try? h.readToEnd()) ?? Data()
    return Substring(String(decoding: data, as: UTF8.self))
}

/// Every process started under pid (the setup runs Python, Ghidra, CMake and the compiler under setup.sh).
func descendants(of pid: Int32) -> [Int32] {
    var all: [Int32] = []
    var frontier = [pid]
    while let p = frontier.popLast() {
        let kids = runQuick("/usr/bin/pgrep", ["-P", String(p)]).output.split(separator: "\n").compactMap { Int32($0) }
        all += kids
        frontier += kids
    }
    return all
}

final class Setup: ObservableObject {
    static let shared = Setup()

    @Published var game: URL?
    @Published var gameStatus = ""
    @Published var gameOK = false
    @Published var xenon: URL?
    @Published var dest: URL
    @Published var tools = hasCommandLineTools()
    @Published var installingTools = false
    @Published var phase: Phase = .idle
    @Published var step = 0
    @Published var stepName = ""
    @Published var detail = ""
    @Published var progress = 0.0
    @Published var lines: [String] = []
    @Published var started = Date()
    @Published var message = ""

    private var process: Process?
    private var activity: NSObjectProtocol?
    private var timer: Timer?
    private var partial = ""
    private var cancelled = false

    init() {
        let apps = URL(fileURLWithPath: "/Applications")
        if FileManager.default.isWritableFile(atPath: apps.path) {
            dest = apps
        } else {
            dest = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications")
        }
    }

    var builtApp: URL { dest.appendingPathComponent(gameAppName + ".app") }
    var busy: Bool { phase == .checking || phase == .building }
    var canBuild: Bool { gameOK && tools && !busy }

    func chooseGame() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.message = "Choose your installed Need for Speed: Most Wanted folder (the one with speed.exe)."
        panel.prompt = "Choose"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        game = url
        check()
    }

    func chooseXenon() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.message = "Choose XenonEffects.tpk from your copy of the XenonEffects mod."
        if panel.runModal() == .OK { xenon = panel.url }
    }

    func chooseDest() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
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
            if hasCommandLineTools() {
                self.tools = true
                self.installingTools = false
                t.invalidate()
            }
        }
    }

    func check() {
        guard let game else { return }
        phase = .checking
        gameOK = false
        gameStatus = "Checking… (the first time this also prepares the setup's own tools, about a minute)"
        DispatchQueue.global(qos: .userInitiated).async {
            var ok = false
            var text: String
            do {
                try prepareSource()
                let p = Process()
                p.executableURL = URL(fileURLWithPath: "/bin/sh")
                p.arguments = setupArguments(game: game, dest: self.dest, xenon: nil, checkOnly: true)
                p.currentDirectoryURL = workDir
                p.environment = setupEnvironment()
                let pipe = Pipe()
                p.standardOutput = pipe
                p.standardError = pipe
                try p.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                let out = String(decoding: data, as: UTF8.self).split(separator: "\n").map(String.init)
                ok = p.terminationStatus == 0 && out.contains { $0.contains("Check passed") }
                text = ok ? "Your game is ready to build." : failureSummary(out)
            } catch {
                text = error.localizedDescription
            }
            DispatchQueue.main.async {
                self.gameOK = ok
                self.gameStatus = text
                self.phase = .idle
            }
        }
    }

    func build() {
        guard let game, canBuild else { return }
        do { try prepareSource() } catch {
            phase = .failed
            message = error.localizedDescription
            return
        }
        phase = .building
        cancelled = false
        lines = []
        partial = ""
        step = 0
        stepName = "Starting"
        detail = ""
        progress = 0
        started = Date()
        message = ""
        activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .idleSystemSleepDisabled],
                                                         reason: "Building \(gameAppName)")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = setupArguments(game: game, dest: dest, xenon: xenon, checkOnly: false)
        p.currentDirectoryURL = workDir
        p.environment = setupEnvironment()
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            guard !data.isEmpty else { return }
            let text = String(decoding: data, as: UTF8.self)
            DispatchQueue.main.async { self?.consume(text) }
        }
        p.terminationHandler = { [weak self] p in
            pipe.fileHandleForReading.readabilityHandler = nil
            let rest = pipe.fileHandleForReading.readDataToEndOfFile()
            DispatchQueue.main.async {
                if !rest.isEmpty { self?.consume(String(decoding: rest, as: UTF8.self)) }
                self?.finished(p.terminationStatus)
            }
        }
        do {
            try p.run()
        } catch {
            finished(-1)
            message = error.localizedDescription
            return
        }
        process = p
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in self?.poll() }
    }

    func consume(_ text: String) {
        let all = partial + text
        var parts = all.components(separatedBy: "\n")
        partial = parts.removeLast()
        for line in parts where !line.isEmpty {
            lines.append(line)
            if let s = parseStep(line) {
                step = s.number
                stepName = s.name
                detail = ""
                progress = overallProgress(step: step, within: 0)
            }
        }
        if lines.count > 3000 { lines.removeFirst(lines.count - 3000) }
    }

    func poll() {
        let tail = readLogTail(workDir.appendingPathComponent("build/setup.log"))
        let (fraction, d) = logProgress(tail)
        if let d { detail = d }
        progress = overallProgress(step: step, within: (step == 4 ? fraction : nil) ?? 0)
    }

    func cancel() {
        guard let p = process, p.isRunning else { return }
        cancelled = true
        for pid in descendants(of: p.processIdentifier).reversed() { kill(pid, SIGTERM) }
        p.terminate()
    }

    func finished(_ status: Int32) {
        timer?.invalidate()
        timer = nil
        if let a = activity { ProcessInfo.processInfo.endActivity(a) }
        activity = nil
        process = nil
        if cancelled {
            phase = .cancelled
            message = "Stopped. Nothing in your game folder was changed; Build again to continue."
        } else if status == 0 && FileManager.default.fileExists(atPath: builtApp.path) {
            phase = .done
            progress = 1
            message = "Built \(gameAppName) in \(elapsedText())."
        } else {
            phase = .failed
            message = failureSummary(lines)
        }
    }

    func elapsedText() -> String {
        let s = Int(Date().timeIntervalSince(started))
        return s >= 3600 ? String(format: "%d h %02d min", s / 3600, (s % 3600) / 60)
            : String(format: "%d min %02d s", s / 60, s % 60)
    }

    func openGame() { NSWorkspace.shared.open(builtApp) }
    func showGame() { NSWorkspace.shared.activateFileViewerSelecting([builtApp]) }
    func showLog() { NSWorkspace.shared.activateFileViewerSelecting([workDir.appendingPathComponent("build/setup.log")]) }
}

/// The disk image's "Read Me First" text, also inside the app: some Macs refuse to open the file from the disk image.
func readMeText() -> String {
    guard let url = Bundle.main.url(forResource: "ReadMe", withExtension: "txt"),
          let text = try? String(contentsOf: url, encoding: .utf8) else {
        return "The guides are on the project's GitHub page: github.com/elforeign/nfs-most-wanted-mac"
    }
    return text
}

struct ReadMeView: View {
    @Binding var shown: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ScrollView {
                Text(readMeText()).font(.system(size: 12, design: .monospaced)).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack { Spacer(); Button("Done") { shown = false }.keyboardShortcut(.defaultAction) }
        }
        .padding(20)
        .frame(width: 760, height: 520)
    }
}

struct ContentView: View {
    @ObservedObject var s: Setup
    @State private var showReadMe = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text(gameAppName).font(.largeTitle).bold()
                Spacer()
                Button("Read Me") { showReadMe = true }
            }
            .sheet(isPresented: $showReadMe) { ReadMeView(shown: $showReadMe) }
            Text("Builds the native Mac version of Need for Speed: Most Wanted (2005) from your own copy of the PC game. "
                 + "Everything happens on this Mac; nothing is uploaded, and your game folder is never changed.")
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            GroupBox(label: Text("1. Your game").bold()) {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(s.game?.path ?? "Your installed PC game folder (Black Edition, version 1.3), with speed.exe in it.")
                            .lineLimit(2).truncationMode(.middle)
                            .foregroundStyle(s.game == nil ? .secondary : .primary)
                        Spacer()
                        Button("Choose…") { s.chooseGame() }.disabled(s.busy)
                    }
                    if !s.gameStatus.isEmpty {
                        Label(s.gameStatus, systemImage: s.gameOK ? "checkmark.circle.fill"
                              : (s.phase == .checking ? "hourglass" : "exclamationmark.triangle.fill"))
                            .foregroundStyle(s.gameOK ? .green : (s.phase == .checking ? .secondary : .orange))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading).padding(4)
            }

            GroupBox(label: Text("2. Apple's Command Line Tools").bold()) {
                HStack {
                    if s.tools {
                        Label("Installed.", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    } else if s.installingTools {
                        Label("Finish Apple's installer window; this continues by itself.", systemImage: "hourglass")
                    } else {
                        Text("Needed to build the game (free, from Apple).")
                    }
                    Spacer()
                    if !s.tools && !s.installingTools { Button("Install…") { s.installTools() } }
                }.padding(4)
            }

            GroupBox(label: Text("3. Options").bold()) {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("Put the game in:")
                        Text(s.dest.path).lineLimit(1).truncationMode(.middle)
                        Spacer()
                        Button("Change…") { s.chooseDest() }.disabled(s.busy)
                    }
                    HStack {
                        Text("XenonEffects sparks and light trails (optional):")
                        Text(s.xenon?.lastPathComponent ?? "from your game folder, if it's there").foregroundStyle(.secondary)
                        Spacer()
                        if s.xenon != nil { Button("Remove") { s.xenon = nil }.disabled(s.busy) }
                        Button("Choose…") { s.chooseXenon() }.disabled(s.busy)
                    }
                    Text("Found automatically in your game folder: a TexWizard texture pack (TRACKS/TexWizardX360) and XenonEffects.tpk (scripts). See the guide for adding them.")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(4)
            }

            buildArea
        }
        .padding(20)
        .frame(width: 680)
    }

    @ViewBuilder var buildArea: some View {
        switch s.phase {
        case .building:
            VStack(alignment: .leading, spacing: 8) {
                ProgressView(value: s.progress)
                HStack {
                    Text(s.step > 0 ? "Step \(s.step) of 7: \(s.stepName)" : s.stepName).bold()
                    Spacer()
                    TimelineView(.periodic(from: .now, by: 1)) { _ in Text(s.elapsedText()).monospacedDigit() }
                }
                if !s.detail.isEmpty { Text(s.detail).font(.caption).foregroundStyle(.secondary) }
                Text("The first build takes about 10 minutes on a recent Mac, longer on older ones. Keep the Mac plugged in; it stays awake while this runs.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                logView
                HStack { Spacer(); Button("Stop") { s.cancel() } }
            }
        case .done:
            VStack(alignment: .leading, spacing: 8) {
                Label(s.message, systemImage: "checkmark.circle.fill").foregroundStyle(.green).font(.headline)
                Text("Your saves and settings will be in ~/Library/Application Support/\(gameAppName)/. "
                     + "The first launch takes a little longer while the Mac prepares the game's shaders.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Open the game") { s.openGame() }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent)
                    Button("Show in Finder") { s.showGame() }
                    Spacer()
                }
            }
        case .failed, .cancelled:
            VStack(alignment: .leading, spacing: 8) {
                Label(s.phase == .failed ? "The build stopped." : "Stopped.", systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange).font(.headline)
                Text(s.message).textSelection(.enabled).font(.callout).fixedSize(horizontal: false, vertical: true)
                logView
                HStack {
                    Button("Show the log") { s.showLog() }
                    Spacer()
                    Button("Build again") { s.build() }.disabled(!s.canBuild)
                }
            }
        default:
            HStack {
                if FileManager.default.fileExists(atPath: s.builtApp.path) {
                    Text("\(gameAppName) is already in that folder; building again updates it.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Build") { s.build() }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent)
                    .disabled(!s.canBuild)
            }
        }
    }

    var logView: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 1) {
                    ForEach(Array(s.lines.enumerated()), id: \.offset) { i, line in
                        Text(line).font(.system(size: 11, design: .monospaced)).textSelection(.enabled).id(i)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading).padding(6)
            }
            .frame(height: 150)
            .background(Color(nsColor: .textBackgroundColor))
            .overlay(RoundedRectangle(cornerRadius: 4).stroke(Color.secondary.opacity(0.3)))
            .onChange(of: s.lines.count) { n in if n > 0 { proxy.scrollTo(n - 1, anchor: .bottom) } }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count { runSnapshots(URL(fileURLWithPath: args[i + 1])) }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard Setup.shared.phase == .building else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "Stop building the game?"
        alert.informativeText = "The build stops now. Building again later continues from the one-time analysis if it finished."
        alert.addButton(withTitle: "Stop and Quit")
        alert.addButton(withTitle: "Keep Building")
        guard alert.runModal() == .alertFirstButtonReturn else { return .terminateCancel }
        Setup.shared.cancel()
        return .terminateNow
    }
}

/// Setup --snapshot DIR: renders the window in each state to DIR/<state>.png (sample data; nothing runs), then quits.
func runSnapshots(_ dir: URL) {
    let s = Setup.shared
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let sample = ["3/7 Analysing speed.exe (once; a few minutes)", "  downloading ghidra_12.1.3_PUBLIC_20260817.zip",
                  "  unpacking ghidra_12.1.3_PUBLIC_20260817.zip", "  building Ghidra's decompiler for Apple Silicon (once)",
                  "  $ .venv/bin/python tools/analyze.py --ghidra-home ..."]
    let states: [(String, () -> Void)] = [
        ("1-start", { s.game = nil; s.gameStatus = ""; s.gameOK = false; s.tools = false; s.phase = .idle }),
        ("2-ready", {
            s.game = URL(fileURLWithPath: "/Users/you/Games/Need for Speed Most Wanted")
            s.gameOK = true; s.gameStatus = "Your game is ready to build."; s.tools = true; s.phase = .idle }),
        ("3-wrong-exe", {
            s.gameOK = false; s.phase = .idle
            s.gameStatus = "This speed.exe can't be used. The port is built for the PC Black Edition, version 1.3 (English). Nothing was changed." }),
        ("4-building", {
            s.gameOK = true; s.gameStatus = "Your game is ready to build."; s.phase = .building; s.step = 3
            s.stepName = "Analysing speed.exe (once; a few minutes)"; s.detail = "Analysing: Exported 4250 functions, 4250 decompiled"
            s.progress = overallProgress(step: 3, within: 0.4); s.lines = sample; s.started = Date().addingTimeInterval(-754) }),
        ("5-done", { s.phase = .done; s.progress = 1; s.message = "Built \(gameAppName) in 41 min 12 s." }),
        ("6-failed", { s.phase = .failed; s.lines = sample + ["", "SETUP STOPPED: step failed (exit 1); the last lines of build/setup.log say why"]
            s.message = failureSummary(s.lines) }),
    ]
    var k = 0
    func next() {
        guard k < states.count else { s.phase = .idle; NSApp.terminate(nil); return }
        states[k].1()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
            if let w = NSApp.windows.first(where: { $0.isVisible }), let v = w.contentView {
                // The window's own layer tree, drawn with its background (cacheDisplay misses SwiftUI's text layers).
                let scale = w.backingScaleFactor
                let size = v.bounds.size
                if let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size.width * scale),
                                              pixelsHigh: Int(size.height * scale), bitsPerSample: 8, samplesPerPixel: 4,
                                              hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                              bytesPerRow: 0, bitsPerPixel: 0),
                   let ctx = NSGraphicsContext(bitmapImageRep: rep) {
                    let cg = ctx.cgContext
                    cg.setFillColor(NSColor.windowBackgroundColor.usingColorSpace(.deviceRGB)?.cgColor ?? .white)
                    cg.fill(CGRect(x: 0, y: 0, width: size.width * scale, height: size.height * scale))
                    cg.scaleBy(x: scale, y: scale)
                    if let layer = w.contentView?.superview?.layer ?? v.layer {
                        layer.render(in: cg)
                    }
                    try? rep.representation(using: .png, properties: [:])?.write(to: dir.appendingPathComponent(states[k].0 + ".png"))
                }
            }
            k += 1
            next()
        }
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { next() }
}

/// Setup --selftest GAME OUT: the window's source preparation, check and build, printed to stdout.
func selftest(_ args: [String]) -> Int32 {
    guard args.count >= 2 else { print("usage: Setup --selftest GAME_FOLDER OUT_FOLDER"); return 2 }
    let game = URL(fileURLWithPath: args[0]), dest = URL(fileURLWithPath: args[1])
    do { try prepareSource() } catch { print("prepare failed: \(error.localizedDescription)"); return 1 }
    print("selftest: source \(bundledVersion()) in \(workDir.path); tools installed: \(hasCommandLineTools())")
    for checkOnly in [true, false] {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = setupArguments(game: game, dest: dest, xenon: nil, checkOnly: checkOnly)
        p.currentDirectoryURL = workDir
        p.environment = setupEnvironment()
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch { print("run failed: \(error)"); return 1 }
        var lines: [String] = []
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        for line in String(decoding: data, as: UTF8.self).split(separator: "\n").map(String.init) {
            lines.append(line)
            if let s = parseStep(line) {
                print(String(format: "selftest: step %d (%@) -> progress %.2f", s.number, s.name,
                             overallProgress(step: s.number, within: 0)))
            }
        }
        print("selftest: \(checkOnly ? "check" : "build") exit \(p.terminationStatus)")
        if p.terminationStatus != 0 { print("selftest: failure summary:\n" + failureSummary(lines)); return 1 }
    }
    let (f, d) = logProgress(readLogTail(workDir.appendingPathComponent("build/setup.log")))
    print("selftest: log progress parse: \(f.map { String($0) } ?? "-") \(d ?? "-")")
    let app = dest.appendingPathComponent(gameAppName + ".app")
    print("selftest: app \(FileManager.default.fileExists(atPath: app.path) ? "built" : "MISSING") at \(app.path)")
    return FileManager.default.fileExists(atPath: app.path) ? 0 : 1
}

@main
struct SetupMain {
    static func main() {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--selftest") { exit(selftest(Array(args[(i + 1)...]))) }
        SetupApp.main()
    }
}

struct SetupApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var setup = Setup.shared

    var body: some Scene {
        WindowGroup("\(gameAppName) Setup") { ContentView(s: setup) }
            .windowResizability(.contentSize)
            .commands { CommandGroup(replacing: .newItem) {} }
    }
}
