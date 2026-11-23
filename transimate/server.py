"""Transimate Assistant HTTP server.

Serves the web UI, JSON API, and a Server-Sent-Events live stream that
pushes the simulation snapshot on every tick. Python stdlib only.
"""

import json
import os
import queue
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .agent import TransimateAgent, CAPABILITY_LABELS
from . import network
from .simulation import Simulation

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


class TransportServer:
    def __init__(self, port=7860):
        self.sim = Simulation()
        self.agent = TransimateAgent(self.sim)
        self.port = port
        self.sse_clients = []          # list of queue.Queue
        self.lock = threading.Lock()
        self.running = False

    def start(self):
        self.running = True
        threading.Thread(target=self._tick_loop, daemon=True).start()
        handler = self._make_handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        print(f"\n  Transimate Assistant running at  http://127.0.0.1:{self.port}\n")
        self.httpd.serve_forever()

    def _tick_loop(self):
        while self.running:
            time.sleep(1.0)
            self.sim.tick()
            self._broadcast()

    def _broadcast(self):
        snap = self.sim.snapshot()
        payload = f"data: {json.dumps(snap)}\n\n"
        with self.lock:
            for q in list(self.sse_clients):
                try:
                    q.put_nowait(payload)
                except Exception:
                    self.sse_clients.remove(q)

    # -- request routing ---------------------------------------------------
    @staticmethod
    def _action_text(a):
        t = a.get("type")
        if t == "shift_capacity":
            extra = f" Priority action at {a['hot_stop']}." if a.get("hot_stop") else ""
            return f"Shift capacity {a['from_route']} -> {a['to_route']}: {a['reason']}.{extra}"
        if t == "cover_breakdown":
            return f"Cover breakdown: {a['reason']}."
        if t == "monitor":
            return a["reason"]
        return a.get("reason", str(a))

    def _make_handler(self):
        outer = self

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send_json(self, obj, code=200):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/api/state":
                    return self._send_json(outer.sim.snapshot())
                if parsed.path == "/api/capabilities":
                    return self._send_json({"capabilities": CAPABILITY_LABELS})
                if parsed.path == "/api/stops":
                    return self._send_json({
                        "positions": {sid: list(s["pos"]) for sid, s in network.ALL_STOPS.items()},
                        "names": {sid: s["name"] for sid, s in network.ALL_STOPS.items()},
                    })
                if parsed.path == "/api/admin":
                    sim = outer.sim
                    actions = sim.redistribution_plan()
                    return self._send_json({
                        "clock": sim.clock_str(),
                        "actions": [{"text": outer._action_text(a), "priority": a.get("priority", "low")} for a in actions],
                        "additional_bus": sim.additional_bus_recommendation(),
                        "longest_delays": sim.longest_delays(),
                    })
                if parsed.path == "/api/chat":
                    qs = parse_qs(parsed.query)
                    msg = (qs.get("q") or [""])[0]
                    persona = (qs.get("persona") or ["student"])[0]
                    if not msg.strip():
                        return self._send_json({"error": "empty query"}, 400)
                    reply = outer.agent.chat(msg, persona)
                    out = reply.to_dict()
                    out["capabilityLabel"] = CAPABILITY_LABELS.get(reply.capability, reply.capability)
                    return self._send_json({"query": msg, "persona": persona, **out})
                if parsed.path == "/api/events":
                    return self._stream_events()
                if parsed.path in ("/", "/index.html"):
                    return self._serve_file("index.html", "text/html")
                return self._serve_static(parsed.path)

            def _stream_events(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q = queue.Queue(maxsize=64)
                with outer.lock:
                    outer.sse_clients.append(q)
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            payload = q.get(timeout=15)
                            self.wfile.write(payload.encode("utf-8"))
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with outer.lock:
                        if q in outer.sse_clients:
                            outer.sse_clients.remove(q)

            def _serve_static(self, path):
                rel = path.lstrip("/")
                full = os.path.normpath(os.path.join(WEB_DIR, rel))
                if not full.startswith(WEB_DIR) or not os.path.isfile(full):
                    return self._send_json({"error": "not found"}, 404)
                return self._serve_file(rel, self._guess_type(rel))

            def _serve_file(self, rel, ctype):
                full = os.path.normpath(os.path.join(WEB_DIR, rel))
                if not os.path.isfile(full):
                    return self._send_json({"error": "not found"}, 404)
                with open(full, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)

            @staticmethod
            def _guess_type(rel):
                ext = os.path.splitext(rel)[1].lower()
                return {
                    ".html": "text/html; charset=utf-8",
                    ".css": "text/css; charset=utf-8",
                    ".js": "application/javascript; charset=utf-8",
                    ".json": "application/json",
                    ".svg": "image/svg+xml",
                }.get(ext, "application/octet-stream")

        return Handler


def main():
    import argparse
    p = argparse.ArgumentParser(description="Transimate Assistant - AI Campus Transport Agent")
    p.add_argument("--port", type=int, default=7860)
    args = p.parse_args()
    TransportServer(args.port).start()


if __name__ == "__main__":
    main()