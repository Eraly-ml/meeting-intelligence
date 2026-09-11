import Foundation
import Security

struct HubConfiguration {
    var baseURL: URL
    var token: String

    init(address: String, token: String) throws {
        guard let url = URL(string: address.trimmingCharacters(in: .whitespacesAndNewlines)),
              let host = url.host?.lowercased(), ["http", "https"].contains(url.scheme ?? ""),
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty || url.path == "/", Self.isLocalHost(host) else {
            throw MeetingBoxError.message("Use a local hub address, such as http://meetingbox.local:8000 or a private LAN IP.")
        }
        guard token.count >= 16 else { throw MeetingBoxError.message("Enter the hub's pairing token (at least 16 characters) in Settings.") }
        baseURL = url
        self.token = token
    }

    static func isLocalHost(_ host: String) -> Bool {
        if ["localhost", "::1", "[::1]"].contains(host) || host.hasSuffix(".local") { return true }
        let components = host.split(separator: ".", omittingEmptySubsequences: false)
        guard components.count == 4 else { return false }
        let octets = components.compactMap { UInt8($0) }
        guard octets.count == 4 else { return false }
        return octets[0] == 127 || octets[0] == 10 || (octets[0] == 192 && octets[1] == 168)
            || (octets[0] == 172 && (16...31).contains(octets[1]))
    }
}

private final class NoRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

final class HubClient {
    let configuration: HubConfiguration
    private let session: URLSession

    init(configuration: HubConfiguration) {
        self.configuration = configuration
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 20
        config.connectionProxyDictionary = [:]
        session = URLSession(configuration: config, delegate: NoRedirects(), delegateQueue: nil)
    }

    deinit { session.invalidateAndCancel() }

    private func request(_ path: String, method: String = "GET", body: Data? = nil) -> URLRequest {
        var request = URLRequest(url: configuration.baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.httpBody = body
        request.setValue("Bearer \(configuration.token)", forHTTPHeaderField: "Authorization")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        return request
    }

    private func perform(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            let detail = String(data: data.prefix(600), encoding: .utf8) ?? ""
            throw MeetingBoxError.message("Hub returned \(status). \(detail)")
        }
        return data
    }

    func start(_ meeting: LocalMeeting) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["meeting_id": meeting.id, "title": meeting.title])
        _ = try await perform(request("meetings/start", method: "POST", body: body))
    }

    func send(_ segments: [TranscriptSegment], meetingID: String) async throws -> Int {
        struct Batch: Encodable { var segments: [TranscriptSegment] }
        struct Acknowledgment: Decodable { var ack_sequence: Int }
        let data = try await perform(request("meetings/\(meetingID)/segments", method: "POST", body: JSONEncoder().encode(Batch(segments: segments))))
        return try JSONDecoder().decode(Acknowledgment.self, from: data).ack_sequence
    }

    func end(_ meeting: LocalMeeting) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["last_sequence": meeting.segments.count])
        _ = try await perform(request("meetings/\(meeting.id)/end", method: "POST", body: body))
    }

    func snapshot(_ id: String) async throws -> HubMeeting {
        try JSONDecoder().decode(HubMeeting.self, from: await perform(request("meetings/\(id)")))
    }

    func retryReport(_ id: String) async throws {
        _ = try await perform(request("meetings/\(id)/retry", method: "POST"))
    }

    func snapshots(_ id: String, receive: @escaping (HubMeeting) async -> Void) async throws {
        var req = request("ws/\(id)")
        var url = URLComponents(url: req.url!, resolvingAgainstBaseURL: false)!
        url.scheme = configuration.baseURL.scheme == "https" ? "wss" : "ws"
        req.url = url.url!
        let socket = session.webSocketTask(with: req)
        socket.resume()
        defer { socket.cancel(with: .goingAway, reason: nil) }
        try await withTaskCancellationHandler {
            while !Task.isCancelled {
                let message = try await socket.receive()
                let data: Data
                switch message {
                case .data(let value): data = value
                case .string(let value): data = Data(value.utf8)
                @unknown default: continue
                }
                struct Envelope: Decodable { var type: String; var meeting: HubMeeting? }
                let envelope = try JSONDecoder().decode(Envelope.self, from: data)
                if let meeting = envelope.meeting { await receive(meeting) }
            }
        } onCancel: { socket.cancel(with: .goingAway, reason: nil) }
    }
}

enum TokenStore {
    private static var query: [String: Any] { [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "local.meetingbox.mac", kSecAttrAccount as String: "hub-token"] }

    static func read() -> String {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(request as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }

    static func save(_ value: String) throws {
        let data = Data(value.utf8)
        var status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var request = query
            request[kSecValueData as String] = data
            request[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            status = SecItemAdd(request as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw MeetingBoxError.message("Unable to save pairing token in Keychain (\(status)).") }
    }
}
