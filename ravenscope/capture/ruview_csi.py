"""Optional RuView / wifi-densepose integration.

RavenScope's live sensing runs on the RSSI tier (zero extra hardware). RuView's
CSI tier — breathing, heart rate, 17-keypoint pose, through-wall — needs the
full Channel State Information that only CSI hardware (an ESP32-S3, ~$9) or a
research NIC produces. A laptop's WiFi driver does not expose CSI.

This module does the honest thing: it *probes* for the RuView core and reports
whether the CSI extractors are importable on this machine, so the dashboard can
light up the CSI tier the moment a real CSI stream is wired in — and otherwise
say plainly that it isn't available. It never fakes vitals from RSSI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RuViewStatus:
    installed: bool
    version: Optional[str]
    extractors: list
    note: str


def probe() -> RuViewStatus:
    note_rssi = ("RuView CSI core not importable here — running the RSSI tier "
                 "(presence + motion). Add an ESP32-S3 CSI node to unlock "
                 "breathing, heart rate, pose, and through-wall.")
    mod = None
    version = None
    name = None
    for candidate in ("ruview", "wifi_densepose"):
        try:
            mod = __import__(candidate)
            name = candidate
            version = getattr(mod, "__version__", None)
            break
        except Exception:
            continue

    if mod is None:
        return RuViewStatus(False, None, [], note_rssi)

    found = []
    for attr in ("BreathingExtractor", "HeartRateExtractor",
                 "PoseEstimator", "SensingClient"):
        if hasattr(mod, attr):
            found.append(attr)
    note = (f"RuView core '{name}' v{version or '?'} detected. CSI extractors "
            f"available: {', '.join(found) or 'none exposed'}. They still need "
            f"a live CSI source (ESP32-S3) — RSSI alone cannot feed them.")
    return RuViewStatus(True, version, found, note)
