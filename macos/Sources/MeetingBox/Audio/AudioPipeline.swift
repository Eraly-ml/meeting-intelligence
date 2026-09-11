import Combine
import Foundation

@MainActor
final class AudioPipeline: ObservableObject {
    @Published private(set) var isCapturing = false
    @Published private(set) var pendingCount = 0
    var configuration: WhisperConfiguration
    var onSegment: ((AudioTranscript) throws -> Void)?
    var onError: ((String) -> Void)?
    /// A fatal capture/storage error needs immediate UI state change and stop().
    var onCaptureStopped: (() -> Void)?
    var onPendingCount: ((Int) -> Void)?

    private let worker = WhisperWorker()
    private var capture: MeetingAudioCapture?
    private var processingTask: Task<Void, Never>?
    private var processingError: Error?
    private var directory: URL?
    private var ingesting = false

    init(configuration: WhisperConfiguration) { self.configuration = configuration }

    func start(directory: URL) async throws {
        guard !isCapturing, !ingesting, processingTask == nil else {
            throw AudioPipelineError.message("Finish the current recording or transcription first.")
        }
        try configuration.validate()
        self.directory = directory
        self.processingError = nil
        let session = MeetingAudioCapture()
        session.onError = { [weak self] message in
            Task { @MainActor in
                self?.onError?(message)
                self?.onCaptureStopped?()
            }
        }
        try await session.start(directory: directory)
        capture = session
        isCapturing = true
        startProcessing()
    }

    /// Stops recording first, then waits for every durable chunk to be transcribed
    /// and accepted by the application. A failure leaves the chunks retryable.
    func stop() async throws {
        var captureError: Error?
        if let capture {
            do { try await capture.stop() } catch { captureError = error }
            if captureError == nil, let directory {
                do { try AudioIngestCheckpoint.complete(directory: directory, mode: "capture") }
                catch { captureError = error }
            }
        }
        self.capture = nil
        isCapturing = false
        await processingTask?.value
        processingTask = nil
        if let directory, let pending = try? await worker.pending(in: directory) {
            pendingCount = pending.count
            onPendingCount?(pending.count)
        }
        if let processingError { throw processingError }
        if let captureError { throw captureError }
    }

    func importFile(_ source: URL, directory: URL) async throws {
        guard !isCapturing, !ingesting, processingTask == nil else {
            throw AudioPipelineError.message("Finish the current recording or transcription first.")
        }
        try configuration.validate()
        self.directory = directory
        processingError = nil
        ingesting = true
        startProcessing()
        var importError: Error?
        do { try await AudioFileImporter.run(source: source, directory: directory) }
        catch { importError = error }
        ingesting = false
        await processingTask?.value
        processingTask = nil
        if let importError { throw importError }
        if let processingError { throw processingError }
    }

    /// Safe after relaunch: metadata, cached results and acknowledgement markers
    /// are stored in the meeting directory, and segment IDs survive every retry.
    func retryPending(directory: URL) async throws {
        guard !isCapturing, !ingesting else {
            throw AudioPipelineError.message("Stop recording before retrying pending transcription.")
        }
        try AudioIngestCheckpoint.requireComplete(directory: directory)
        await processingTask?.value
        processingTask = nil
        self.directory = directory
        processingError = nil
        startProcessing()
        await processingTask?.value
        processingTask = nil
        if let processingError { throw processingError }
    }

    private func startProcessing() {
        guard let directory else { return }
        let configuration = self.configuration
        processingTask = Task { [weak self] in
            guard let self else { return }
            do {
                while true {
                    let pending = try await worker.pending(in: directory)
                    pendingCount = pending.count
                    onPendingCount?(pending.count)
                    guard let chunk = pending.first else {
                        if !isCapturing && !ingesting { break }
                        try await Task.sleep(for: .milliseconds(400))
                        continue
                    }
                    let segments = try await worker.transcribe(chunk, in: directory, configuration: configuration)
                    guard let onSegment else {
                        throw AudioPipelineError.message("No transcript storage callback is configured. Audio remains pending.")
                    }
                    for segment in segments { try onSegment(segment) }
                    try await worker.acknowledge(chunk, in: directory)
                }
            } catch {
                processingError = error
                onError?(error.localizedDescription)
                // Capture continues on its own queue even after inference has failed.
                while isCapturing || ingesting {
                    if let pending = try? await worker.pending(in: directory) {
                        pendingCount = pending.count
                        onPendingCount?(pending.count)
                    }
                    try? await Task.sleep(for: .milliseconds(400))
                }
            }
        }
    }
}
