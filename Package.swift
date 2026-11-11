// swift-tools-version: 6.3
import PackageDescription

let package = Package(
    name: "CashBooks",
    platforms: [.iOS("26.5"), .macOS("26.5")],
    products: [
        .library(name: "CashBooksCore", targets: ["CashBooksCore"]),
        .library(name: "CashBooksDocument", targets: ["CashBooksDocument"]),
        .library(name: "CashBooksPersistence", targets: ["CashBooksPersistence"]),
        .library(name: "CashBooksIntelligence", targets: ["CashBooksIntelligence"]),
        .library(name: "CashBooksLocalEngineHost", targets: ["CashBooksLocalEngineHost"]),
    ],
    targets: [
        .target(name: "CashBooksCore", dependencies: []),
        .target(name: "CashBooksDocument", dependencies: ["CashBooksCore"]),
        .target(name: "CashBooksPersistence", dependencies: ["CashBooksCore"]),
        .target(name: "CashBooksIntelligence", dependencies: ["CashBooksCore"]),
        .target(name: "CashBooksLocalEngineHost", dependencies: ["CashBooksCore", "CashBooksDocument", "CashBooksPersistence", "CashBooksIntelligence"]),
        .testTarget(name: "CashBooksCoreTests", dependencies: ["CashBooksCore"]),
        .testTarget(name: "CashBooksDocumentTests", dependencies: ["CashBooksDocument"]),
        .testTarget(name: "CashBooksPersistenceTests", dependencies: ["CashBooksPersistence"]),
        .testTarget(name: "CashBooksIntelligenceTests", dependencies: ["CashBooksIntelligence"]),
        .testTarget(name: "CashBooksLocalEngineHostTests", dependencies: ["CashBooksLocalEngineHost"]),
    ],
    swiftLanguageModes: [.v6]
)
