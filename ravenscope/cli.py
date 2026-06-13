"""RavenScope command-line entry point.

Examples
--------
    raven-scope                     # auto-detect WiFi, open dashboard
    raven-scope --simulate          # preview with the physics simulator
    raven-scope --backend ping      # universal gateway-jitter fallback
    raven-scope --calibrate 45      # longer baseline learning
    raven-scope --port 9000 --no-browser
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser

from .capture import select_backend
from .capture.ruview_csi import probe as probe_ruview
from .engine import SensingEngine
from .server import make_server
from .state import StateConfig
from .store import Store

_BANNER = r"""
 ___                   ___
| _ \__ ___ _____ _ _ / __| __ ___ _ __  ___   WiFi Motion Radar
|   / _` \ V / -_) ' \__ \/ _/ _ \ '_ \/ -_)  no camera · no extra hardware
|_|_\__,_|\_/\___|_||_|___/\__\___/ .__/\___|  RuView "$0 / Any WiFi" tier
                                  |_|
"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="raven-scope",
        description="Turn your laptop's WiFi into a live motion/presence radar.")
    p.add_argument("--backend", default="auto",
                   choices=["auto", "linux", "macos", "windows", "ping", "simulate"],
                   help="capture backend (default: auto-detect)")
    p.add_argument("--simulate", action="store_true",
                   help="run the built-in physics simulator (no radio needed)")
    p.add_argument("--iface", default=None, help="WiFi interface name override")
    p.add_argument("--ping-host", default=None,
                   help="host for the ping fallback (default: gateway)")
    p.add_argument("--allow-sudo", action="store_true",
                   help="macOS: permit `sudo wdutil` for faster RSSI")
    p.add_argument("--interval", type=float, default=0.4,
                   help="seconds between samples (default 0.4)")
    p.add_argument("--calibrate", type=float, default=30.0,
                   help="baseline calibration seconds (default 30)")
    p.add_argument("--db", default="ravenscope.db", help="SQLite log path")
    p.add_argument("--no-store", action="store_true", help="disable logging")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8731)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--headless", action="store_true",
                   help="no server/UI; print state to the terminal")
    args = p.parse_args(argv)

    kind = "simulate" if args.simulate else args.backend

    print(_BANNER)
    try:
        backend = select_backend(kind=kind, iface=args.iface,
                                 allow_sudo=args.allow_sudo,
                                 ping_host=args.ping_host)
    except RuntimeError as e:
        print(f"  ✗ {e}", file=sys.stderr)
        return 2

    rv = probe_ruview()
    print(f"  capture backend : {backend.name} — {backend.description}")
    print(f"  RuView CSI core : {'detected v'+str(rv.version) if rv.installed else 'not installed'}")
    print(f"  sample interval : {args.interval:.2f}s   calibration: {args.calibrate:.0f}s")
    if backend.quantity == "proxy":
        print("  note            : using ping-jitter proxy (no RSSI tool found)")
    print()

    store = None if args.no_store else Store(args.db)
    engine = SensingEngine(
        backend=backend, store=store, interval=args.interval,
        calibrate_seconds=args.calibrate, state_cfg=StateConfig(),
        ruview_note=rv.note, ruview_installed=rv.installed)
    engine.start()

    if args.headless:
        return _headless_loop(engine)

    httpd = make_server(engine, store, host=args.host, port=args.port)
    url = f"http://{args.host}:{args.port}/"
    print(f"  ▶ dashboard live at  {url}")
    print("    (Ctrl-C to stop)\n")
    if not args.no_browser:
        threading.Timer(0.8, lambda: _open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopping…")
    finally:
        httpd.shutdown()
        engine.stop()
        if store:
            store.close()
    return 0


def _open(url):
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _headless_loop(engine) -> int:
    try:
        while True:
            s = engine.get_snapshot()
            bar = "█" * int(min(1.0, s.get("energy", 0)) * 30)
            cal = " [calibrating]" if s.get("calibrating") else ""
            sys.stdout.write(
                f"\r{s.get('state','?'):>7}  energy {s.get('energy',0):.3f} "
                f"|{bar:<30}| links {s.get('n_links',0)}{cal}   ")
            sys.stdout.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n  stopping…")
        engine.stop()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
