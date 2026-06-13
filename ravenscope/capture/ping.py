"""Universal fallback backend: gateway link-quality via ping RTT.

When no RSSI tool is reachable (locked-down corporate laptop, unusual OS), we
fall back to a physical proxy that works literally everywhere: the round-trip
time to the default gateway. A moving body changes the WiFi channel, which
triggers rate adaptation and retransmissions, which shows up as RTT *jitter*.
It is a coarser signal than RSSI, but it is real physics and needs nothing but
the `ping` command.

We expose the jitter as a single pseudo-link so the rest of the pipeline is
unchanged. `unit` is marked "proxy" so the dashboard can label it honestly.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from typing import Optional

from .base import CaptureBackend, Sample


def _default_gateway() -> Optional[str]:
    plat = sys.platform
    try:
        if plat == "win32":
            out = subprocess.run(["ipconfig"], capture_output=True, text=True,
                                 timeout=4).stdout
            m = re.search(r"Default Gateway[ .]*:\s*([\d.]+)", out)
            return m.group(1) if m else None
        if plat == "darwin":
            out = subprocess.run(["route", "-n", "get", "default"],
                                 capture_output=True, text=True, timeout=4).stdout
            m = re.search(r"gateway:\s*([\d.]+)", out)
            return m.group(1) if m else None
        # linux
        out = subprocess.run(["ip", "route"], capture_output=True, text=True,
                             timeout=4).stdout
        m = re.search(r"default via ([\d.]+)", out)
        return m.group(1) if m else None
    except Exception:
        return None


def _ping_once(host: str) -> Optional[float]:
    plat = sys.platform
    if plat == "win32":
        cmd = ["ping", "-n", "1", "-w", "800", host]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", host]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=2.5).stdout
    except Exception:
        return None
    m = re.search(r"time[=<]\s*([\d.]+)\s*ms", out)
    return float(m.group(1)) if m else None


class PingBackend(CaptureBackend):
    name = "ping"
    description = "Gateway ping-jitter (universal proxy)"
    quantity = "proxy"

    def __init__(self, host: Optional[str] = None):
        self.host = host

    def available(self) -> bool:
        if not self.host:
            self.host = _default_gateway()
        if self.host:
            self.description = f"Gateway ping-jitter to {self.host} (proxy)"
        return self.host is not None

    def read(self) -> Optional[Sample]:
        if not self.host:
            return None
        rtt = _ping_once(self.host)
        if rtt is None:
            return None
        # Encode RTT as a pseudo-dBm so the DSP's despike/detrend works as-is:
        # higher RTT (worse link, more motion-induced retries) -> "weaker".
        pseudo = -50.0 - min(rtt, 200.0) / 4.0
        s = Sample(ts=time.time(), primary="gateway", unit="proxy")
        s.links["gateway"] = pseudo
        s.ssids["gateway"] = "gateway-rtt"
        return s
