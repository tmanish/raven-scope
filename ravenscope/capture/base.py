"""Capture backend interface.

A backend's only job: every time `read()` is called, return a `Sample` holding
the current RSSI (or RSSI-proxy) for every access point it can currently see.
The engine takes care of timing, DSP, and state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class Sample:
    ts: float
    # bssid -> rssi in dBm (negative; closer to 0 = stronger).
    # For percent-only platforms we convert to a pseudo-dBm so the DSP sees a
    # consistent scale: dBm ≈ percent/2 - 100.
    links: Dict[str, float] = field(default_factory=dict)
    ssids: Dict[str, str] = field(default_factory=dict)
    primary: Optional[str] = None      # the connected AP's bssid, if known
    unit: str = "dBm"                  # "dBm" or "proxy"

    @property
    def n(self) -> int:
        return len(self.links)


class CaptureBackend:
    name = "base"
    #: human description shown in the capability report / dashboard
    description = "abstract backend"
    #: what physical quantity this backend yields
    quantity = "rssi"

    def available(self) -> bool:
        """Cheap check: can this backend run on this machine right now?"""
        return False

    def read(self) -> Optional[Sample]:
        """Return a fresh Sample, or None if nothing could be read."""
        raise NotImplementedError

    def close(self) -> None:
        pass


def percent_to_pseudo_dbm(pct: float) -> float:
    """Map a 0-100 link-quality percentage to a pseudo-dBm scale so the DSP
    can treat every platform uniformly. -100 dBm (0%) .. -50 dBm (100%)."""
    pct = max(0.0, min(100.0, pct))
    return pct / 2.0 - 100.0
