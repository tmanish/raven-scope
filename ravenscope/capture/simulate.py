"""Physics-grounded RSSI simulator.

This is NOT the product — the product senses your real WiFi. The simulator
exists so the pipeline and dashboard can be exercised and tested without a
live radio (CI, sandboxes, "try before you deploy"). It models a small set of
access points at different bearings, each with:

  * a slow baseline drift + Gaussian beacon jitter (the "still room"),
  * scripted occupancy: a person enters, moves, settles, leaves,
  * motion that perturbs the APs *nearest the person's bearing* most,
  * the occasional sharp "lunge" to exercise the anomaly detector.

Enabled only with the explicit --simulate flag.
"""

from __future__ import annotations

import math
import random
import time
from typing import List, Optional

from .base import CaptureBackend, Sample


class _AP:
    def __init__(self, bssid: str, ssid: str, base: float, bearing: float):
        self.bssid = bssid
        self.ssid = ssid
        self.base = base
        self.bearing = bearing  # degrees, 0..360
        self.drift = 0.0


class SimulateBackend(CaptureBackend):
    name = "simulate"
    description = "Physics simulator (no radio)"
    quantity = "rssi"

    def __init__(self, seed: Optional[int] = None, n_aps: int = 5):
        self.rng = random.Random(seed)
        names = ["HomeMesh-5G", "HomeMesh-2G", "NETGEAR47", "xfinitywifi",
                 "ATT-Guest", "eero-upstairs", "Linksys00231"]
        self.aps: List[_AP] = []
        for i in range(n_aps):
            self.aps.append(_AP(
                bssid=f"02:00:00:00:00:{i:02x}",
                ssid=names[i % len(names)],
                base=-40.0 - self.rng.uniform(0, 35),
                bearing=(360.0 / n_aps) * i,
            ))
        self.t0 = time.time()
        # occupancy script: (start_s, end_s, person_bearing_deg, intensity)
        self.script = [
            (8, 26, 30, 0.6),     # someone enters from the NE, walks around
            (26, 50, 30, 0.12),   # settles / sits still (quiet occupancy)
            (50, 58, 210, 1.0),   # crosses the room fast (SW) -> anomaly-ish
            (58, 80, 0, 0.0),     # empty
        ]
        self.period = 90.0

    def available(self) -> bool:
        return True

    def _intensity_and_bearing(self, t: float):
        tt = t % self.period
        for (a, b, bearing, inten) in self.script:
            if a <= tt < b:
                return inten, bearing
        return 0.0, 0.0

    def read(self) -> Optional[Sample]:
        now = time.time()
        t = now - self.t0
        inten, person_bearing = self._intensity_and_bearing(t)
        s = Sample(ts=now, unit="dBm", primary=self.aps[0].bssid)
        for ap in self.aps:
            ap.drift += self.rng.gauss(0, 0.05)
            ap.drift = max(-3, min(3, ap.drift))
            val = ap.base + ap.drift + self.rng.gauss(0, 0.6)  # beacon jitter
            if inten > 0:
                # APs near the person's bearing are perturbed more strongly
                d = abs((ap.bearing - person_bearing + 180) % 360 - 180)
                w = math.cos(math.radians(min(d, 90)))  # 1 at 0°, 0 at >=90°
                w = max(0.0, w)
                # correlated multipath fade: a few-Hz wobble + random fade
                wobble = math.sin(t * 2 * math.pi * 1.3 + ap.bearing) * 2.5
                fade = self.rng.gauss(0, 3.5)
                val += inten * w * (wobble + fade)
            s.links[ap.bssid] = val
            s.ssids[ap.bssid] = ap.ssid
        return s
