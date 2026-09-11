import AppKit
import SwiftUI
import UniformTypeIdentifiers

@main
struct MeetingBoxApp: App {
    @StateObject private var model = AppModel()
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        WindowGroup {
            MeetingWindow(model: model)
                .frame(minWidth: 960, minHeight: 660)
                .onAppear { delegate.model = model; NSApp.setActivationPolicy(.regular) }
        }
        .defaultSize(width: 1120, height: 760)
        .commands { CommandGroup(replacing: .newItem) {} }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: AppModel?
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model, model.activeID != nil || model.isBusy else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "Finish this recording before quitting"
        alert.informativeText = "Stop recording and let the remaining audio transcribe. Your audio and transcript are being saved on this Mac."
        alert.addButton(withTitle: "Keep MeetingBox Open")
        alert.runModal()
        return .terminateCancel
    }
}

struct MeetingWindow: View {
    @ObservedObject var model: AppModel
    @State private var title = ""
    @State private var showSettings = false
    @State private var selectedTab = 0

    var body: some View {
        NavigationSplitView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(spacing: 10) {
                    Image(systemName: "waveform.circle.fill").font(.system(size: 30)).foregroundStyle(.teal)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("MeetingBox").font(.headline)
                        Text("YOUR PRIVATE MEETING SPACE").font(.system(size: 8, weight: .semibold)).tracking(1)
                            .foregroundStyle(.secondary)
                    }
                }.padding(.horizontal, 16).padding(.top, 24)

                Text("MEETINGS").font(.caption2.weight(.semibold)).foregroundStyle(.secondary).tracking(1.4).padding(.horizontal, 18)
                List(selection: $model.selectedID) {
                    ForEach(model.meetings) { meeting in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(meeting.title).font(.system(size: 13, weight: .medium)).lineLimit(1)
                            HStack {
                                Text(meeting.createdAt, style: .date)
                                Spacer()
                                if meeting.id == model.activeID { Image(systemName: "record.circle").foregroundStyle(.red) }
                                else if meeting.pendingCount > 0 { Image(systemName: "arrow.triangle.2.circlepath") }
                                else if meeting.report != nil { Image(systemName: "checkmark.circle").foregroundStyle(.teal) }
                            }.font(.caption2).foregroundStyle(.secondary)
                        }.padding(.vertical, 6).tag(meeting.id)
                    }
                }.listStyle(.sidebar)

                VStack(alignment: .leading, spacing: 8) {
                    Label("Audio stays on this Mac", systemImage: "lock.shield").font(.caption)
                    Text(model.connection).font(.caption2).foregroundStyle(.secondary)
                    Button { showSettings = true } label: { Label("Settings", systemImage: "gearshape") }
                        .buttonStyle(.plain).padding(.top, 6)
                }.padding(18)
            }.navigationSplitViewColumnWidth(min: 240, ideal: 255, max: 300)
        } detail: {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 12) {
                    TextField("Name your meeting…", text: $title).textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                        .disabled(model.activeID != nil || model.isBusy)
                    Spacer()
                    Button { importAudio() } label: { Label("Import audio", systemImage: "square.and.arrow.down") }
                        .disabled(model.activeID != nil || model.isBusy)
                    if model.activeID != nil {
                        Button(role: .destructive) { Task { await model.stop() } } label: {
                            Label(model.isBusy ? "Processing…" : "Stop recording", systemImage: "stop.fill")
                        }.disabled(model.isBusy).buttonStyle(.borderedProminent).tint(.red)
                    } else {
                        Button { Task { await model.start(title: title) } } label: { Label("Record meeting", systemImage: "record.circle") }
                            .disabled(model.isBusy).buttonStyle(.borderedProminent).tint(.teal)
                    }
                }.padding(24)
                Divider()

                if let meeting = model.selected {
                    VStack(alignment: .leading, spacing: 18) {
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 8) {
                                Text(meeting.title).font(.system(size: 28, weight: .semibold))
                                HStack(spacing: 12) {
                                    Label(meeting.id == model.activeID ? (model.isBusy ? "Processing audio" : "Recording") : meeting.localState.replacingOccurrences(of: "_", with: " ").capitalized,
                                          systemImage: meeting.id == model.activeID ? "record.circle.fill" : "externaldrive")
                                        .foregroundStyle(meeting.id == model.activeID ? Color.red : Color.secondary)
                                    Text("\(meeting.segments.count) segments")
                                    if meeting.pendingCount > 0 { Text("\(meeting.pendingCount) awaiting sync") }
                                }.font(.caption)
                            }
                            Spacer()
                            Menu {
                                Button("Show local files", action: model.revealRecording)
                                Button("Export JSON", action: model.exportSelected)
                                Button("Sync all meetings") { Task { await model.syncNow(forceAll: true) } }
                            } label: { Image(systemName: "ellipsis.circle").font(.title2) }.menuStyle(.borderlessButton).frame(width: 32)
                        }

                        if model.pendingAudio > 0 && meeting.id == model.activeID {
                            Label("\(model.pendingAudio) audio chunks waiting for transcription", systemImage: "waveform")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if !meeting.ended && model.activeID == nil {
                            HStack {
                                Label("Finish saved audio to generate a report", systemImage: "arrow.clockwise")
                                Spacer()
                                Button("Retry transcription") { Task { await model.retryTranscription() } }.disabled(model.isBusy)
                            }.font(.caption).padding(12).background(.orange.opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
                        }
                        if let error = meeting.error {
                            Text(error).font(.caption).foregroundStyle(.orange).textSelection(.enabled)
                        }
                        if meeting.hubStatus == "failed" {
                            Button("Retry report on hub") { Task { await model.retryReport() } }
                        }
                        Picker("View", selection: $selectedTab) {
                            Text("Transcript").tag(0)
                            Text("Report").tag(1)
                        }.pickerStyle(.segmented).frame(width: 260)
                        if selectedTab == 0 { transcript(meeting) } else { report(meeting) }
                    }.padding(24)
                } else {
                    Spacer()
                    VStack(spacing: 18) {
                        Image(systemName: "waveform.badge.mic").font(.system(size: 56, weight: .light)).foregroundStyle(.teal)
                        Text("Be present. Keep the details.").font(.system(size: 27, weight: .medium))
                        Text("Record a meeting or import an MP3, WAV, or M4A.\nTranscribe on this Mac. Turn the conversation into a report on your private hub.")
                            .multilineTextAlignment(.center).foregroundStyle(.secondary).lineSpacing(5)
                        Button("Configure local models and hub") { showSettings = true }.padding(.top, 4)
                    }.frame(maxWidth: .infinity)
                    Spacer()
                }
                Divider()
                HStack {
                    Image(systemName: "network").foregroundStyle(.teal)
                    Text(model.connection)
                    Spacer()
                    Text("Let participants know before recording.")
                }.font(.caption2).foregroundStyle(.secondary).padding(.horizontal, 24).padding(.vertical, 12)
            }.background(Color(nsColor: .windowBackgroundColor))
        }
        .sheet(isPresented: $showSettings) { SettingsView(model: model) }
        .alert("MeetingBox", isPresented: Binding(get: { model.notice != nil }, set: { if !$0 { model.notice = nil } })) {
            Button("OK") { model.notice = nil }
        } message: { Text(model.notice ?? "") }
    }

    private func transcript(_ meeting: LocalMeeting) -> some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 20) {
                if meeting.segments.isEmpty {
                    ContentUnavailableView("Your conversation appears here", systemImage: "text.bubble",
                                           description: Text("Audio is transcribed locally in short chunks. The full recording is saved throughout the meeting."))
                }
                ForEach(meeting.segments.sorted { $0.start == $1.start ? $0.sequence < $1.sequence : $0.start < $1.start }) { segment in
                    HStack(alignment: .top, spacing: 16) {
                        Text(timestamp(segment.start)).monospacedDigit().font(.caption).foregroundStyle(.secondary).frame(width: 44, alignment: .leading)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(segment.speaker == "local" ? "You · microphone" : segment.speaker == "remote" ? "Meeting audio" : "Imported recording")
                                .font(.caption.weight(.semibold)).foregroundStyle(.teal)
                            Text(segment.text).font(.system(size: 14)).lineSpacing(5).textSelection(.enabled)
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            }.padding(.vertical, 12)
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func report(_ meeting: LocalMeeting) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if let report = meeting.report {
                    if !meeting.ended || !["completed", "complete"].contains(meeting.hubStatus) {
                        Label("Provisional · final report follows the complete transcript", systemImage: "clock").font(.caption).foregroundStyle(.orange)
                    }
                    reportSection("Summary") { Text(report.summary).lineSpacing(5) }
                    if !report.decisions.isEmpty {
                        reportSection("Decisions") {
                            ForEach(Array(report.decisions.enumerated()), id: \.offset) { _, item in
                                VStack(alignment: .leading, spacing: 4) {
                                    Label(item.text, systemImage: "checkmark.circle").foregroundStyle(.primary)
                                    evidence(item.evidence, meeting: meeting)
                                }
                            }
                        }
                    }
                    if !report.actionItems.isEmpty {
                        reportSection("Action items") {
                            ForEach(Array(report.actionItems.enumerated()), id: \.offset) { _, item in
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(item.task).fontWeight(.medium)
                                    Text("\(item.owner ?? "Unassigned")  ·  \(item.due ?? "No deadline stated")").font(.caption).foregroundStyle(.secondary)
                                    evidence(item.evidence, meeting: meeting)
                                }.padding(14).frame(maxWidth: .infinity, alignment: .leading).background(.teal.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                            }
                        }
                    }
                    if !report.openQuestions.isEmpty { reportSection("Open questions") { strings(report.openQuestions) } }
                    if !report.topics.isEmpty { reportSection("Topics") { strings(report.topics) } }
                    if !report.risks.isEmpty { reportSection("Risks") { strings(report.risks) } }
                } else {
                    ContentUnavailableView("Waiting for meeting intelligence", systemImage: "sparkle",
                        description: Text("Hub status: \(meeting.hubStatus). Reports require your local Qwen model. Your transcript remains available on this Mac."))
                }
            }.textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 12)
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func reportSection<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) { Text(title).font(.title3.weight(.semibold)); content() }
    }
    private func strings(_ items: [String]) -> some View {
        ForEach(Array(items.enumerated()), id: \.offset) { _, text in Text("•  \(text)") }
    }
    private func evidence(_ sequences: [Int], meeting: LocalMeeting) -> some View {
        Text("Source: " + sequences.map { sequence in
            meeting.segments.first { $0.sequence == sequence }.map { timestamp($0.start) } ?? "segment \(sequence)"
        }.joined(separator: ", ")).font(.caption2).foregroundStyle(.secondary)
    }
    private func importAudio() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.mp3, .wav, .mpeg4Audio, UTType(filenameExtension: "caf") ?? .audio]
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task { await model.importFile(url) }
    }
}

struct SettingsView: View {
    @ObservedObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Your local setup").font(.title2.weight(.semibold))
            Form {
                TextField("Hub address", text: $model.hubAddress)
                SecureField("Pairing token", text: $model.token)
                TextField("Whisper executable", text: $model.whisperBinary)
                HStack {
                    TextField("Whisper model", text: $model.whisperModel)
                    Button("Choose…") {
                        let panel = NSOpenPanel()
                        if panel.runModal() == .OK, let url = panel.url { model.whisperModel = url.path }
                    }
                }
                TextField("Language", text: $model.language)
            }.textFieldStyle(.roundedBorder)
            Text("Use a downloaded whisper.cpp model. Language can be auto, en, ru, or another Whisper language code. The hub runs Qwen locally; no models are downloaded by this app.")
                .font(.caption).foregroundStyle(.secondary)
            Text("LAN HTTP is intended for a trusted demo network. Configure HTTPS on the hub for encrypted transport.")
                .font(.caption).foregroundStyle(.secondary)
            HStack { Spacer(); Button("Done") { if model.saveSettings() { dismiss() } }.buttonStyle(.borderedProminent).tint(.teal) }
        }.padding(28).frame(width: 620)
    }
}
