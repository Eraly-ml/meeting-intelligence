import AppKit
import Combine
import Foundation

@MainActor
final class AppModel: ObservableObject {
    @Published var meetings: [LocalMeeting] = []
    @Published var selectedID: String? { didSet { watchSelectedMeeting() } }
    @Published var activeID: String?
    @Published var isBusy = false
    @Published var pendingAudio = 0
    @Published var connection = "Set up your hub"
    @Published var notice: String?
    @Published var hubAddress: String
    @Published var token: String
    @Published var whisperBinary: String
    @Published var whisperModel: String
    @Published var language: String
    private(set) var repository: MeetingRepository?
    private var audio: AudioPipeline?
    private var syncTask: Task<Void, Never>?
    private var watchTask: Task<Void, Never>?
    private var syncing = false
    private var lastSyncError: String?

    var selected: LocalMeeting? { meetings.first { $0.id == selectedID } }
    var recording: Bool { activeID != nil && !isBusy }

    init() {
        let defaults = UserDefaults.standard
        hubAddress = defaults.string(forKey: "hubAddress") ?? "http://localhost:8000"
        whisperBinary = defaults.string(forKey: "whisperBinary") ?? "/opt/homebrew/bin/whisper-cli"
        whisperModel = defaults.string(forKey: "whisperModel") ?? ""
        language = defaults.string(forKey: "language") ?? "auto"
        token = TokenStore.read()
        do {
            let repo = try MeetingRepository()
            repository = repo
            let loaded = try repo.loadWithIssues()
            meetings = loaded.meetings
            if !loaded.issues.isEmpty { notice = loaded.issues.joined(separator: "\n") }
            for index in meetings.indices where !meetings[index].ended {
                meetings[index].localState = "interrupted"
                try repo.save(meetings[index])
            }
            selectedID = meetings.first?.id
        } catch { notice = "Could not load local meetings: \(error.localizedDescription)" }
        syncTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.syncNow()
                try? await Task.sleep(for: .seconds(4))
            }
        }
        watchSelectedMeeting()
    }

    @discardableResult
    func saveSettings() -> Bool {
        do {
            if !token.isEmpty { _ = try HubConfiguration(address: hubAddress, token: token) }
            try TokenStore.save(token)
            let defaults = UserDefaults.standard
            defaults.set(hubAddress, forKey: "hubAddress")
            defaults.set(whisperBinary, forKey: "whisperBinary")
            defaults.set(whisperModel, forKey: "whisperModel")
            defaults.set(language, forKey: "language")
            watchSelectedMeeting()
            Task { await syncNow() }
            return true
        } catch { notice = error.localizedDescription; return false }
    }

    private func update(_ id: String, _ mutate: (inout LocalMeeting) throws -> Void) throws {
        guard let index = meetings.firstIndex(where: { $0.id == id }), let repository else {
            throw MeetingBoxError.message("Local meeting storage is unavailable.")
        }
        var next = meetings[index]
        try mutate(&next)
        try repository.save(next)
        meetings[index] = next
    }

    private func createMeeting(title: String) throws -> LocalMeeting {
        guard let repository else { throw MeetingBoxError.message("Local storage is unavailable.") }
        let title = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let meeting = LocalMeeting(id: UUID().uuidString.lowercased(), title: title.isEmpty ? "Untitled meeting" : String(title.prefix(200)), createdAt: Date())
        try repository.save(meeting)
        meetings.insert(meeting, at: 0)
        selectedID = meeting.id
        return meeting
    }

    private func pipeline(for id: String) -> AudioPipeline {
        let pipeline = AudioPipeline(configuration: WhisperConfiguration(binaryPath: whisperBinary, modelPath: whisperModel, language: language))
        pipeline.onSegment = { [weak self] segment in
            guard let self else { throw MeetingBoxError.message("Transcript storage is unavailable.") }
            try self.update(id) { meeting in
                try meeting.append(sourceID: segment.id, start: segment.start, end: segment.end, speaker: segment.speaker, text: segment.text)
            }
        }
        pipeline.onError = { [weak self] message in
            self?.notice = message
            do { try self?.update(id) { $0.error = message } } catch { self?.notice = error.localizedDescription }
        }
        pipeline.onPendingCount = { [weak self] count in self?.pendingAudio = count }
        pipeline.onCaptureStopped = { [weak self] in
            guard let self else { return }
            try? self.update(id) { $0.localState = "capture_error" }
            Task { [weak self] in
                guard let self else { return }
                while self.isBusy && self.activeID == id { try? await Task.sleep(for: .milliseconds(100)) }
                if self.activeID == id { await self.stop() }
            }
        }
        return pipeline
    }

    func start(title: String) async {
        guard activeID == nil, !isBusy else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            try WhisperConfiguration(binaryPath: whisperBinary, modelPath: whisperModel, language: language).validate()
            let meeting = try createMeeting(title: title)
            activeID = meeting.id
            let pipeline = pipeline(for: meeting.id)
            audio = pipeline
            try update(meeting.id) { $0.localState = "recording" }
            try await pipeline.start(directory: repository!.directory(for: meeting.id))
        } catch {
            if let id = activeID { try? update(id) { $0.localState = "needs_retry"; $0.error = error.localizedDescription } }
            activeID = nil
            notice = error.localizedDescription
        }
    }

    func stop() async {
        guard let id = activeID, let audio, !isBusy else { return }
        isBusy = true
        defer { isBusy = false; activeID = nil }
        do {
            // A metadata write failure must never prevent the microphone from stopping.
            do { try update(id) { $0.localState = "transcribing" } }
            catch { notice = error.localizedDescription }
            try await audio.stop()
            try finish(id)
        } catch {
            try? update(id) { $0.localState = "needs_retry"; $0.error = error.localizedDescription }
            notice = "Recording is saved locally. \(error.localizedDescription)"
        }
        await syncNow()
    }

    func importFile(_ source: URL) async {
        guard activeID == nil, !isBusy else { return }
        isBusy = true
        defer { isBusy = false; activeID = nil }
        do {
            try WhisperConfiguration(binaryPath: whisperBinary, modelPath: whisperModel, language: language).validate()
            let meeting = try createMeeting(title: source.deletingPathExtension().lastPathComponent)
            activeID = meeting.id
            let pipeline = pipeline(for: meeting.id)
            audio = pipeline
            try update(meeting.id) { $0.localState = "importing" }
            try await pipeline.importFile(source, directory: repository!.directory(for: meeting.id))
            try finish(meeting.id)
        } catch {
            if let id = activeID { try? update(id) { $0.localState = "needs_retry"; $0.error = error.localizedDescription } }
            notice = error.localizedDescription
        }
        await syncNow()
    }

    func retryTranscription() async {
        guard let id = selectedID, let directory = repository?.directory(for: id), activeID == nil, !isBusy else { return }
        isBusy = true
        defer { isBusy = false; activeID = nil }
        do {
            activeID = id
            let pipeline = pipeline(for: id)
            audio = pipeline
            try update(id) { $0.localState = "transcribing"; $0.error = nil }
            try await pipeline.retryPending(directory: directory)
            try finish(id)
        } catch {
            try? update(id) { $0.localState = "needs_retry"; $0.error = error.localizedDescription }
            notice = error.localizedDescription
        }
        await syncNow()
    }

    private func finish(_ id: String) throws {
        try update(id) { $0.ended = true; $0.localState = "saved"; $0.error = nil }
    }

    func syncNow(forceAll: Bool = false) async {
        guard !syncing else { return }
        let client: HubClient
        do { client = HubClient(configuration: try HubConfiguration(address: hubAddress, token: token)) }
        catch { connection = "Set up your hub"; return }
        syncing = true
        defer { syncing = false }
        var hadSyncError = false
        for id in meetings.map(\.id) {
            guard let initial = meetings.first(where: { $0.id == id }) else { continue }
            if !forceAll, id != selectedID, initial.endSynced, initial.hubStatus == "complete", initial.pendingCount == 0 { continue }
            do {
                try await client.start(initial)
                // Reconcile ACK from the server after reconnect/restart, including a rebuilt hub database.
                let snapshot = try await client.snapshot(id)
                try update(id) { try $0.reconcileAcknowledgment(snapshot.lastSequence, hubStatus: snapshot.status) }
                if let current = meetings.first(where: { $0.id == id }) {
                    let pending = Array(current.segments.filter { $0.sequence > current.ackSequence }.prefix(100))
                    if !pending.isEmpty {
                        let ack = try await client.send(pending, meetingID: id)
                        guard ack >= current.ackSequence, ack <= current.segments.count else { throw MeetingBoxError.message("Invalid transcript acknowledgment from hub.") }
                        try update(id) { $0.ackSequence = ack }
                    }
                }
                if let current = meetings.first(where: { $0.id == id }), current.ended, !current.endSynced, current.ackSequence == current.segments.count {
                    try await client.end(current)
                    try update(id) { $0.endSynced = true }
                }
                try apply(try await client.snapshot(id))
            } catch {
                hadSyncError = true
                connection = "Sync paused · saved on this Mac"
                // Surface auth/schema failures without replacing the capture/transcription error.
                if !(error is URLError), lastSyncError != error.localizedDescription {
                    notice = error.localizedDescription
                    lastSyncError = error.localizedDescription
                }
                if error is URLError { break }
            }
        }
        if !hadSyncError { connection = "Hub connected"; lastSyncError = nil }
        if meetings.isEmpty { connection = "Ready to record locally" }
    }

    private func apply(_ snapshot: HubMeeting) throws {
        try update(snapshot.meetingID) { meeting in
            meeting.hubStatus = snapshot.status
            meeting.report = snapshot.report
            if let error = snapshot.error { meeting.error = error }
            else if meeting.ended { meeting.error = nil }
        }
    }

    func retryReport() async {
        guard let id = selectedID else { return }
        do {
            let client = HubClient(configuration: try HubConfiguration(address: hubAddress, token: token))
            try await client.retryReport(id)
            try apply(try await client.snapshot(id))
        } catch { notice = error.localizedDescription }
    }

    private func watchSelectedMeeting() {
        watchTask?.cancel()
        guard let id = selectedID, let configuration = try? HubConfiguration(address: hubAddress, token: token) else { return }
        watchTask = Task { [weak self] in
            let client = HubClient(configuration: configuration)
            while !Task.isCancelled {
                do {
                    try await client.snapshots(id) { [weak self] snapshot in
                        do { try self?.apply(snapshot) } catch { self?.notice = error.localizedDescription }
                    }
                } catch { /* HTTP sync also recovers snapshots; reconnect below. */ }
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }

    func revealRecording() {
        if let id = selectedID, let url = repository?.directory(for: id) { NSWorkspace.shared.activateFileViewerSelecting([url]) }
    }

    func exportSelected() {
        guard let meeting = selected else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "meeting-\(meeting.id.prefix(8)).json"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            try encoder.encode(meeting).write(to: url, options: .atomic)
        } catch { notice = error.localizedDescription }
    }
}
