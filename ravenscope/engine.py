"""The sensing engine: the loop that turns WiFi readings into room state.

Runs in a background thread. Each tick it:
  1. reads a Sample from the capture backend,
  2. updates per-AP DSP (Hampel -> baseline -> motion energy -> coherence),
  3. fuses links into one room-energy value + coarse sector,
  4. drives the room state machine + anomaly detector,
  5. logs to SQLite and updates an atomic snapshot for the web UI.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .capture import CaptureBackend
from .dsp import AnomalyZScore, LinkProcessor, LinkStats
from .fusion import dominant_sector, fuse_energy, sector_energies
from .state import RoomState, StateConfig
from .store import Store

_SECTOR_NAMES = ["left", "center", "right"]


@dataclass
class Snapshot:
    ts: float = 0.0
    energy: float = 0.0
    z: float = 0.0
    state: str = "EMPTY"
    dwell: float = 0.0
    n_links: int = 0
    n_reliable: int = 0
    calibrating: bool = True
    calib_remaining: float = 0.0
    backend: str = ""
    backend_desc: str = ""
    unit: str = "dBm"
    sector: str = ""
    sector_conf: float = 0.0
    sectors: List[float] = field(default_factory=list)
    links: List[dict] = field(default_factory=list)
    ruview_note: str = ""
    ruview_installed: bool = False

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class SensingEngine:
    def __init__(self, backend: CaptureBackend, store: Optional[Store] = None,
                 interval: float = 0.4, calibrate_seconds: float = 30.0,
                 state_cfg: Optional[StateConfig] = None,
                 ruview_note: str = "", ruview_installed: bool = False):
        self.backend = backend
        self.store = store
        self.interval = interval
        self.calibrate_seconds = calibrate_seconds
        self.procs: Dict[str, LinkProcessor] = {}
        self.order: List[str] = []
        self.room = RoomState(state_cfg)
        self.anomaly = AnomalyZScore()
        self.snapshot = Snapshot(backend=backend.name,
                                 backend_desc=backend.description,
                                 ruview_note=ruview_note,
                                 ruview_installed=ruview_installed)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._listeners: List[Callable[[dict], None]] = []
        self._t_start = 0.0

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._t_start = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def add_listener(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def get_snapshot(self) -> dict:
        with self._lock:
            return self.snapshot.to_dict()

    # -- main loop ---------------------------------------------------------
    def _run(self) -> None:
        calib_done = False
        while not self._stop.is_set():
            tick = time.time()
            elapsed = tick - self._t_start
            calibrating = elapsed < self.calibrate_seconds

            sample = None
            try:
                sample = self.backend.read()
            except Exception:
                sample = None

            if sample and sample.links:
                self._process(sample, calibrating)

            if not calibrating and not calib_done:
                for p in self.procs.values():
                    p.finalize_calibration()
                calib_done = True

            # pace the loop
            dt = time.time() - tick
            time.sleep(max(0.0, self.interval - dt))

    def _process(self, sample, calibrating: bool) -> None:
        for bssid, rssi in sample.links.items():
            if bssid not in self.procs:
                self.procs[bssid] = LinkProcessor(
                    bssid, ssid=sample.ssids.get(bssid, ""))
                self.order.append(bssid)
            self.procs[bssid].push(rssi, calibrating)

        link_stats = {b: p.stats for b, p in self.procs.items()}
        fused, n_reliable = fuse_energy(link_stats)
        sec_e = sector_energies(link_stats, self.order, sectors=3)
        sec_idx, sec_conf = dominant_sector(sec_e)

        z, is_anom = self.anomaly.push(fused)
        state, transition = self.room.update(fused, now=sample.ts)

        # build link view sorted by signal strength
        links_view = []
        for b in self.order:
            st = self.procs[b].stats
            links_view.append({
                "bssid": b,
                "ssid": st.ssid or "(hidden)",
                "rssi": round(st.last_rssi, 1),
                "energy": round(st.energy, 3),
                "coherence": round(st.coherence, 3),
                "reliable": st.reliable,
            })
        links_view.sort(key=lambda d: d["rssi"], reverse=True)

        snap = Snapshot(
            ts=sample.ts,
            energy=round(fused, 4),
            z=round(z, 2),
            state=state,
            dwell=round(self.room.dwell, 1),
            n_links=len(sample.links),
            n_reliable=n_reliable,
            calibrating=calibrating,
            calib_remaining=max(0.0, self.calibrate_seconds -
                                (time.time() - self._t_start)),
            backend=self.backend.name,
            backend_desc=self.backend.description,
            unit=sample.unit,
            sector=_SECTOR_NAMES[sec_idx] if sec_idx >= 0 else "",
            sector_conf=round(sec_conf, 2),
            sectors=[round(x, 3) for x in sec_e],
            links=links_view,
            ruview_note=self.snapshot.ruview_note,
            ruview_installed=self.snapshot.ruview_installed,
        )
        with self._lock:
            self.snapshot = snap
            listeners = list(self._listeners)

        if self.store:
            self.store.log_sample(sample.ts, fused, state, len(sample.links), z)
            if transition:
                self.store.log_event(sample.ts, "transition", transition)
            if is_anom:
                self.store.log_event(
                    sample.ts, "anomaly", f"z={z:.1f} energy={fused:.2f}")

        if transition:
            self._emit_event("transition", transition, sample.ts)
        if is_anom:
            self._emit_event("anomaly", f"z={z:.1f}", sample.ts)

        payload = snap.to_dict()
        for fn in listeners:
            try:
                fn(payload)
            except Exception:
                pass

    def _emit_event(self, kind: str, detail: str, ts: float) -> None:
        ev = {"_event": kind, "detail": detail, "ts": ts}
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(ev)
            except Exception:
                pass
