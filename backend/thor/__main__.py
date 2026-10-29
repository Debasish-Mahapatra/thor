"""`python -m thor serve` starts the backend; `python -m thor check` prints the dependency report."""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from . import deps
from .paths import APP_NAME
from .first_run import FirstRun
from .server import create_app
from .settings import KeychainSecret, SettingsStore
from .setup_tex import TexInstaller

DEV_TOKEN = "dev"


def exit_when_stdin_closes() -> None:
    """The app shell holds our stdin open; when the shell quits or crashes, it closes and we follow."""

    def watch() -> None:
        while sys.stdin.buffer.read(1024):
            pass
        os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def serve(args: argparse.Namespace) -> None:
    token = DEV_TOKEN if args.dev else os.environ.get("THOR_TOKEN", "")
    if not token and args.open:
        token = secrets.token_urlsafe(32)
    if not token:
        sys.exit("Set THOR_TOKEN, or pass --open (random token) or --dev (fixed development token).")
    ui_dir = Path(args.ui).resolve() if args.ui else None
    setup = FirstRun(TexInstaller(), enabled=args.setup)
    app = create_app(token, KeychainSecret(), ui_dir, setup=setup)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", args.port))
    except OSError as e:
        sys.exit(f"Cannot listen on 127.0.0.1:{args.port}: {e.strerror}. Another program may be using that port.")
    port = sock.getsockname()[1]
    # The shell reads this line to learn the port.
    print(f"THOR_LISTENING port={port}", flush=True)
    if args.dev:
        print(f"Open http://127.0.0.1:{port}/auth?token={DEV_TOKEN}", flush=True)
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{port}/auth?token={token}")
        print(f"{APP_NAME} is open in your browser. Press Ctrl-C here to stop it.", flush=True)
    if args.exit_with_stdin:
        exit_when_stdin_closes()
    if args.setup and setup.needed():
        setup.start()
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    server.run(sockets=[sock])


def check(_: argparse.Namespace) -> None:
    checks = deps.run_checks(SettingsStore().load().agent, KeychainSecret())
    for group in dict.fromkeys(c.group for c in checks):
        print(group)
        for c in (c for c in checks if c.group == group):
            mark = "ok" if c.ok else ("MISSING" if c.required else "optional")
            print(f"  {mark:<8} {c.name:<18} {c.version or c.detail}")
            if not c.ok and c.fix:
                print(f"           {c.fix}")
    sys.exit(0 if deps.summary(checks)["ok"] else 1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="thor")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("serve", help="start the backend")
    p.add_argument("--port", type=int, default=0, help="0 picks a free port")
    p.add_argument("--ui", help="folder with the built interface (ui/dist)")
    p.add_argument("--dev", action="store_true", help="use the fixed development token")
    p.add_argument("--open", action="store_true", help="open the interface in the default browser")
    p.add_argument("--exit-with-stdin", action="store_true", help="exit when stdin closes (used by the app shell)")
    p.add_argument("--setup", action="store_true", help="install missing Claude Code and MacTeX at start (used by the app shell)")
    p.set_defaults(func=serve)
    c = sub.add_parser("check", help="print the dependency report")
    c.set_defaults(func=check)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
