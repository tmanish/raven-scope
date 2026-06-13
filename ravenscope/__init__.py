"""RavenScope — turn a laptop's WiFi into a live motion / presence radar.

Zero extra hardware. Implements RuView's documented "$0 / Any WiFi" RSSI tier:
Hampel despike -> self-calibrating baseline -> motion-band energy -> coherence
gate -> multi-AP fusion -> debounced room state machine + anomaly detection.

The CSI tier (breathing, heart rate, pose, through-wall) needs CSI hardware
(an ESP32-S3) and is surfaced via the optional RuView integration when present.
"""
from __future__ import annotations

__version__ = "0.1.0"

from .engine import SensingEngine, Snapshot
from .state import RoomState, StateConfig
from .capture import select_backend, Sample

__all__ = ["SensingEngine", "Snapshot", "RoomState", "StateConfig",
           "select_backend", "Sample", "__version__"]
