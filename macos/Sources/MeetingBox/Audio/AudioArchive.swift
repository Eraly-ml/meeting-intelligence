import AVFoundation
import CoreMedia
import Foundation

/// Used only on the capture/file-decoding serial queue. Recording never waits for Whisper.
final class AudioArchive {
    static let sampleRate = 16_000.0
    static let chunkFrames = 12 * 16_000
    static let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate,
                                      channels: 1, interleaved: false)!

    private let directory: URL
    private let speaker: String
    private var file: AVAudioFile?
    private var converter: AVAudioConverter?
    private var inputFormat: AVAudioFormat?
    private var recordedFrames: Int64 = 0
    private var chunkStart: Int64 = 0
    private var samples = [Float]()
    private var finished = false

    init(directory: URL, speaker: String) throws {
        self.directory = directory.appendingPathComponent("audio/chunks", isDirectory: true)
        self.speaker = speaker
        try FileManager.default.createDirectory(at: self.directory, withIntermediateDirectories: true)
        let archive = directory.appendingPathComponent("audio/\(speaker).caf")
        guard !FileManager.default.fileExists(atPath: archive.path) else {
            throw AudioPipelineError.message("This meeting already contains a \(speaker) recording. Create a new meeting to record again.")
        }
        file = try AVAudioFile(forWriting: archive, settings: Self.format.settings,
                               commonFormat: .pcmFormatFloat32, interleaved: false)
        samples.reserveCapacity(Self.chunkFrames)
    }

    func append(_ buffer: AVAudioPCMBuffer, at seconds: Double?) throws {
        guard !finished else { return }
        let converted = try convert(buffer)
        guard !converted.isEmpty else { return }
        var trim = 0
        if let seconds {
            guard seconds.isFinite, seconds > -1, seconds < 7 * 86_400 else {
                throw AudioPipelineError.message("Capture returned a timestamp outside the meeting clock.")
            }
            let target = Int64((max(0, seconds) * Self.sampleRate).rounded())
            // Preserve source start delays and gaps. A two-millisecond tolerance absorbs
            // resampler rounding without accumulating drift across a long meeting.
            let delta = target - recordedFrames
            if delta > 32 {
                var remaining = delta
                let zeros = [Float](repeating: 0, count: 4_096)
                while remaining > 0 {
                    let count = min(Int64(zeros.count), remaining)
                    try appendNormalized(Array(zeros.prefix(Int(count))))
                    remaining -= count
                }
            } else if delta < -32 {
                trim = min(converted.count, Int(-delta))
            }
        }
        if trim < converted.count { try appendNormalized(Array(converted.dropFirst(trim))) }
    }

    private func convert(_ buffer: AVAudioPCMBuffer) throws -> [Float] {
        if buffer.format == Self.format {
            guard let channel = buffer.floatChannelData?[0] else {
                throw AudioPipelineError.message("Audio buffer has no PCM samples.")
            }
            return Array(UnsafeBufferPointer(start: channel, count: Int(buffer.frameLength)))
        }
        if inputFormat != buffer.format {
            inputFormat = buffer.format
            converter = AVAudioConverter(from: buffer.format, to: Self.format)
            converter?.primeMethod = .none
            converter?.downmix = true
        }
        guard let converter else { throw AudioPipelineError.message("Unsupported audio input format.") }
        let capacity = AVAudioFrameCount(ceil(Double(buffer.frameLength) * Self.sampleRate / buffer.format.sampleRate)) + 256
        guard let output = AVAudioPCMBuffer(pcmFormat: Self.format, frameCapacity: capacity) else {
            throw AudioPipelineError.message("Cannot allocate the audio conversion buffer.")
        }
        var inputOffset: AVAudioFrameCount = 0
        var result = [Float]()
        while true {
            var error: NSError?
            let status = converter.convert(to: output, error: &error) { requested, status in
                guard inputOffset < buffer.frameLength else { status.pointee = .noDataNow; return nil }
                let count = min(requested, buffer.frameLength - inputOffset)
                guard let slice = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: count) else {
                    status.pointee = .noDataNow
                    return nil
                }
                slice.frameLength = count
                let bytesPerFrame = Int(buffer.format.streamDescription.pointee.mBytesPerFrame)
                let source = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
                let destination = UnsafeMutableAudioBufferListPointer(slice.mutableAudioBufferList)
                for index in source.indices {
                    if let from = source[index].mData, let to = destination[index].mData {
                        memcpy(to, from.advanced(by: Int(inputOffset) * bytesPerFrame), Int(count) * bytesPerFrame)
                    }
                }
                inputOffset += count
                status.pointee = .haveData
                return slice
            }
            if let error { throw error }
            if status == .error { throw AudioPipelineError.message("Audio conversion failed.") }
            if let channel = output.floatChannelData?[0], output.frameLength > 0 {
                result.append(contentsOf: UnsafeBufferPointer(start: channel, count: Int(output.frameLength)))
            }
            if status != .haveData || output.frameLength == 0 { break }
            output.frameLength = 0
        }
        return result
    }

    private func appendNormalized(_ values: [Float]) throws {
        guard !values.isEmpty, let file else { return }
        guard let buffer = AVAudioPCMBuffer(pcmFormat: Self.format, frameCapacity: AVAudioFrameCount(values.count)),
              let channel = buffer.floatChannelData?[0] else {
            throw AudioPipelineError.message("Cannot allocate a recording buffer.")
        }
        buffer.frameLength = AVAudioFrameCount(values.count)
        values.withUnsafeBufferPointer { pointer in channel.update(from: pointer.baseAddress!, count: pointer.count) }
        try file.write(from: buffer)
        recordedFrames += Int64(values.count)
        var cursor = 0
        while cursor < values.count {
            let count = min(Self.chunkFrames - samples.count, values.count - cursor)
            samples.append(contentsOf: values[cursor..<(cursor + count)])
            cursor += count
            if samples.count == Self.chunkFrames { try flushChunk() }
        }
    }

    private func flushChunk() throws {
        guard !samples.isEmpty else { return }
        let count = samples.count
        // Exact digital silence carries no speech; avoid Whisper hallucinations for it.
        if samples.contains(where: { abs($0) > 0.0000001 }) {
            let id = UUID().uuidString.lowercased()
            let chunk = AudioChunk(id: id, speaker: speaker,
                                   start: Double(chunkStart) / Self.sampleRate,
                                   duration: Double(count) / Self.sampleRate, filename: id + ".wav")
            try Self.wavData(samples).write(to: directory.appendingPathComponent(chunk.filename), options: .atomic)
            // Publish metadata last. The inference queue only discovers complete WAVs.
            try JSONEncoder().encode(chunk).write(to: chunk.url(in: directory, suffix: ".chunk.json"), options: .atomic)
        }
        chunkStart += Int64(count)
        samples.removeAll(keepingCapacity: true)
    }

    func finish(at seconds: Double? = nil) throws {
        guard !finished else { return }
        // A source can become silent before the meeting ends. Keep both archives
        // on the complete meeting timeline, including that trailing silence.
        if let seconds, seconds.isFinite, seconds >= 0, seconds < 7 * 86_400 {
            let target = Int64((seconds * Self.sampleRate).rounded())
            let zeros = [Float](repeating: 0, count: 4_096)
            while recordedFrames < target {
                let count = min(Int64(zeros.count), target - recordedFrames)
                try appendNormalized(Array(zeros.prefix(Int(count))))
            }
        }
        try flushChunk()
        file = nil // Closes and finalizes the full CAF, including a partial final chunk.
        finished = true
    }

    static func wavData(_ samples: [Float]) -> Data {
        var result = Data()
        func tag(_ value: String) { result.append(contentsOf: value.utf8) }
        func integer<T: FixedWidthInteger>(_ value: T) {
            var value = value.littleEndian
            withUnsafeBytes(of: &value) { result.append(contentsOf: $0) }
        }
        tag("RIFF"); integer(UInt32(36 + samples.count * 2)); tag("WAVEfmt ")
        integer(UInt32(16)); integer(UInt16(1)); integer(UInt16(1))
        integer(UInt32(16_000)); integer(UInt32(32_000)); integer(UInt16(2)); integer(UInt16(16))
        tag("data"); integer(UInt32(samples.count * 2))
        for value in samples {
            let sample = value.isFinite ? min(1, max(-1, value)) : 0
            integer(Int16((sample * 32_767).rounded()))
        }
        return result
    }

    static func pcmBuffer(from sample: CMSampleBuffer) throws -> AVAudioPCMBuffer {
        guard let description = sample.formatDescription else {
            throw AudioPipelineError.message("Capture returned no audio format description.")
        }
        let format = AVAudioFormat(cmAudioFormatDescription: description)
        guard sample.numSamples > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(sample.numSamples)) else {
            throw AudioPipelineError.message("Capture returned an unsupported audio buffer.")
        }
        buffer.frameLength = AVAudioFrameCount(sample.numSamples)
        let result = CMSampleBufferCopyPCMDataIntoAudioBufferList(sample, at: 0,
                                                                frameCount: Int32(sample.numSamples), into: buffer.mutableAudioBufferList)
        guard result == noErr else { throw AudioPipelineError.message("Cannot copy captured audio (\(result)).") }
        return buffer
    }
}
