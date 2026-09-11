import Foundation

struct TranscriptSegment: Codable, Identifiable, Equatable {
    var sequence: Int
    var start: Double
    var end: Double
    var speaker: String
    var text: String
    var id: Int { sequence }
}

struct Decision: Codable {
    var text: String
    var evidence: [Int]
}

struct ActionItem: Codable {
    var task: String
    var owner: String?
    var due: String?
    var evidence: [Int]
}

struct MeetingReport: Codable {
    var summary: String
    var decisions: [Decision]
    var actionItems: [ActionItem]
    var openQuestions: [String]
    var topics: [String]
    var risks: [String]
    enum CodingKeys: String, CodingKey {
        case summary, decisions, topics, risks
        case actionItems = "action_items", openQuestions = "open_questions"
    }
}

struct HubMeeting: Codable {
    var meetingID: String
    var title: String
    var status: String
    var lastSequence: Int
    var segments: [TranscriptSegment]
    var report: MeetingReport?
    var error: String?
    enum CodingKeys: String, CodingKey {
        case title, status, segments, report, error
        case meetingID = "meeting_id", lastSequence = "last_sequence"
    }
}

struct LocalMeeting: Codable, Identifiable {
    var id: String
    var title: String
    var createdAt: Date
    var segments: [TranscriptSegment] = []
    var sourceIDs: [String] = []
    var ackSequence: Int = 0
    var ended = false
    var endSynced = false
    var localState = "ready"
    var hubStatus = "offline"
    var report: MeetingReport?
    var error: String?
    var pendingCount: Int { max(0, segments.count - ackSequence) }

    mutating func reconcileAcknowledgment(_ ack: Int, hubStatus: String) throws {
        guard ack >= 0, ack <= segments.count else {
            throw MeetingBoxError.message("Hub sequence does not match this Mac's saved transcript.")
        }
        ackSequence = ack
        // Rebuilding/replacing a hub must replay both the transcript and its end marker.
        if hubStatus == "recording" { endSynced = false }
    }

    mutating func append(sourceID: String, start: Double, end: Double, speaker: String, text: String) throws {
        guard !sourceIDs.contains(sourceID) else { return }
        guard !ended, start.isFinite, end.isFinite, start >= 0, end >= start,
              ["local", "remote", "unknown"].contains(speaker), !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw MeetingBoxError.message("Invalid transcript segment, or meeting has already ended.")
        }
        segments.append(TranscriptSegment(sequence: segments.count + 1, start: start, end: end, speaker: speaker, text: text))
        sourceIDs.append(sourceID)
    }
}

enum MeetingBoxError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let value) = self { return value }; return nil }
}

func timestamp(_ seconds: Double) -> String {
    let value = max(0, Int(seconds))
    return String(format: "%02d:%02d", value / 60, value % 60)
}
