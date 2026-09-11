import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

final class MeetingAudioCapture: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    private let queue = DispatchQueue(label: "meetingbox.audio.capture", qos: .userInitiated)
    private var stream: SCStream?
    private var archives: [String: AudioArchive] = [:]
    private var epoch: Double = 0
    private var firstError: Error?
    private var accepting = false
    var onError: (@Sendable (String) -> Void)?

    @MainActor
    func start(directory: URL) async throws {
        let microphoneGranted: Bool
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: microphoneGranted = true
        case .notDetermined: microphoneGranted = await AVCaptureDevice.requestAccess(for: .audio)
        default: microphoneGranted = false
        }
        guard microphoneGranted else {
            throw AudioPipelineError.message("Microphone access is required. Enable MeetingBox in System Settings → Privacy & Security → Microphone.")
        }
        guard CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess() else {
            throw AudioPipelineError.message("Allow MeetingBox in System Settings → Privacy & Security → Screen & System Audio Recording, then relaunch it. Only audio is saved.")
        }
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else {
            throw AudioPipelineError.message("No display is available for system audio capture.")
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.capturesAudio = true
        configuration.captureMicrophone = true
        configuration.sampleRate = 48_000
        configuration.channelCount = 2
        configuration.excludesCurrentProcessAudio = true
        configuration.width = 2
        configuration.height = 2
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        configuration.showsCursor = false
        let stream = SCStream(filter: filter, configuration: configuration, delegate: self)
        // Deliberately register no screen output and no video/recording output.
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        try stream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: queue)
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            queue.async {
                do {
                    self.archives["remote"] = try AudioArchive(directory: directory, speaker: "remote")
                    self.archives["local"] = try AudioArchive(directory: directory, speaker: "local")
                    self.epoch = CMClockGetTime(CMClockGetHostTimeClock()).seconds
                    self.accepting = true
                    continuation.resume()
                } catch { continuation.resume(throwing: error) }
            }
        }
        self.stream = stream
        do { try await stream.startCapture() }
        catch {
            try? await finishArchives()
            self.stream = nil
            throw error
        }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard accepting, sampleBuffer.isValid, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        let speaker: String
        switch type {
        case .audio: speaker = "remote"
        case .microphone: speaker = "local"
        default: return
        }
        do {
            let buffer = try AudioArchive.pcmBuffer(from: sampleBuffer)
            let seconds = sampleBuffer.presentationTimeStamp.seconds - epoch
            try archives[speaker]?.append(buffer, at: seconds)
        } catch { report(error) }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        queue.async { self.report(error) }
    }

    private func report(_ error: Error) {
        if firstError == nil {
            firstError = error
            onError?("Audio capture: \(error.localizedDescription)")
        }
    }

    @MainActor
    func stop() async throws {
        let stopTime = CMClockGetTime(CMClockGetHostTimeClock()).seconds
        var stopError: Error?
        if let stream {
            do { try await stream.stopCapture() } catch { stopError = error }
        }
        stream = nil
        do { try await finishArchives(endHostTime: stopTime) } catch { if stopError == nil { stopError = error } }
        if let stopError { throw stopError }
    }

    private func finishArchives(endHostTime: Double? = nil) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            queue.async {
                self.accepting = false
                var failure = self.firstError
                for archive in self.archives.values {
                    do { try archive.finish(at: endHostTime.map { $0 - self.epoch }) }
                    catch { if failure == nil { failure = error } }
                }
                self.archives.removeAll()
                if let failure { continuation.resume(throwing: failure) }
                else { continuation.resume() }
            }
        }
    }
}

enum AudioFileImporter {
    static func run(source: URL, directory: URL) async throws {
        try await Task.detached(priority: .userInitiated) {
            let fileExtension = source.pathExtension.lowercased()
            guard ["mp3", "wav", "m4a", "caf"].contains(fileExtension) else {
                throw AudioPipelineError.message("Choose an MP3, WAV, M4A, or saved CAF recording.")
            }
            let audioDirectory = directory.appendingPathComponent("audio", isDirectory: true)
            try FileManager.default.createDirectory(at: audioDirectory, withIntermediateDirectories: true)
            let archivedSource = audioDirectory.appendingPathComponent("original.\(fileExtension)")
            let accessed = source.startAccessingSecurityScopedResource()
            defer { if accessed { source.stopAccessingSecurityScopedResource() } }
            try FileManager.default.copyItem(at: source, to: archivedSource)
            let input = try AVAudioFile(forReading: archivedSource)
            let archive = try AudioArchive(directory: directory, speaker: "unknown")
            guard let buffer = AVAudioPCMBuffer(pcmFormat: input.processingFormat, frameCapacity: 8_192) else {
                throw AudioPipelineError.message("Cannot decode the recording's audio format.")
            }
            do {
                while input.framePosition < input.length {
                    let count = AVAudioFrameCount(min(AVAudioFramePosition(buffer.frameCapacity), input.length - input.framePosition))
                    try input.read(into: buffer, frameCount: count)
                    if buffer.frameLength == 0 { break }
                    try archive.append(buffer, at: nil)
                }
                try archive.finish()
                try AudioIngestCheckpoint.complete(directory: directory, mode: "import")
            } catch {
                try? archive.finish()
                throw error
            }
        }.value
    }
}
