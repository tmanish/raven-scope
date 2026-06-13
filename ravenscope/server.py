"""Local web server (stdlib only).

Serves the single-file dashboard and three endpoints:
  GET /                -> dashboard
  GET /api/state       -> current snapshot (JSON)
  GET /api/history     -> recent motion track + events + occupancy heatmap
  GET /api/stream      -> Server-Sent Events: live snapshots + discrete events

No framework, no websockets — just http.server + SSE so it runs anywhere Python
runs, on localhost, private to the machine.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .engine import SensingEngine
from .store import Store

_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")


class _Handler(BaseHTTPRequestHandler):
    engine: SensingEngine = None       # set on the server class
    store: Optional[Store] = None

    def log_message(self, *args):       # silence default logging
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            return self._serve_file("index.html", "text/html; charset=utf-8")
        if path == "/api/state":
            return self._send(200, json.dumps(self.engine.get_snapshot()).encode())
        if path == "/api/history":
            return self._history()
        if path == "/api/stream":
            return self._stream()
        self._send(404, b'{"error":"not found"}')

    def _serve_file(self, name, ctype):
        fp = os.path.join(_WEB_DIR, name)
        try:
            with open(fp, "rb") as f:
                self._send(200, f.read(), ctype)
        except OSError:
            self._send(404, b"missing asset")

    def _history(self):
        secs = 900
        out = {"samples": [], "events": [], "occupancy": []}
        if self.store:
            out["samples"] = [
                {"ts": ts, "energy": e, "state": s, "z": z}
                for (ts, e, s, z) in self.store.recent_samples(secs)
            ]
            out["events"] = [
                {"ts": ts, "kind": k, "detail": d}
                for (ts, k, d) in self.store.recent_events(40)
            ]
            out["occupancy"] = self.store.occupancy_by_hour(7)
        self._send(200, json.dumps(out).encode())

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q: "queue.Queue[dict]" = queue.Queue(maxsize=100)

        def listener(payload: dict):
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

        self.engine.add_listener(listener)
        # prime with current state
        try:
            self._sse_write(self.engine.get_snapshot())
        except Exception:
            pass
        try:
            last_ping = time.time()
            while True:
                try:
                    payload = q.get(timeout=1.0)
                    self._sse_write(payload)
                except queue.Empty:
                    if time.time() - last_ping > 10:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_ping = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.engine.remove_listener(listener)

    def _sse_write(self, payload: dict):
        ev = "event" if "_event" in payload else "state"
        data = json.dumps(payload)
        self.wfile.write(f"event: {ev}\ndata: {data}\n\n".encode())
        self.wfile.flush()


def make_server(engine: SensingEngine, store: Optional[Store],
                host: str = "127.0.0.1", port: int = 8731) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (_Handler,),
                   {"engine": engine, "store": store})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd
