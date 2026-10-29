// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Thor",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "Thor", path: "Sources/Thor"),
    ]
)
