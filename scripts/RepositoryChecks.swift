import Foundation

/// Framework-free checks for Macs with Command Line Tools but no Swift Testing module.
@main
enum RepositoryChecks {
    enum Failure: Error { case check(String) }

    static func require(_ condition: Bool, _ message: String) throws {
        guard condition else { throw Failure.check(message) }
    }

    static func rejects(_ operation: () throws -> Void) throws {
        do { try operation() }
        catch is MeetingBoxError { return }
        throw Failure.check("Expected a rejected transcript append")
    }

    static func main() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("MeetingBoxRepositoryChecks-" + UUID().uuidString, isDirectory: true)
        let repository = try MeetingRepository(root: root)
        defer { try? FileManager.default.removeItem(at: root) }

        var meeting = LocalMeeting(id: UUID().uuidString.lowercased(), title: "Restart check",
                                   createdAt: Date(timeIntervalSince1970: 1_700_000_000))
        try meeting.append(sourceID: "chunk-a:0", start: 0, end: 2, speaker: "remote", text: "Ship Friday.")
        meeting.ackSequence = 1
        try repository.save(meeting)
        let reopened = try MeetingRepository(root: root)
        guard var restored = try reopened.load().first else { throw Failure.check("Saved meeting was not loaded") }
        try require(restored.id == meeting.id && restored.title == meeting.title && restored.createdAt == meeting.createdAt,
                    "Meeting metadata changed on reload")
        try require(restored.segments == meeting.segments && restored.ackSequence == 1,
                    "Saved transcript or acknowledgement was lost")

        // Replay a result saved before an app exit but not yet acknowledged to the audio worker.
        try restored.append(sourceID: "chunk-a:0", start: 0, end: 2, speaker: "remote", text: "Ship Friday.")
        try restored.append(sourceID: "chunk-b:0", start: 2, end: 4, speaker: "local", text: "Move it to Monday.")
        restored.ended = true
        restored.endSynced = true
        restored.localState = "saved"
        try reopened.save(restored)
        guard let replayed = try repository.load().first else { throw Failure.check("Replayed meeting was not loaded") }
        try require(replayed.segments.map(\.sequence) == [1, 2], "Replay duplicated a transcript or left a sequence gap")
        try require(replayed.sourceIDs == ["chunk-a:0", "chunk-b:0"], "Source IDs changed on replay")
        try require(replayed.segments.map(\.text) == ["Ship Friday.", "Move it to Monday."], "Replay changed transcript content")
        try require(replayed.ended && replayed.endSynced && replayed.localState == "saved", "Finalization state did not persist")
        print("PASS: transcript and synchronization state persist; replay keeps stable IDs and sequences")

        let brokenFolder = repository.directory(for: "broken-record")
        try FileManager.default.createDirectory(at: brokenFolder, withIntermediateDirectories: true)
        let brokenFile = brokenFolder.appendingPathComponent("meeting.json")
        let original = Data("{\"private-transcript\":\"never disclose\"".utf8)
        try original.write(to: brokenFile)
        let loaded = try repository.loadWithIssues()
        try require(loaded.meetings.map(\.id) == [meeting.id], "Corrupt neighbor hid a healthy meeting")
        try require(loaded.issues.count == 1 && loaded.issues[0].contains("broken-record/meeting.json"), "Missing filename issue")
        try require(!loaded.issues[0].contains("private-transcript") && !loaded.issues[0].contains("never disclose"), "Issue revealed file content")
        try require(try Data(contentsOf: brokenFile) == original, "Loading modified unreadable metadata")
        try require(try repository.load().count == 1, "Compatibility load did not isolate corrupt metadata")
        print("PASS: corrupt metadata is isolated and reported without its contents")

        var invalid = LocalMeeting(id: UUID().uuidString.lowercased(), title: "Invalid append", createdAt: Date())
        let values: [(Double, Double, String, String)] = [
            (-1, 1, "local", "Negative"), (2, 1, "local", "Reversed"),
            (.nan, 1, "local", "NaN"), (0, .infinity, "local", "Infinite"),
            (0, 1, "unrecognized", "Source"), (0, 1, "local", " \n\t")
        ]
        for (start, end, speaker, text) in values {
            try rejects { try invalid.append(sourceID: "retryable", start: start, end: end, speaker: speaker, text: text) }
            try require(invalid.segments.isEmpty && invalid.sourceIDs.isEmpty, "Rejected append consumed an ID or sequence")
        }
        try invalid.append(sourceID: "retryable", start: 0, end: 1, speaker: "local", text: "Valid replacement")
        invalid.ended = true
        try rejects { try invalid.append(sourceID: "late", start: 1, end: 2, speaker: "local", text: "Late segment") }
        try require(invalid.segments.map(\.sequence) == [1], "Late append changed finalized transcript")
        print("PASS: invalid and late transcript events leave stored sequences unchanged")
    }
}
