"""Linux WiFi RSSI capture via `nmcli` (preferred, multi-AP, no root) with an
`iw dev <iface> link` fallback for the connected AP only."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from typing import Optional

from .base import CaptureBackend, Sample


def _run(cmd, timeout=4.0) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return out.stdout or ""
    except Exception:
        return ""


class LinuxBackend(CaptureBackend):
    name = "linux"
    description = "Linux nmcli/iw RSSI"
    quantity = "rssi"

    def __init__(self, iface: Optional[str] = None):
        self.iface = iface
        self._mode = None  # "nmcli" | "iw"

    def available(self) -> bool:
        if shutil.which("nmcli"):
            self._mode = "nmcli"
            return True
        if shutil.which("iw"):
            self._mode = "iw"
            return True
        return False

    def _iface(self) -> Optional[str]:
        if self.iface:
            return self.iface
        # find first wifi device via nmcli
        txt = _run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "dev"])
        for line in txt.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1] == "wifi":
                self.iface = parts[0]
                return self.iface
        # fallback: parse /sys/class/net for a wireless dir
        return None

    def read(self) -> Optional[Sample]:
        if self._mode == "nmcli":
            return self._read_nmcli()
        return self._read_iw()

    def _read_nmcli(self) -> Optional[Sample]:
        # IN-USE,SIGNAL(0-100),SSID,BSSID  — gives every visible AP at once.
        txt = _run([
            "nmcli", "-t", "-e", "no", "-f",
            "IN-USE,SIGNAL,SSID,BSSID", "dev", "wifi", "list",
        ])
        if not txt.strip():
            return self._read_iw()
        s = Sample(ts=time.time(), unit="dBm")
        for line in txt.splitlines():
            # BSSID contains escaped colons; nmcli -e no leaves them raw, so
            # split carefully: first 3 fields are IN-USE, SIGNAL, SSID.
            m = re.match(r"^(\*?):(\d+):(.*):([0-9A-Fa-f:]{17})$", line)
            if not m:
                continue
            in_use, sig, ssid, bssid = m.groups()
            try:
                pct = float(sig)
            except ValueError:
                continue
            # nmcli SIGNAL is 0-100; convert to pseudo-dBm consistent scale
            s.links[bssid] = pct / 2.0 - 100.0
            s.ssids[bssid] = ssid
            if in_use == "*":
                s.primary = bssid
        return s if s.links else self._read_iw()

    def _read_iw(self) -> Optional[Sample]:
        iface = self._iface()
        if not iface:
            return None
        txt = _run(["iw", "dev", iface, "link"])
        m = re.search(r"signal:\s*(-?\d+)\s*dBm", txt)
        b = re.search(r"Connected to ([0-9a-fA-F:]{17})", txt)
        sm = re.search(r"SSID:\s*(.+)", txt)
        if not m:
            return None
        bssid = b.group(1) if b else "connected"
        s = Sample(ts=time.time(), primary=bssid, unit="dBm")
        s.links[bssid] = float(m.group(1))
        if sm:
            s.ssids[bssid] = sm.group(1).strip()
        return s
