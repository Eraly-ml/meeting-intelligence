import Foundation
import Testing
@testable import MeetingBox

struct RepositoryTests {
    private func withRepository(_ body: (MeetingRepository) throws -> Void) throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("MeetingBoxRepositoryTests-" + UUID().uuidString, isDirectory: true)
        let repository = try MeetingRepository(root: root)
        defer { try? FileManager.default.removeItem(at: root) }
        try body(repository)
    }

    @Test func transcriptAndSyncStateSurviveReload() throws {
        try withRepository { repository in
            var meeting = LocalMeeting(id: UUID().uuidString.lowercased(), title: "Launch review",
                                       createdAt: Date(timeIntervalSince1970: 1_700_000_000))
            try meeting.append(sourceID: "chunk-a:0", start: 1.5, end: 3.0, speaker: "local", text: "Dana owns the report.")
            meeting.ackSequence = 1
            meeting.ended = true
            meeting.endSynced = true
            meeting.localState = "saved"
            try repository.save(meeting)

            let reopened = try MeetingRepository(root: repository.root)
            let loaded = try #require(reopened.load().first)
            #expect(loaded.id == meeting.id)
            #expect(loaded.title == meeting.title)
            #expect(loaded.createdAt == meeting.createdAt)
            #expect(loaded.segments == meeting.segments)
            #expect(loaded.sourceIDs == ["chunk-a:0"])
            #expect(loaded.ackSequence == 1)
            #expect(loaded.ended)
            #expect(loaded.endSynced)
            #expect(loaded.localState == "saved")
        }
    }

    @Test func replayAfterRestartKeepsStableIDsAndContiguousSequences() throws {
        try withRepository { repository in
            var meeting = LocalMeeting(id: UUID().uuidString.lowercased(), title: "Retry scenario", createdAt: Date())
            try meeting.append(sourceID: "chunk-a:0", start: 0, end: 2, speaker: "remote", text: "Ship Friday.")
            try repository.save(meeting)

            // The app persisted the segment, then exited before the worker marked its chunk done.
            var recovered = try #require(repository.load().first)
            try recovered.append(sourceID: "chunk-a:0", start: 0, end: 2, speaker: "remote", text: "Ship Friday.")
            try recovered.append(sourceID: "chunk-b:0", start: 2, end: 4, speaker: "local", text: "Move it to Monday.")
            try repository.save(recovered)

            let loaded = try #require(repository.load().first)
            #expect(loaded.segments.map(\.sequence) == [1, 2])
            #expect(loaded.sourceIDs == ["chunk-a:0", "chunk-b:0"])
            #expect(loaded.segments.map(\.text) == ["Ship Friday.", "Move it to Monday."])
        }
    }

    @Test func corruptNeighborDoesNotHideHealthyMeetings() throws {
        try withRepository { repository in
            let older = LocalMeeting(id: UUID().uuidString.lowercased(), title: "Older", createdAt: Date(timeIntervalSince1970: 10))
            let newer = LocalMeeting(id: UUID().uuidString.lowercased(), title: "Newer", createdAt: Date(timeIntervalSince1970: 20))
            try repository.save(older)
            try repository.save(newer)
            let corruptFolder = repository.directory(for: "broken-record")
            try FileManager.default.createDirectory(at: corruptFolder, withIntermediateDirectories: true)
            let corruptFile = corruptFolder.appendingPathComponent("meeting.json")
            let original = Data("{\"private-transcript\":\"do not show in an error\"".utf8)
            try original.write(to: corruptFile)

            let result = try repository.loadWithIssues()
            #expect(result.meetings.map(\.id) == [newer.id, older.id])
            #expect(result.issues.count == 1)
            let issue = try #require(result.issues.first)
            #expect(issue.contains("broken-record/meeting.json"))
            #expect(!issue.contains("private-transcript"))
            #expect(!issue.contains("do not show"))
            #expect(try Data(contentsOf: corruptFile) == original)
            #expect(try repository.load().map(\.id) == [newer.id, older.id])
        }
    }

    @Test func invalidSegmentsDoNotConsumeSequenceOrSourceID() throws {
        var meeting = LocalMeeting(id: UUID().uuidString.lowercased(), title: "Validation", createdAt: Date())
        let invalid: [(Double, Double, String, String)] = [
            (-1, 1, "local", "Negative start"),
            (2, 1, "local", "Reversed interval"),
            (.nan, 1, "local", "Invalid start"),
            (0, .infinity, "local", "Invalid end"),
            (0, 1, "unrecognized", "Unknown source"),
            (0, 1, "local", " \n\t")
        ]
        for (start, end, speaker, text) in invalid {
            #expect(throws: MeetingBoxError.self) {
                try meeting.append(sourceID: "retryable-source", start: start, end: end, speaker: speaker, text: text)
            }
            #expect(meeting.segments.isEmpty)
            #expect(meeting.sourceIDs.isEmpty)
        }
        try meeting.append(sourceID: "retryable-source", start: 0, end: 1, speaker: "local", text: "Valid replacement")
        #expect(meeting.segments.map(\.sequence) == [1])
        #expect(meeting.sourceIDs == ["retryable-source"])
        meeting.ended = true
        #expect(throws: MeetingBoxError.self) {
            try meeting.append(sourceID: "late-source", start: 1, end: 2, speaker: "local", text: "Too late")
        }
        #expect(meeting.segments.count == 1)
    }
}
