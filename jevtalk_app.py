#!/usr/bin/env python3
"""JevTalk app: a local page (app/index.html) that shows Jev writing 8 drafts at
once, picking one, and polishing it.

    python3 jevtalk_app.py --open     # in your browser
    ./build_app.sh --install          # in its own window: ~/Applications/JevTalk.app (JevTalkShell.swift)

Serves 127.0.0.1 only, and /ask needs a per-launch token, so other pages can't spend your Jev credit.
Without --open the server exits when stdin closes, i.e. when the window that launched it quits.
"""
import itertools
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jevtalk as J

ROOT = Path(__file__).with_name("app")
TOKEN = secrets.token_urlsafe(18)
history = []           # [{id, question, answer}]: Jev sees these as the conversation so far
turn_ids = itertools.count(1)
busy = threading.Lock()  # one question at a time: jevtalk.stats is global


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        if not secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN):
            return self.send_error(403)
        if self.path == "/clear":
            history.clear()
            self.send_response(204)
            return self.end_headers()
        if self.path == "/forget":  # drop one answer from the conversation Jev sees
            turn = self.body().get("id")
            history[:] = [t for t in history if t["id"] != turn]
            self.send_response(204)
            return self.end_headers()
        if self.path != "/ask":
            return self.send_error(404)
        question = str(self.body().get("question", "")).strip()[:300]
        if not question:
            return self.send_error(400)
        if not busy.acquire(blocking=False):
            return self.send_error(409)
        try:
            self.answer(question)
        finally:
            busy.release()

    def body(self):
        return json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 10_000)) or b"{}")

    def answer(self, question):
        """Stream one event per line (NDJSON) while Jev works."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        lock, started = threading.Lock(), time.time()
        J.stats.update(calls=0, cost=0.0, tokens=0)

        def send(**event):  # called from reply()'s draft threads too
            event["stats"] = {"calls": J.stats["calls"], "cost": J.stats["cost"], "secs": time.time() - started}
            with lock:
                self.wfile.write((json.dumps(event) + "\n").encode())
                self.wfile.flush()

        try:
            reply = J.reply(question, history=[(t["question"], t["answer"]) for t in history],
                            log=lambda s: send(type="log", line=s.strip()),
                            on_text=lambda s: send(type="text", text=s),
                            on_draft=lambda i, s, score=None: send(type="draft", i=i, text=s, score=score))
            turn = {"id": next(turn_ids), "question": question, "answer": reply}
            history.append(turn)
            send(type="done", answer=reply, id=turn["id"])
        except (BrokenPipeError, ConnectionResetError):
            pass  # window closed mid-answer
        except BaseException as e:  # includes SystemExit when no API key is found
            send(type="error", message=str(e) or type(e).__name__)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/#t={TOKEN}"
    print(url, flush=True)
    if "--open" in sys.argv:
        webbrowser.open(url)
    else:
        threading.Thread(target=lambda: (sys.stdin.read(), os._exit(0)), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
