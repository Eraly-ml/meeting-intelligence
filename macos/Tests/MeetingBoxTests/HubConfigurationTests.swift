import Foundation
import Testing
@testable import MeetingBox

struct HubConfigurationTests {
    private let token = "a-local-demo-token-123"

    @Test func permitsLocalEndpoints() throws {
        for address in ["http://localhost:8000", "https://meetingbox.local", "http://192.168.1.4:8000", "http://172.16.2.3", "http://10.0.0.1", "http://[::1]:8000"] {
            #expect(try HubConfiguration(address: address, token: token).token == token)
        }
    }

    @Test func refusesPublicEndpointsAndEmbeddedCredentials() {
        for address in ["https://example.com", "http://8.8.8.8", "http://172.32.0.1", "http://192.168.1.999", "http://user:password@localhost", "http://localhost/proxy", "http://localhost?host=example.com", "file:///tmp/model", "http://localhost.example.com"] {
            #expect(throws: (any Error).self) { try HubConfiguration(address: address, token: token) }
        }
        #expect(throws: (any Error).self) { try HubConfiguration(address: "http://localhost", token: "short") }
    }

    @Test func decodesHubReportContract() throws {
        let json = """
        {"meeting_id":"a","title":"Review","status":"complete","last_sequence":2,
        "segments":[{"sequence":2,"start":12.0,"end":13.0,"speaker":"remote","text":"Monday instead."}],
        "report":{"summary":"Deadline revised.","decisions":[{"text":"Move to Monday","evidence":[2]}],
        "action_items":[{"task":"Ship report","owner":null,"due":"Monday","evidence":[2]}],
        "open_questions":[],"topics":[],"risks":[]},"error":null}
        """
        let meeting = try JSONDecoder().decode(HubMeeting.self, from: Data(json.utf8))
        #expect(meeting.lastSequence == 2)
        #expect(meeting.report?.actionItems.first?.owner == nil)
        #expect(meeting.report?.actionItems.first?.due == "Monday")
        #expect(meeting.report?.decisions.first?.evidence == [2])
    }
}
