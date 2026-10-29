import AppKit
import UniformTypeIdentifiers
import WebKit

/// Answers `window.webkit.messageHandlers.thor.postMessage(...)` from the interface.
final class Bridge: NSObject, WKScriptMessageHandlerWithReply {
    weak var window: NSWindow?
    /// The last mouse-down, which starts a window drag when the page asks for one.
    private var lastMouseDown: NSEvent?
    private var monitor: Any?

    override init() {
        super.init()
        monitor = NSEvent.addLocalMonitorForEvents(matching: .leftMouseDown) { [weak self] event in
            self?.lastMouseDown = event
            return event
        }
    }

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
        case "pickPdf":
            Bridge.pickPdf(in: window) { replyHandler($0, nil) }
        case "dragWindow":
            // The toolbars stand in for the title bar: pressing on an empty part of one moves the window.
            replyHandler(nil, nil)
            if let window, let event = lastMouseDown {
                DispatchQueue.main.async { window.performDrag(with: event) }
            }
        case "titlebarDoubleClick":
            replyHandler(nil, nil)
            Bridge.titlebarDoubleClick(window)
        default:
            replyHandler(nil, "Unknown command \(cmd)")
        }
    }

    /// What double-clicking a title bar does, as set in System Settings > Desktop & Dock.
    static func titlebarDoubleClick(_ window: NSWindow?) {
        guard let window else { return }
        switch UserDefaults.standard.string(forKey: "AppleActionOnDoubleClick") {
        case "Minimize": window.performMiniaturize(nil)
        case "None": break
        default: window.performZoom(nil)
        }
    }

    static func pickPdf(in window: NSWindow?, completion: @escaping (String?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowedContentTypes = [.pdf]
        panel.allowsMultipleSelection = false
        panel.prompt = "Import"
        panel.message = "Choose a co-author's annotated PDF."
        let done: (NSApplication.ModalResponse) -> Void = { response in
            completion(response == .OK ? panel.url?.path : nil)
        }
        if let window {
            panel.beginSheetModal(for: window, completionHandler: done)
        } else {
            done(panel.runModal())
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
