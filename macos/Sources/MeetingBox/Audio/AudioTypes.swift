import Foundation

struct AudioTranscript: Codable, Sendable {
    let id: String
    let speaker: String
    let start: Double
    let end: Double
    let text: String
}

struct WhisperConfiguration: Sendable {
    var binaryPath: String
    var modelPath: String
    var language: String = "auto"

    func validate() throws {
        guard FileManager.default.isExecutableFile(atPath: binaryPath) else {
            throw AudioPipelineError.message("Choose an installed whisper-cli executable in Settings. No executable found at \(binaryPath).")
        }
        var isDirectory: ObjCBool = false
        guard !modelPath.isEmpty,
              FileManager.default.fileExists(atPath: modelPath, isDirectory: &isDirectory),
              !isDirectory.boolValue, FileManager.default.isReadableFile(atPath: modelPath) else {
            throw AudioPipelineError.message("Choose a local whisper.cpp GGML model in Settings. Models are never downloaded automatically.")
        }
    }
}

enum AudioPipelineError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case .message(let value): return value }
    }
}

struct AudioChunk: Codable, Sendable {
    let id: String
    let speaker: String
    let start: Double
    let duration: Double
    let filename: String

    func url(in directory: URL, suffix: String) -> URL {
        directory.appendingPathComponent(id + suffix)
    }
}

enum AudioIngestCheckpoint {
    private struct Completion: Codable {
        let version: Int
        let mode: String
        let completedAt: Date
    }

    static func complete(directory: URL, mode: String) throws {
        let marker = Completion(version: 1, mode: mode, completedAt: Date())
        try JSONEncoder().encode(marker).write(to: directory.appendingPathComponent("audio/ingest-complete.json"), options: .atomic)
    }

    static func requireComplete(directory: URL) throws {
        let marker = directory.appendingPathComponent("audio/ingest-complete.json")
        guard let data = try? Data(contentsOf: marker),
              let completion = try? JSONDecoder().decode(Completion.self, from: data),
              completion.version == 1 else {
            throw AudioPipelineError.message("Audio capture or import did not finish cleanly. This meeting cannot be finalized because some audio may be missing from its chunk queue. Import the saved original recording, or the local/remote CAF files in this meeting's audio folder, into a new meeting to recover them.")
        }
    }
}

/// whisper.cpp JSON offsets are milliseconds, not its internal 10 ms token ticks.
enum WhisperJSONParser {
    private struct Result: Decodable {
        struct Segment: Decodable {
            struct Offsets: Decodable { let from: Double; let to: Double }
            let offsets: Offsets
            let text: String
        }
        let transcription: [Segment]
    }

    static func parse(_ data: Data, chunk: AudioChunk) throws -> [AudioTranscript] {
        let result = try JSONDecoder().decode(Result.self, from: data)
        return try result.transcription.enumerated().compactMap { index, item in
            guard item.offsets.from.isFinite, item.offsets.to.isFinite,
                  item.offsets.from >= 0, item.offsets.to >= item.offsets.from else {
                throw AudioPipelineError.message("Whisper returned invalid transcript timestamps.")
            }
            let text = item.text.trimmingCharacters(in: .whitespacesAndNewlines)
            let start = min(chunk.duration, item.offsets.from / 1_000)
            let end = min(chunk.duration, item.offsets.to / 1_000)
            guard !text.isEmpty, end > start else { return nil }
            return AudioTranscript(id: "\(chunk.id):\(index)", speaker: chunk.speaker,
                                   start: chunk.start + start, end: chunk.start + end, text: text)
        }
    }
}
