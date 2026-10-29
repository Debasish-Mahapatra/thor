import Foundation
import Security

/// Where the backend lives, from Resources/backend.json. In the self-contained app (`make dmg`) the
/// paths are relative to Contents/Resources; in the development app (`make app`) they point into the
/// source folder.
struct BackendConfig: Decodable {
    let python: String
    let workdir: String
    let ui: String

    func resolve(_ path: String) -> String {
        if path.hasPrefix("/") { return path }
        let resources = Bundle.main.resourceURL ?? URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        return resources.appendingPathComponent(path).standardizedFileURL.path
    }

    static func load() throws -> BackendConfig {
        let override = ProcessInfo.processInfo.environment["THOR_BACKEND_JSON"]
        guard let url = override.map(URL.init(fileURLWithPath:))
            ?? Bundle.main.url(forResource: "backend", withExtension: "json") else {
            throw BackendError.notConfigured
        }
        return try JSONDecoder().decode(BackendConfig.self, from: Data(contentsOf: url))
    }
}

enum BackendError: LocalizedError {
    case notConfigured
    case noPort(String)
    case exited(Int32)

    var errorDescription: String? {
        switch self {
        case .notConfigured:
            "\(appName) does not know where its backend is. Build the app with `make app`."
        case let .noPort(detail):
            "The backend did not start. \(detail)"
        case let .exited(status):
            "The backend stopped (exit code \(status))."
        }
    }
}

/// Runs `python -m thor serve`, learns its port from the line it prints, and stops it when the app quits.
/// The backend also exits by itself when its stdin closes, so it never outlives a crashed app.
final class Backend {
    let token: String = Backend.randomToken()
    static let logURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/\(appName)/backend.log")

    private var process: Process?
    private var stdin: Pipe?
    private var log: FileHandle?
    var onUnexpectedExit: ((Int32) -> Void)?

    private static func randomToken() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        return bytes.map { String(format: "%02x", $0) }.joined()
    }

    func start(completion: @escaping (Result<Int, Error>) -> Void) {
        let config: BackendConfig
        do {
            config = try BackendConfig.load()
        } catch {
            completion(.failure(error))
            return
        }
        try? FileManager.default.createDirectory(at: Backend.logURL.deletingLastPathComponent(),
                                                 withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: Backend.logURL.path, contents: nil)
        log = try? FileHandle(forWritingTo: Backend.logURL)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: config.resolve(config.python))
        // --setup: install whatever is missing (Claude Code, MacTeX) as soon as the app starts.
        process.arguments = ["-m", "thor", "serve", "--ui", config.resolve(config.ui), "--exit-with-stdin", "--setup"]
        process.currentDirectoryURL = URL(fileURLWithPath: config.resolve(config.workdir))
        var env = ProcessInfo.processInfo.environment
        // The app's own Python must not pick up the user's Python settings or write into the signed bundle.
        for key in ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"] { env.removeValue(forKey: key) }
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["THOR_TOKEN"] = token
        env["PYTHONUNBUFFERED"] = "1"
        process.environment = env

        let stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr

        var finished = false
        let finish: (Result<Int, Error>) -> Void = { result in
            DispatchQueue.main.async {
                guard !finished else { return }
                finished = true
                completion(result)
            }
        }

        var pending = Data()
        stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            self?.log?.write(data)
            pending.append(data)
            if let text = String(data: pending, encoding: .utf8),
               let range = text.range(of: #"THOR_LISTENING port=(\d+)"#, options: .regularExpression),
               let port = Int(text[range].split(separator: "=").last ?? "") {
                finish(.success(port))
            }
        }
        stderr.fileHandleForReading.readabilityHandler = { [weak self] handle in
            self?.log?.write(handle.availableData)
        }
        process.terminationHandler = { [weak self] proc in
            stdout.fileHandleForReading.readabilityHandler = nil
            stderr.fileHandleForReading.readabilityHandler = nil
            finish(.failure(BackendError.exited(proc.terminationStatus)))
            DispatchQueue.main.async {
                if self?.process != nil { self?.onUnexpectedExit?(proc.terminationStatus) }
            }
        }

        do {
            try process.run()
        } catch {
            completion(.failure(error))
            return
        }
        self.process = process
        self.stdin = stdin
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) {
            finish(.failure(BackendError.noPort("It printed no port within 30 seconds. See \(Backend.logURL.path).")))
        }
    }

    func stop() {
        guard let process else { return }
        self.process = nil
        try? stdin?.fileHandleForWriting.close()
        if process.isRunning { process.terminate() }
    }
}
