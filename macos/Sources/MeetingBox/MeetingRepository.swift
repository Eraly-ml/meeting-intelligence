import Foundation

/// Recording files and this durable transcript are authoritative; ACKs are only sync progress.
struct MeetingRepository {
    let root: URL

    init(root: URL? = nil) throws {
        self.root = try root ?? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
            .appendingPathComponent("MeetingBox/Meetings", isDirectory: true)
        try FileManager.default.createDirectory(at: self.root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    }

    func directory(for id: String) -> URL { root.appendingPathComponent(id, isDirectory: true) }

    func save(_ meeting: LocalMeeting) throws {
        let folder = directory(for: meeting.id)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let data = try JSONEncoder().encode(meeting)
        let destination = folder.appendingPathComponent("meeting.json")
        try data.write(to: destination, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: destination.path)
    }

    func load() throws -> [LocalMeeting] {
        try loadWithIssues().meetings
    }

    func loadWithIssues() throws -> (meetings: [LocalMeeting], issues: [String]) {
        let folders = try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        var meetings = [LocalMeeting]()
        var issues = [String]()
        for folder in folders {
            let metadata = folder.appendingPathComponent("meeting.json")
            guard FileManager.default.fileExists(atPath: metadata.path) else { continue }
            do {
                meetings.append(try JSONDecoder().decode(LocalMeeting.self, from: Data(contentsOf: metadata)))
            } catch {
                // Decoding errors may include stored values. Only expose the file's relative path.
                issues.append("Could not load \(folder.lastPathComponent)/meeting.json. Saved files are unchanged.")
            }
        }
        return (meetings.sorted { $0.createdAt > $1.createdAt }, issues)
    }
}
