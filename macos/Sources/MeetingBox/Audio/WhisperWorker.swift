import Darwin
import Foundation

actor WhisperWorker {
    private let inferenceQueue = DispatchQueue(label: "meetingbox.whisper.serial", qos: .utility)

    func pending(in directory: URL) throws -> [AudioChunk] {
        let chunksDirectory = directory.appendingPathComponent("audio/chunks", isDirectory: true)
        guard FileManager.default.fileExists(atPath: chunksDirectory.path) else { return [] }
        return try FileManager.default.contentsOfDirectory(at: chunksDirectory, includingPropertiesForKeys: nil)
            .filter { $0.lastPathComponent.hasSuffix(".chunk.json") }
            .compactMap { file in
                let chunk = try JSONDecoder().decode(AudioChunk.self, from: Data(contentsOf: file))
                guard !FileManager.default.fileExists(atPath: chunk.url(in: chunksDirectory, suffix: ".done").path) else { return nil }
                return chunk
            }
            .sorted { lhs, rhs in lhs.start == rhs.start ? lhs.id < rhs.id : lhs.start < rhs.start }
    }

    func transcribe(_ chunk: AudioChunk, in directory: URL, configuration: WhisperConfiguration) async throws -> [AudioTranscript] {
        let chunksDirectory = directory.appendingPathComponent("audio/chunks", isDirectory: true)
        let resultURL = chunk.url(in: chunksDirectory, suffix: ".result.json")
        if FileManager.default.fileExists(atPath: resultURL.path) {
            return try JSONDecoder().decode([AudioTranscript].self, from: Data(contentsOf: resultURL))
        }
        try configuration.validate()
        return try await withCheckedThrowingContinuation { continuation in
            inferenceQueue.async {
                do {
                    let result = try Self.run(chunk: chunk, directory: chunksDirectory, configuration: configuration)
                    try JSONEncoder().encode(result).write(to: resultURL, options: .atomic)
                    continuation.resume(returning: result)
                } catch { continuation.resume(throwing: error) }
            }
        }
    }

    func acknowledge(_ chunk: AudioChunk, in directory: URL) throws {
        // Only called after the application durably accepts every segment callback.
        try Data().write(to: chunk.url(in: directory.appendingPathComponent("audio/chunks"), suffix: ".done"), options: .atomic)
    }

    private static func run(chunk: AudioChunk, directory: URL, configuration: WhisperConfiguration) throws -> [AudioTranscript] {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: configuration.binaryPath)
        let outputBase = chunk.url(in: directory, suffix: ".whisper")
        process.arguments = ["-m", configuration.modelPath, "-f", directory.appendingPathComponent(chunk.filename).path,
                             "-oj", "-of", outputBase.path, "-l", configuration.language,
                             "-t", String(max(1, min(4, ProcessInfo.processInfo.activeProcessorCount - 1))), "-np"]
        process.standardInput = FileHandle.nullDevice
        let logURL = chunk.url(in: directory, suffix: ".log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let log = try FileHandle(forWritingTo: logURL)
        try log.truncate(atOffset: 0)
        defer { try? log.close() }
        // File-backed stdout/stderr cannot fill a pipe and deadlock the worker.
        process.standardOutput = log
        process.standardError = log
        try process.run()
        let timeout = DispatchWorkItem {
            if process.isRunning {
                process.terminate()
                DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 3) {
                    if process.isRunning { kill(process.processIdentifier, SIGKILL) }
                }
            }
        }
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 300, execute: timeout)
        process.waitUntilExit()
        timeout.cancel()
        try log.synchronize()
        guard process.terminationStatus == 0 else {
            let handle = try FileHandle(forReadingFrom: logURL)
            defer { try? handle.close() }
            let size = try handle.seekToEnd()
            try handle.seek(toOffset: size > 4_096 ? size - 4_096 : 0)
            let detail = String(decoding: try handle.readToEnd() ?? Data(), as: UTF8.self)
            throw AudioPipelineError.message("Whisper failed (exit \(process.terminationStatus); limit 5 minutes per chunk). Your audio is saved; fix Settings and retry. \(detail)")
        }
        let jsonURL = URL(fileURLWithPath: outputBase.path + ".json")
        guard FileManager.default.fileExists(atPath: jsonURL.path) else {
            throw AudioPipelineError.message("whisper-cli produced no JSON. Install a whisper.cpp CLI supporting -oj, and retry the saved audio.")
        }
        return try WhisperJSONParser.parse(Data(contentsOf: jsonURL), chunk: chunk)
    }
}
