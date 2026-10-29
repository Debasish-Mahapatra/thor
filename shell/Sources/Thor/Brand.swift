import Foundation

/// The display name, from CFBundleName in Info.plist. "Thor" is a working name; change it there,
/// in backend/thor/paths.py and in ui/src/brand.tsx.
let appName = Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String ?? "Thor"
