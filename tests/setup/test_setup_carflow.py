"""Car flow fixture: N=3, old N+4=7 activations / 2 import buttons;
new 1 picker + 1 press / 1 import button (3 activations including Choose/confirm).
After the picker: old N+2=5 presses -> new 1. Source checking was already automatic.
Screen hash strings: old 6 -> new 0; approval words: old 4 -> new 0.
Compiled pinned-original runCars/page controls exercise the same fixture and oracle.
All inputs, selection writes and remembered choices use scratch directories only.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app import compile_app

SWIFT = ROOT / 'tools/installer/SetupApp.swift'

ORIGINAL_RUN = r'''    func runCars(_ mode: RunMode, slots: [Int] = [], generation: String? = nil, measure: Bool = false, repair: Bool = false) {
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
        // Disc installs need the checked copy, which includes the PC update. Extras can read archives directly.
        let mw = managingExtras ? nil : (pcKind == .disc ? workDir().appendingPathComponent("pc_src") : pc)
        let args = carArguments(mode: mode, carbon: carbon, selection: selection,
                                work: workDir(), mw: requiresSource && selection != nil ? mw : nil,
                                slots: slots, generation: generation, measure: measure, repair: repair)
        launch(mode: mode, arguments: args) { [weak self] outcome, state in
            guard let self else { return }
            self.checking = false
            self.extras.finish(outcome, mode: mode)
            if case .success = outcome {
                if mode == .carsCheck || mode == .carsImport, let source = self.carbon {
                    let saved: [String: Any] = ["source": source.path, "selection": self.selectedCarIDs.sorted()]
                    if let data = try? JSONSerialization.data(withJSONObject: saved) {
                        try? data.write(to: supportDir().appendingPathComponent("car-source-choices.json"), options: .atomic)
                    }
                }
                if let result = state.carResult, mode == .carsList { self.installedCars = result }
                if mode == .carsCheck, let recognition = state.recognition {
                    let count = recognition.cars.filter { $0.status == "importable" }.count
                    self.extras.message = "Recognized \(carSourceName(recognition.game, recognition.platform)): \(count) verified source pairs. "
                        + (state.carPlan?.approved == true ? "Selected outputs passed packaged approval. Import to activate them."
                           : recognition.approval_available ? "Packaged approval is available; selected outputs must still verify."
                           : "No packaged output approval is available. Import is unavailable.")
                }
            }
        }
    }

'''
ORIGINAL_RUN_SHA256 = 'bcd835420431976f2fac0cd46c6f10a362856583b883599c2c5184de031432d9'

ORIGINAL_PAGE = r'''    var extrasPage: some View {
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
                    Text("Import cars from your own Carbon PC or Xbox 360, ProStreet PC, or Undercover PC copy. Enable later-game cars in game settings.")
                        .font(.caption).foregroundStyle(.secondary)
                    choice("Later-game source", s.carbon, "Folder or ISO", s.chooseCarbon)
                    if let recognition = s.state.recognition, recognition.approval_available {
                        GroupBox(carSourceName(recognition.game, recognition.platform) + " cars") {
                            VStack(alignment: .leading, spacing: 4) {
                                ForEach(s.carChoices.filter { $0.game == recognition.game && $0.platform == recognition.platform }) { car in
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

'''
ORIGINAL_PAGE_SHA256 = '6244f774a101bb21da1cd4b548fbe04c97609f0e418b54ed33f3ef67125cd4f7'

# Record the real SwiftUI page's strings and actions without displaying a window.
# ForEach is evaluated eagerly so every fixture row participates in the oracle.
RECORDERS = r'''
var fixtureText: [String] = []
var fixtureButtons: [String: (() -> Void, Bool)] = [:]
var fixtureTicks: [String: Binding<Bool>] = [:]
var fixtureCommands: [[String]] = []
var fixtureFailMode: RunMode? = nil
func recordedText(_ value: String) -> Text { fixtureText.append(value); return Text(value) }
struct RecordedButton: View {
    let title: String
    var body: some View { EmptyView() }
    func disabled(_ disabled: Bool) -> RecordedButton {
        if let row = fixtureButtons[title] { fixtureButtons[title] = (row.0, !disabled) }
        return self
    }
}
func recordedButton(_ title: String, action: @escaping () -> Void) -> RecordedButton {
    fixtureButtons[title] = (action, true)
    return RecordedButton(title: title)
}
func recordedToggle(_ title: String, isOn: Binding<Bool>) -> Toggle<Text> {
    fixtureText.append(title); fixtureTicks[title] = isOn
    return Toggle(title, isOn: isOn)
}
func recordedEach<C: RandomAccessCollection, ID: Hashable, V: View>(
    _ rows: C, id: KeyPath<C.Element, ID>, @ViewBuilder content: (C.Element) -> V
) -> EmptyView {
    for row in rows { _ = content(row) }
    return EmptyView()
}
func recordedEach<C: RandomAccessCollection, V: View>(
    _ rows: C, @ViewBuilder content: (C.Element) -> V
) -> EmptyView where C.Element: Identifiable {
    for row in rows { _ = content(row) }
    return EmptyView()
}
func drawCars(_ s: Setup) {
    fixtureText = []; fixtureButtons = [:]; fixtureTicks = [:]
    _ = ContentView(s: s).extrasPage
}
func pressCarButton(_ title: String) {
    guard let (action, enabled) = fixtureButtons[title], enabled else {
        fatalError("Fixture button unavailable: " + title)
    }
    action()
}
'''

TRANSPORT = r'''
        fixtureCommands.append(arguments)
        if fixtureFailMode == mode {
            fixtureFailMode = nil
            finish(.failed(FailureInfo(stage: "cars", message: "approval failed for 0123456789abcdef",
                                       fix: "approved deadbeef", log: "/fixture.log")), state)
            return
        }
        if mode == .carsCheck || mode == .carsImport {
            state.recognition = CarRecognition(game: "carbon", platform: "pc", approval_available: true,
                output_verified: false, cars: carChoices.filter { $0.game == "carbon" }.map {
                    RecognizedCar(car_hash: $0.carHash, status: $0.slot <= 3 ? "importable" : $0.slot == 4 ? "unknown" : "missing")
                })
            if arguments.contains("--cars-spec") {
                state.carPlan = CarPlan(measured: arguments.contains("--cars-measure"),
                    approved: arguments.contains("--cars-measure"), volumes: [], cars: [])
            }
        }
        state.carResult = CarResult(operation: mode.carOperation!, verified: true,
            generation: "g-" + String(repeating: "a", count: 64), message: "", selection: [1, 2, 3],
            cars: mode == .carsList ? [InstalledCar(slot: 1, name: "Fixture car", state: "ready", selected: true)] : [],
            generations: [RetainedGeneration(generation: "g-" + String(repeating: "a", count: 64), verified: true)])
        finish(.success(app: nil), state)
    }

'''

HARNESS = r'''
        if args.count > 1, args[1] == "--carflow-fixture" {
            let s = Setup(product: .mw, rememberedState: supportDir())
            s.managingExtras = true
            s.carChoices = (1...5).map { n in
                CarChoice(game: "carbon", platform: "pc", slot: n, folder: "FIXTURE" + String(n),
                          spec: ["slot": n, "folder": "FIXTURE" + String(n)])
            }
            s.carChoices.append(CarChoice(game: "prostreet", platform: "pc", slot: 6, folder: "OTHER", spec: [:]))
            s.selectedCarIDs = []
            s.installedCars = CarResult(operation: "list", verified: true, generation: "", message: "", selection: [], cars: [],
                generations: [RetainedGeneration(generation: "g-" + String(repeating: "a", count: 64), verified: true)])
            s.chooseCarbon()
            let automaticChecks = fixtureCommands.filter { $0.contains("--cars-check") }.count
            drawCars(s)
            let defaultTicks = s.selectedCarIDs.count
            let initialRetry = fixtureButtons["Retry check"] != nil
            let importButtons = fixtureButtons.keys.filter { $0.lowercased().contains("import") }.count
            let hashes = fixtureText.filter { $0.range(of: #"\b[0-9a-f]{8,}\b"#, options: .regularExpression) != nil }.count
            let jargon = fixtureText.filter { $0.lowercased().contains("approv") }.count
            let sourceRows = fixtureText.filter { $0.contains("Carbon PC — ") }.count
            var presses = 0
            // Only the three passing cars are intended for import in either flow.
            for car in s.carChoices.filter({ $0.slot <= 3 }) where !s.selectedCarIDs.contains(car.id) {
                fixtureTicks[car.name]!.wrappedValue = true; presses += 1
            }
            drawCars(s)
            let importTitle = fixtureButtons["Import selected cars"] != nil ? "Import selected cars" : "Import selected extras"
            if fixtureButtons[importTitle]?.1 != true {
                pressCarButton("Retry check"); presses += 1
                drawCars(s)
            }
            pressCarButton(importTitle); presses += 1
            let refreshed = s.installedCars?.cars.count == 1
            let activation = s.extras.message
            let completed = s.extras.completed && !s.extras.busy
            drawCars(s)
            let postImportHashes = fixtureText.filter { $0.range(of: #"\b[0-9a-f]{8,}\b"#, options: .regularExpression) != nil }.count
            var output: [String: Any] = ["defaultTicks": defaultTicks, "presses": presses,
                "automaticChecks": automaticChecks, "retryInitially": initialRetry,
                "importButtons": importButtons, "hashes": hashes, "jargon": jargon,
                "sourceRows": sourceRows, "refreshed": refreshed, "activation": activation,
                "completed": completed, "postImportHashes": postImportHashes]
            if importTitle == "Import selected cars" {
                pressCarButton("Select none"); drawCars(s)
                output["noneDisabled"] = s.selectedCarIDs.isEmpty && fixtureButtons[importTitle]?.1 == false
                pressCarButton("Select all"); drawCars(s)
                output["allOnlyReady"] = s.selectedCarIDs == Set(s.carChoices.prefix(3).map { $0.id })
                fixtureFailMode = .carsImport
                pressCarButton(importTitle); drawCars(s)
                output["failurePrivate"] = !fixtureText.joined().contains("deadbeef")
                    && !fixtureText.joined().contains("0123456789abcdef") && !fixtureText.joined().contains("approv")
                output["retrySameButton"] = fixtureButtons[importTitle]?.1 == true && s.carImportNeedsRepair
                pressCarButton(importTitle)
                output["repairCommand"] = fixtureCommands.contains { $0.contains("--cars-import") && $0.contains("--cars-repair") }
                output["repairCleared"] = !s.carImportNeedsRepair
                fixtureFailMode = .carsList
                drawCars(s); pressCarButton(importTitle)
                output["refreshFailureKeepsActivation"] = s.extras.completed && s.extras.message.hasPrefix(activation)
                    && s.extras.message.contains("could not be refreshed")
                s.installedCars?.message = "approval failed deadbeef"
                s.state.carPlan = CarPlan(measured: true, approved: false, volumes: [],
                    cars: [PlannedCar(slot: 1, name: "deadbeef", car_hash: "deadbeef", approved: false,
                                      reused: false, reason: "approval 0123456789abcdef")])
                drawCars(s)
                output["technicalDetailsPrivate"] = !fixtureText.joined().contains("deadbeef")
                    && !fixtureText.joined().contains("0123456789abcdef") && !fixtureText.joined().contains("approv")
                fixtureFailMode = .carsCheck
                s.chooseCarbon(); drawCars(s)
                output["checkFailureRetry"] = fixtureButtons["Retry check"]?.1 == true && s.selectedCarIDs.isEmpty
                pressCarButton("Retry check"); drawCars(s)
                output["retryRecovers"] = fixtureButtons["Retry check"] == nil && s.selectedCarIDs.count == 3
                s.runCars(.carsRemove, slots: [1])
                s.runCars(.carsRestore, generation: "g-" + String(repeating: "a", count: 64))
                let ops = fixtureCommands.compactMap { command in command.first { $0.hasPrefix("--cars-") } }
                output["mutationsRefresh"] = ops.suffix(4) == ["--cars-remove", "--cars-list", "--cars-restore", "--cars-list"]
            }
            print(jsonString(output)); exit(0)
        }
'''


def replace_section(source, start, end, replacement):
    a = source.index(start)
    b = source.index(end, a + len(start))
    return source[:a] + replacement + source[b:]


def fixture_source(original=False):
    source = SWIFT.read_text()
    if original:
        for value, digest in ((ORIGINAL_RUN, ORIGINAL_RUN_SHA256), (ORIGINAL_PAGE, ORIGINAL_PAGE_SHA256)):
            assert hashlib.sha256(value.encode()).hexdigest() == digest
        source = replace_section(source, '    func runCars(', '    func skipExtras()', ORIGINAL_RUN)
        source = replace_section(source, '    var extrasPage: some View {', '    var review: some View {', ORIGINAL_PAGE)
    source = replace_section(source, '    func pickFolder(', '    func ', '''    func pickFolder(message: String, files: Bool = false, types: [UTType] = [], folders: Bool = true) -> URL? {
        return supportDir().appendingPathComponent("fixture-source")
    }

''')
    # Keep launch's real state reset and finish path; replace the backend process only.
    a = source.index('        let python = bundledPython()', source.index('    private func launch('))
    b = source.index('    func consume(', a)
    source = source[:a] + TRANSPORT + source[b:]
    a = source.index('    var extrasPage: some View {')
    b = source.index('    var review: some View {', a)
    page = source[a:b]
    for old, new in [('Text(', 'recordedText('), ('Button(', 'recordedButton('),
                     ('Toggle(', 'recordedToggle('), ('ForEach(', 'recordedEach(')]:
        page = page.replace(old, new)
    source = source[:a] + page + source[b:]
    source = source.replace('@main\nstruct Entry', RECORDERS + '\n@main\nstruct Entry')
    source = source.replace('        let args = CommandLine.arguments', '        let args = CommandLine.arguments\n' + HARNESS)
    return source


def run_fixture(original=False):
    binary = compile_app(fixture_source(original))
    with tempfile.TemporaryDirectory(prefix='carflow-', dir=ROOT / '.metal-cache') as tmp:
        state = Path(tmp) / 'state'
        state.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith('NFSMW_SETUP_')}
        env['NFSMW_SETUP_HOME'] = tmp
        result = subprocess.run([str(binary), '--carflow-fixture'], capture_output=True, text=True, env=env, timeout=30)
        assert result.returncode == 0, result.stderr[-2000:]
        return json.loads(result.stdout)


class CarFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = run_fixture(True)
        cls.new = run_fixture()

    def test_presses_and_import_buttons(self):
        self.assertEqual(self.old['automaticChecks'], 1)
        self.assertEqual(self.new['automaticChecks'], 1)
        self.assertEqual((self.old['defaultTicks'], self.old['presses'] + 2, self.old['importButtons']), (0, 7, 2))
        self.assertEqual((self.new['defaultTicks'], self.new['presses'], self.new['importButtons']), (3, 1, 1))
        self.assertTrue(self.old['retryInitially'])
        self.assertFalse(self.new['retryInitially'])
        print('N=3: old N+4=7 activations, 2 import buttons; new 1 picker + 1 press, 1 import button')

    def test_screen_text(self):
        self.assertEqual(self.old['hashes'], 6)
        self.assertGreater(self.old['jargon'], 0)
        self.assertEqual((self.new['hashes'], self.new['jargon'], self.new['postImportHashes']), (0, 0, 0))
        self.assertEqual(self.new['sourceRows'], 5)
        self.assertTrue(self.new['failurePrivate'])
        self.assertTrue(self.new['technicalDetailsPrivate'])
        print('Screen hash strings: old 6 -> new 0; approval words: old %d -> new 0' % self.old['jargon'])

    def test_refresh_and_activation(self):
        self.assertFalse(self.old['refreshed'])
        self.assertTrue(self.new['refreshed'])
        self.assertTrue(self.new['completed'])
        self.assertEqual(self.new['activation'], 'In the game, press F10. Open Cars & cutscenes. Turn on Cars from later games. Restart the game.')
        self.assertTrue(self.new['mutationsRefresh'])
        self.assertTrue(self.new['refreshFailureKeepsActivation'])

    def test_selection_and_recovery(self):
        for key in ('noneDisabled', 'allOnlyReady', 'retrySameButton', 'repairCommand', 'repairCleared',
                    'checkFailureRetry', 'retryRecovers'):
            self.assertTrue(self.new[key], key)


if __name__ == '__main__':
    unittest.main()
