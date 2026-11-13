import Foundation

/// The display name, from CFBundleName in Info.plist. backend/thor/paths.py and ui/src/brand.tsx
/// have it too; keep the three the same.
let appName = Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String ?? "Thor"
