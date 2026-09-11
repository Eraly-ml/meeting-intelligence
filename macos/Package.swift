// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MeetingBox",
    platforms: [.macOS(.v15)],
    products: [.executable(name: "MeetingBox", targets: ["MeetingBox"])],
    targets: [
        .executableTarget(name: "MeetingBox"),
        .testTarget(name: "MeetingBoxTests", dependencies: ["MeetingBox"])
    ],
    swiftLanguageModes: [.v5]
)
