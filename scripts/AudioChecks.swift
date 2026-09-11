// Standalone checks for Macs with Command Line Tools but no XCTest/Testing module.
// Compile with Audio/*.swift; see README for the complete command.
import AVFoundation
import Foundation

@main struct AudioChecks {
    static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("MeetingBoxAudioChecks-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48_000, channels: 2, interleaved: false)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 24_000)!
        buffer.frameLength = 24_000
        for channel in 0..<2 {
            for frame in 0..<24_000 {
                buffer.floatChannelData![channel][frame] = 0.2 * sin(Float(frame) * .pi * 2 * 440 / 48_000)
            }
        }
        let archive = try AudioArchive(directory: root, speaker: "local")
        try archive.append(buffer, at: 0.25)
        try archive.append(buffer, at: 1.25)
        try archive.finish(at: 2)
        let file = try AVAudioFile(forReading: root.appendingPathComponent("audio/local.caf"))
        precondition(file.length == 32_000 && file.processingFormat.sampleRate == 16_000)
        let pcm = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length))!
        try file.read(into: pcm)
        precondition(abs(pcm.floatChannelData![0][2_000]) < 0.00001)
        precondition(abs(pcm.floatChannelData![0][16_000]) < 0.00001)
        precondition(abs(pcm.floatChannelData![0][30_000]) < 0.00001)
        precondition(abs(pcm.floatChannelData![0][4_011]) > 0.05)
        let worker = WhisperWorker()
        let pending = try await worker.pending(in: root)
        precondition(pending.count == 1 && pending[0].start == 0 && pending[0].duration == 2)
        print("PASS: 48 kHz stereo conversion, shared timeline, delayed source, capture gap, trailing silence, final flush")

        let nonIntegerRateDirectory = root.appendingPathComponent("noninteger-rate")
        let nonIntegerRateArchive = try AudioArchive(directory: nonIntegerRateDirectory, speaker: "unknown")
        let nonIntegerRateFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 44_100, channels: 1, interleaved: false)!
        var sourceFrame = 0
        while sourceFrame < 44_100 {
            let count = min(512, 44_100 - sourceFrame)
            let input = AVAudioPCMBuffer(pcmFormat: nonIntegerRateFormat, frameCapacity: AVAudioFrameCount(count))!
            input.frameLength = AVAudioFrameCount(count)
            for frame in 0..<count { input.floatChannelData![0][frame] = 0.1 }
            try nonIntegerRateArchive.append(input, at: nil)
            sourceFrame += count
        }
        try nonIntegerRateArchive.finish()
        let nonIntegerRateFile = try AVAudioFile(forReading: nonIntegerRateDirectory.appendingPathComponent("audio/unknown.caf"))
        precondition(abs(nonIntegerRateFile.length - 16_000) <= 1, "44.1 kHz resampling changed duration: \(nonIntegerRateFile.length)")
        print("PASS: 44.1 kHz short-buffer conversion preserves one-second duration")

        let json = Data(#"{"transcription":[{"offsets":{"from":500,"to":2000},"text":" Test "}]}"#.utf8)
        let chunk = AudioChunk(id: "durable-id", speaker: "remote", start: 24, duration: 1.5, filename: "unused.wav")
        let transcript = try WhisperJSONParser.parse(json, chunk: chunk)
        precondition(transcript.count == 1 && transcript[0].id == "durable-id:0")
        precondition(transcript[0].start == 24.5 && transcript[0].end == 25.5 && transcript[0].text == "Test")
        let invalid = Data(#"{"transcription":[{"offsets":{"from":500,"to":100},"text":"invalid"}]}"#.utf8)
        do {
            _ = try WhisperJSONParser.parse(invalid, chunk: chunk)
            fatalError("Invalid timestamps were accepted")
        } catch { }
        let wav = AudioArchive.wavData([0, 1, -1, .nan])
        precondition(wav.count == 52 && String(decoding: wav.prefix(4), as: UTF8.self) == "RIFF")
        print("PASS: millisecond offsets, bounded timestamps, stable segment IDs, signed PCM16 WAV")

        let source = root.appendingPathComponent("sample.wav")
        let original = AudioArchive.wavData([Float](repeating: 0.1, count: 208_000))
        try original.write(to: source)
        let imported = root.appendingPathComponent("imported")
        try await AudioFileImporter.run(source: source, directory: imported)
        let importedChunks = try await worker.pending(in: imported)
        precondition(importedChunks.count == 2)
        precondition(importedChunks[0].duration == 12 && importedChunks[1].duration == 1)
        precondition(importedChunks[1].start == 12 && importedChunks[1].speaker == "unknown")
        let archivedOriginal = try Data(contentsOf: imported.appendingPathComponent("audio/original.wav"))
        precondition(archivedOriginal == original)
        try AudioIngestCheckpoint.requireComplete(directory: imported)
        do {
            try AudioIngestCheckpoint.requireComplete(directory: root)
            fatalError("Interrupted ingest was accepted")
        } catch { }
        print("PASS: original file copy, 12 + 1 second imported chunks, incomplete-ingest rejection")

        let binary = root.appendingPathComponent("fake-whisper")
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
        let model = root.appendingPathComponent("test-model.bin")
        try Data("fixture".utf8).write(to: model)
        let job = importedChunks[0]
        let result = try await worker.transcribe(job, in: imported,
                                                 configuration: WhisperConfiguration(binaryPath: binary.path, modelPath: model.path))
        precondition(result[0].id == job.id + ":0")
        let replay = try await worker.transcribe(job, in: imported,
                                                 configuration: WhisperConfiguration(binaryPath: "/missing", modelPath: "/missing"))
        precondition(replay[0].id == result[0].id)
        let beforeAck = try await worker.pending(in: imported)
        precondition(beforeAck.count == 2)
        try await worker.acknowledge(job, in: imported)
        let afterAck = try await worker.pending(in: imported)
        precondition(afterAck.count == 1 && afterAck[0].id == importedChunks[1].id)
        print("PASS: subprocess large stderr, durable result replay, acknowledgement removes only accepted job")
        try await checkPipelinePersistence(root: root, binary: binary, model: model)
        print("All standalone audio checks passed.")
    }

    @MainActor
    private static func checkPipelinePersistence(root: URL, binary: URL, model: URL) async throws {
        let source = root.appendingPathComponent("pipeline.wav")
        try AudioArchive.wavData([Float](repeating: 0.1, count: 16_000)).write(to: source)
        let directory = root.appendingPathComponent("pipeline")
        let pipeline = AudioPipeline(configuration: WhisperConfiguration(binaryPath: binary.path, modelPath: model.path))
        var rejectedID: String?
        pipeline.onSegment = { segment in
            rejectedID = segment.id
            throw AudioPipelineError.message("Simulated storage failure")
        }
        do {
            try await pipeline.importFile(source, directory: directory)
            fatalError("Storage failure did not stop finalization")
        } catch { precondition(rejectedID != nil) }
        let persistedPending = try await WhisperWorker().pending(in: directory)
        precondition(persistedPending.count == 1)
        let restored = AudioPipeline(configuration: WhisperConfiguration(binaryPath: "/missing", modelPath: "/missing"))
        var accepted = [String]()
        restored.onSegment = { accepted.append($0.id) }
        try await restored.retryPending(directory: directory)
        precondition(accepted == [rejectedID!] && restored.pendingCount == 0)
        print("PASS: failed application persistence keeps job pending; new pipeline replays and acknowledges it")
    }
}
