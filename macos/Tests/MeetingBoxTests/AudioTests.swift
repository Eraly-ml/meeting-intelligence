import AVFoundation
import XCTest
@testable import MeetingBox

final class AudioTests: XCTestCase {
    private func temporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("MeetingBoxAudioTests-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try FileManager.default.removeItem(at: url) }
        return url
    }

    func testWhisperOffsetsUseMillisecondsAndPreserveStableIDs() throws {
        let chunk = AudioChunk(id: "stable", speaker: "remote", start: 24, duration: 1.5, filename: "test.wav")
        let json = Data(#"{"transcription":[{"offsets":{"from":500,"to":2000},"text":" Test "},{"offsets":{"from":2500,"to":3000},"text":"out of range"}]}"#.utf8)
        let parsed = try WhisperJSONParser.parse(json, chunk: chunk)
        XCTAssertEqual(parsed.count, 1)
        XCTAssertEqual(parsed[0].id, "stable:0")
        XCTAssertEqual(parsed[0].speaker, "remote")
        XCTAssertEqual(parsed[0].start, 24.5)
        XCTAssertEqual(parsed[0].end, 25.5)
        XCTAssertEqual(parsed[0].text, "Test")
        XCTAssertEqual(try WhisperJSONParser.parse(json, chunk: chunk)[0].id, parsed[0].id)
        let invalid = Data(#"{"transcription":[{"offsets":{"from":500,"to":100},"text":"invalid"}]}"#.utf8)
        XCTAssertThrowsError(try WhisperJSONParser.parse(invalid, chunk: chunk))
    }

    func testResamplingPreservesInitialDelayAndCaptureGap() async throws {
        let directory = try temporaryDirectory()
        let sourceFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48_000, channels: 2, interleaved: false)!
        let buffer = AVAudioPCMBuffer(pcmFormat: sourceFormat, frameCapacity: 24_000)!
        buffer.frameLength = 24_000
        for channel in 0..<2 {
            for frame in 0..<24_000 {
                buffer.floatChannelData![channel][frame] = 0.2 * sin(Float(frame) * .pi * 2 * 440 / 48_000)
            }
        }
        let archive = try AudioArchive(directory: directory, speaker: "local")
        try archive.append(buffer, at: 0.25)
        try archive.append(buffer, at: 1.25)
        try archive.finish(at: 2)
        let file = try AVAudioFile(forReading: directory.appendingPathComponent("audio/local.caf"))
        XCTAssertEqual(file.processingFormat.sampleRate, 16_000)
        XCTAssertEqual(file.processingFormat.channelCount, 1)
        XCTAssertEqual(file.length, 32_000)
        let normalized = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length))!
        try file.read(into: normalized)
        XCTAssertEqual(normalized.floatChannelData![0][2_000], 0, accuracy: 0.00001)
        XCTAssertEqual(normalized.floatChannelData![0][16_000], 0, accuracy: 0.00001)
        XCTAssertEqual(normalized.floatChannelData![0][30_000], 0, accuracy: 0.00001)
        XCTAssertGreaterThan(abs(normalized.floatChannelData![0][4_011]), 0.05)
        let chunks = try await WhisperWorker().pending(in: directory)
        XCTAssertEqual(chunks.count, 1)
        XCTAssertEqual(chunks[0].start, 0)
        XCTAssertEqual(chunks[0].duration, 2)
    }

    func testImportCopiesOriginalAndFlushesFinalShortChunk() async throws {
        let directory = try temporaryDirectory()
        let source = directory.appendingPathComponent("source.wav")
        let original = AudioArchive.wavData([Float](repeating: 0.1, count: 208_000))
        try original.write(to: source)
        let destination = directory.appendingPathComponent("meeting")
        try await AudioFileImporter.run(source: source, directory: destination)
        XCTAssertEqual(try Data(contentsOf: destination.appendingPathComponent("audio/original.wav")), original)
        let chunks = try await WhisperWorker().pending(in: destination)
        XCTAssertEqual(chunks.count, 2)
        XCTAssertEqual(chunks.map(\.duration), [12, 1])
        XCTAssertEqual(chunks.map(\.start), [0, 12])
        XCTAssertTrue(chunks.allSatisfy { $0.speaker == "unknown" })
        XCTAssertNoThrow(try AudioIngestCheckpoint.requireComplete(directory: destination))
    }

    func testWAVIsSigned16BitPCMAndHandlesNonfiniteValues() throws {
        let directory = try temporaryDirectory()
        let fileURL = directory.appendingPathComponent("pcm.wav")
        let data = AudioArchive.wavData([0, 1, -1, .nan])
        XCTAssertEqual(data.count, 52)
        try data.write(to: fileURL)
        let file = try AVAudioFile(forReading: fileURL)
        XCTAssertEqual(file.fileFormat.commonFormat, .pcmFormatInt16)
        XCTAssertEqual(file.fileFormat.sampleRate, 16_000)
        XCTAssertEqual(file.length, 4)
    }

    func testInterruptedIngestCannotBeFinalized() throws {
        let directory = try temporaryDirectory()
        XCTAssertThrowsError(try AudioIngestCheckpoint.requireComplete(directory: directory))
        try FileManager.default.createDirectory(at: directory.appendingPathComponent("audio"), withIntermediateDirectories: true)
        try AudioIngestCheckpoint.complete(directory: directory, mode: "capture")
        XCTAssertNoThrow(try AudioIngestCheckpoint.requireComplete(directory: directory))
    }

    func testWorkerHandlesLargeStderrAndCachesUntilAcknowledged() async throws {
        let directory = try temporaryDirectory()
        let chunksDirectory = directory.appendingPathComponent("audio/chunks")
        try FileManager.default.createDirectory(at: chunksDirectory, withIntermediateDirectories: true)
        let chunk = AudioChunk(id: "job", speaker: "local", start: 12, duration: 1, filename: "job.wav")
        try AudioArchive.wavData([Float](repeating: 0.1, count: 16_000)).write(to: chunksDirectory.appendingPathComponent(chunk.filename))
        try JSONEncoder().encode(chunk).write(to: chunk.url(in: chunksDirectory, suffix: ".chunk.json"))
        let binary = directory.appendingPathComponent("fake-whisper")
        let script = #"""
        #!/bin/sh
        while [ "$#" -gt 0 ]; do
          if [ "$1" = "-of" ]; then shift; output="$1"; fi
          shift
        done
        /usr/bin/yes diagnostic | /usr/bin/head -c 131072 >&2
        printf '%s' '{"transcription":[{"offsets":{"from":0,"to":1000},"text":"persist me"}]}' > "$output.json"
        """#
        try Data(script.utf8).write(to: binary)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: binary.path)
        let model = directory.appendingPathComponent("test-model.bin")
        try Data("fixture".utf8).write(to: model)
        let worker = WhisperWorker()
        let result = try await worker.transcribe(chunk, in: directory,
                                                 configuration: WhisperConfiguration(binaryPath: binary.path, modelPath: model.path))
        XCTAssertEqual(result[0].id, "job:0")
        XCTAssertEqual(result[0].start, 12)
        let unacknowledged = try await worker.pending(in: directory)
        XCTAssertEqual(unacknowledged.count, 1)
        // A crash before application acknowledgement replays cached IDs without inference.
        let replay = try await worker.transcribe(chunk, in: directory,
                                                 configuration: WhisperConfiguration(binaryPath: "/missing", modelPath: "/missing"))
        XCTAssertEqual(replay[0].id, result[0].id)
        try await worker.acknowledge(chunk, in: directory)
        let pending = try await worker.pending(in: directory)
        XCTAssertTrue(pending.isEmpty)
    }
}
