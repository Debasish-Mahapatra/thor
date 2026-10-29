import AppKit
import WebKit

/// Answers `window.webkit.messageHandlers.thor.postMessage(...)` from the interface.
final class Bridge: NSObject, WKScriptMessageHandlerWithReply {
    weak var window: NSWindow?

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        // Only the interface served by our own backend may use the bridge.
        guard message.frameInfo.securityOrigin.host == "127.0.0.1" else {
            replyHandler(nil, "Refused")
            return
        }
        guard let body = message.body as? [String: Any], let cmd = body["cmd"] as? String else {
            replyHandler(nil, "Malformed message")
            return
        }
        switch cmd {
        case "pickFolder":
            Bridge.pickFolder(in: window) { replyHandler($0, nil) }
        default:
            replyHandler(nil, "Unknown command \(cmd)")
        }
    }

    static func pickFolder(in window: NSWindow?, completion: @escaping (String?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Open"
        panel.message = "Choose the folder of a paper."
        let done: (NSApplication.ModalResponse) -> Void = { response in
            completion(response == .OK ? panel.url?.path : nil)
        }
        if let window {
            panel.beginSheetModal(for: window, completionHandler: done)
        } else {
            done(panel.runModal())
        }
    }
}
