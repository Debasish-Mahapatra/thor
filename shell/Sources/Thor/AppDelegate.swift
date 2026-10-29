import AppKit
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private let backend = Backend()
    private let bridge = Bridge()
    private var port: Int?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.mainMenu = makeMenu()
        makeWindow()
        showMessage("Starting \(appName)...")
        backend.onUnexpectedExit = { [weak self] status in
            self?.showMessage("\(appName)'s backend stopped (exit code \(status)). Its log is in \(Backend.logURL.path). Quit and reopen \(appName).")
        }
        backend.start { [weak self] result in
            guard let self else { return }
            switch result {
            case let .success(port):
                self.port = port
                self.loadInterface()
            case let .failure(error):
                self.showMessage(error.localizedDescription)
            }
        }
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationWillTerminate(_ notification: Notification) {
        backend.stop()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    // MARK: Window

    private func makeWindow() {
        let config = WKWebViewConfiguration()
        config.userContentController.addScriptMessageHandler(bridge, contentWorld: .page, name: "thor")
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        if #available(macOS 13.3, *) { webView.isInspectable = true }

        // No title bar: the page runs under it, and the window buttons sit in the page's own toolbar.
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = appName
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        // An empty compact toolbar only sets the height the window buttons are centred in.
        window.toolbar = NSToolbar(identifier: "main")
        window.toolbarStyle = .unifiedCompact
        window.minSize = NSSize(width: 760, height: 520)
        window.contentView = webView
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("MainWindow")
        window.makeKeyAndOrderFront(nil)
        bridge.window = window
        tellPageAboutTitlebar()
    }

    /// The page sizes its toolbar to the title bar's height and leaves room for the window buttons.
    private func tellPageAboutTitlebar() {
        let height = window.frame.height - window.contentLayoutRect.height
        let zoom = window.standardWindowButton(.zoomButton)
        let left = (zoom.map { $0.convert($0.bounds, to: nil).maxX } ?? 70) + 14
        let script = """
        document.documentElement.classList.add('native-titlebar');
        document.documentElement.style.setProperty('--titlebar-height', '\(Int(height.rounded()))px');
        document.documentElement.style.setProperty('--titlebar-left', '\(Int(left.rounded()))px');
        """
        webView.configuration.userContentController.addUserScript(
            WKUserScript(source: script, injectionTime: .atDocumentStart, forMainFrameOnly: true))
    }

    // In full screen the window buttons are hidden, so the toolbar takes the whole width.
    func windowWillEnterFullScreen(_ notification: Notification) {
        webView.evaluateJavaScript("document.documentElement.classList.add('fullscreen')")
    }

    func windowWillExitFullScreen(_ notification: Notification) {
        webView.evaluateJavaScript("document.documentElement.classList.remove('fullscreen')")
    }

    private func loadInterface() {
        guard let port else { return }
        webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(port)/auth?token=\(backend.token)")!))
    }

    private func showMessage(_ text: String) {
        let escaped = text.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        webView.loadHTMLString("""
        <!doctype html><meta name="color-scheme" content="light dark">
        <body style="font: 14px -apple-system; display: grid; place-items: center; height: 90vh; margin: 0;
        color: #6e6e73; padding: 0 40px; text-align: center">\(escaped)</body>
        """, baseURL: nil)
    }

    /// Sends a menu command to the interface, which listens for "thor-shell" events.
    private func send(_ cmd: String, _ arg: String? = nil) {
        var detail: [String: String] = ["cmd": cmd]
        if let arg { detail["arg"] = arg }
        guard let data = try? JSONSerialization.data(withJSONObject: detail),
              let json = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('thor-shell', {detail: \(json)}))")
    }

    // MARK: Navigation: links to other sites open in the default browser.

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, let host = url.host, host != "127.0.0.1",
           url.scheme == "http" || url.scheme == "https" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    // MARK: Menu

    @objc private func openProject(_ sender: Any?) {
        Bridge.pickFolder(in: window) { [weak self] path in
            if let path { self?.send("openProject", path) }
        }
    }

    @objc private func openSettings(_ sender: Any?) {
        send("openSettings")
    }

    @objc private func reload(_ sender: Any?) {
        loadInterface()
    }

    private func makeMenu() -> NSMenu {
        let main = NSMenu()

        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About \(appName)", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(item("Settings...", #selector(openSettings(_:)), ","))
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide \(appName)", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthers = appMenu.addItem(withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit \(appName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        add(appMenu, titled: appName, to: main)

        let file = NSMenu(title: "File")
        file.addItem(item("Open Project...", #selector(openProject(_:)), "o"))
        file.addItem(.separator())
        file.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        add(file, titled: "File", to: main)

        // WKWebView needs these for copy, paste and undo to work in text fields.
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        add(edit, titled: "Edit", to: main)

        let view = NSMenu(title: "View")
        view.addItem(item("Reload", #selector(reload(_:)), "r"))
        add(view, titled: "View", to: main)

        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        add(windowMenu, titled: "Window", to: main)
        NSApp.windowsMenu = windowMenu
        return main
    }

    private func item(_ title: String, _ action: Selector, _ key: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        return item
    }

    private func add(_ menu: NSMenu, titled title: String, to main: NSMenu) {
        let holder = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        holder.submenu = menu
        main.addItem(holder)
    }
}
