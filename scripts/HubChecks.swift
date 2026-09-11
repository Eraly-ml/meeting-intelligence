import Foundation

@main
enum HubChecks {
    static func main() throws {
        let token = "a-local-test-token-123"
        for address in ["http://localhost:8000", "https://meetingbox.local", "http://192.168.1.4:8000", "http://172.16.2.3", "http://10.0.0.1", "http://[::1]:8000"] {
            _ = try HubConfiguration(address: address, token: token)
        }
        for address in ["https://example.com", "http://8.8.8.8", "http://172.32.0.1", "http://192.168.1.999", "http://user:password@localhost", "http://localhost/proxy", "http://localhost?host=example.com", "file:///tmp/model", "http://localhost.example.com"] {
            do {
                _ = try HubConfiguration(address: address, token: token)
                fatalError("Accepted forbidden endpoint: \(address)")
            } catch {}
        }
        var meeting = LocalMeeting(id: UUID().uuidString, title: "Rebuilt hub", createdAt: Date())
        try meeting.append(sourceID: "chunk:0", start: 0, end: 1, speaker: "local", text: "Ship Monday")
        meeting.ended = true
        meeting.endSynced = true
        meeting.ackSequence = 1
        try meeting.reconcileAcknowledgment(0, hubStatus: "recording")
        precondition(meeting.pendingCount == 1 && !meeting.endSynced && meeting.ended)
        do { try meeting.reconcileAcknowledgment(2, hubStatus: "complete"); fatalError("Accepted invalid ACK") } catch {}
        let json = """
        {"meeting_id":"a","title":"Review","status":"complete","last_sequence":2,
        "segments":[{"sequence":2,"start":12.0,"end":13.0,"speaker":"remote","text":"Monday instead."}],
        "report":{"summary":"Deadline revised.","decisions":[{"text":"Move to Monday","evidence":[2]}],
        "action_items":[{"task":"Ship report","owner":null,"due":"Monday","evidence":[2]}],
        "open_questions":[],"topics":[],"risks":[]},"error":null}
        """
        let snapshot = try JSONDecoder().decode(HubMeeting.self, from: Data(json.utf8))
        precondition(snapshot.lastSequence == 2)
        precondition(snapshot.report?.actionItems.first?.owner == nil)
        precondition(snapshot.report?.actionItems.first?.due == "Monday")
        precondition(snapshot.report?.decisions.first?.evidence == [2])
        print("PASS: local endpoints, hub contract, invalid ACK rejection, rebuilt-hub finalization")
    }
}
