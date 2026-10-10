// Native window for the JevTalk app: starts jevtalk_app.py, shows its page in a WebKit view, quits it on close.
// Built by ./build_app.sh, which copies the Python files into the bundle's Resources folder.
import Cocoa
import WebKit

let pageBackground = NSColor(name: nil) { $0.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
    ? NSColor(red: 0x16 / 255.0, green: 0x16 / 255.0, blue: 0x18 / 255.0, alpha: 1)    // --bg in app/index.html, dark
    : NSColor(red: 0xF5 / 255.0, green: 0xF5 / 255.0, blue: 0xF7 / 255.0, alpha: 1) }  // --bg, light

final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let server = Process()
    let toServer = Pipe()  // the server exits when this closes, even if we crash

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        let out = Pipe()
        // Apps opened from Finder don't get your shell's PATH, so ask your login shell to run python3:
        // that's the same python3 (pyenv, Homebrew, …) you get in Terminal.
        server.executableURL = URL(fileURLWithPath: ProcessInfo.processInfo.environment["SHELL"] ?? "/bin/zsh")
        server.arguments = ["-l", "-i", "-c", "exec python3 jevtalk_app.py"]
        server.currentDirectoryURL = Bundle.main.resourceURL
        server.standardInput = toServer
        server.standardOutput = out
        let log = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/JevTalk.log")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        server.standardError = try? FileHandle(forWritingTo: log)
        do { try server.run() } catch { fail("Couldn’t start Python: \(error.localizedDescription)") }

        var printed = ""  // the server prints its URL; shell startup files may print other lines first
        var url: URL?
        while url == nil {
            let chunk = out.fileHandleForReading.availableData
            if chunk.isEmpty { fail("The JevTalk server stopped. Details are in ~/Library/Logs/JevTalk.log.") }
            printed += String(decoding: chunk, as: UTF8.self)
            url = printed.components(separatedBy: "\n").dropLast()  // complete lines only
                .first { $0.hasPrefix("http://127.0.0.1:") }.flatMap { URL(string: $0) }
        }

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 800),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "JevTalk"
        window.titlebarAppearsTransparent = true
        window.backgroundColor = pageBackground
        window.minSize = NSSize(width: 640, height: 560)
        let web = WKWebView(frame: window.contentView!.bounds)
        web.autoresizingMask = [.width, .height]
        web.setValue(false, forKey: "drawsBackground")
        window.contentView = web
        web.load(URLRequest(url: url!))
        window.center()
        window.setFrameAutosaveName("JevTalkWindow")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ note: Notification) {
        if server.isRunning { server.terminate() }
    }

    func fail(_ message: String) -> Never {
        let alert = NSAlert()
        alert.messageText = "JevTalk couldn’t start"
        alert.informativeText = message
        alert.runModal()
        exit(1)
    }

    func buildMenu() {  // without an Edit menu, ⌘C / ⌘V / ⌘A don't reach the page
        let main = NSMenu()
        let appItem = NSMenuItem()
        appItem.submenu = NSMenu()
        appItem.submenu!.addItem(withTitle: "Hide JevTalk", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appItem.submenu!.addItem(withTitle: "Quit JevTalk", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editItem = NSMenuItem()
        editItem.submenu = NSMenu(title: "Edit")
        for (title, action, key) in [("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), ("Cut", "cut:", "x"),
                                     ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")] {
            editItem.submenu!.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        let windowItem = NSMenuItem()
        windowItem.submenu = NSMenu(title: "Window")
        windowItem.submenu!.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowItem.submenu!.addItem(withTitle: "Minimize", action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m")
        [appItem, editItem, windowItem].forEach(main.addItem)
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
