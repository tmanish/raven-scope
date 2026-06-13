"""macOS WiFi RSSI capture.

Apple has made this genuinely annoying over the years:
  * the classic `airport -I` private binary was removed in recent macOS;
  * `wdutil info` reports RSSI but requires sudo;
  * `system_profiler SPAirPortDataType` works with NO sudo but is slow (~1s).

On top of that, modern macOS (Sonoma+) *redacts the network name (SSID)* from
these tools unless the calling process has Location Services permission. The
RSSI still comes through — so sensing works fine — but the name may be blank.
We try an extra `networksetup -getairportnetwork` read to recover the name when
possible, and otherwise leave it empty so the UI can fall back to the BSSID.

The no-sudo `system_profiler` path also parses the *Other Local Wi-Fi Networks*
block, so RavenScope gets several illuminators on macOS without sudo — not just
the connected AP. Note that macOS only refreshes that neighbor scan every
~30-60s, so neighbor links update slowly and contribute less to live motion
than the connected AP, whose RSSI is read fresh on every call.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import List, Optional, Tuple

from .base import CaptureBackend, Sample

_AIRPORT = ("/System/Library/PrivateFrameworks/Apple80211.framework/"
            "Versions/Current/Resources/airport")

_PROP_SIG = re.compile(r"Signal\s*/\s*Noise:\s*(-?\d+)\s*dBm")
# a "header" line is indented, ends with a colon, and has no value after it
_HEADER = re.compile(r"^(\s+)([^:]+?):\s*$")


def _run(cmd, timeout=6.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout)
        return out.stdout or ""
    except Exception:
        return ""


def _looks_redacted(name: Optional[str]) -> bool:
    if not name:
        return True
    low = name.strip().lower()
    return (low in ("", "<redacted>", "redacted")
            or "not associated" in low
            or "you are not" in low
            or low.startswith("wi-fi power"))


def _section_body(txt: str, label: str) -> str:
    """Return the indented body beneath a `label:` line, stopping at the first
    line that dedents back to (or past) the label's own indentation."""
    lines = txt.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().rstrip(":") == label.rstrip(":") and ln.strip().endswith(":"):
            indent = len(ln) - len(ln.lstrip())
            body = []
            for l2 in lines[i + 1:]:
                if l2.strip() and (len(l2) - len(l2.lstrip())) <= indent:
                    break
                body.append(l2)
            return "\n".join(body)
    return ""


def _networks_in(section: str) -> List[Tuple[str, float]]:
    """Parse (network_name, rssi) pairs from a section body. Each network is a
    bare `Name:` header followed by indented props incl. `Signal / Noise`."""
    out: List[Tuple[str, float]] = []
    cur_name: Optional[str] = None
    for ln in section.splitlines():
        if not ln.strip():
            continue
        sig = _PROP_SIG.search(ln)
        if sig:
            if cur_name is not None:
                out.append((cur_name, float(sig.group(1))))
                cur_name = None
            continue
        head = _HEADER.match(ln)
        if head:
            cur_name = head.group(2).strip()
    return out


class MacOSBackend(CaptureBackend):
    name = "macos"
    description = "macOS RSSI"
    quantity = "rssi"

    def __init__(self, iface: Optional[str] = None, allow_sudo: bool = False):
        self.iface = iface
        self.allow_sudo = allow_sudo
        self._mode = None  # "airport" | "wdutil" | "sysprofiler"
        self._iface_cached = None
        self._ssid_cache = None
        self._ssid_cache_t = 0.0

    # ---- interface + SSID helpers -------------------------------------
    def _wifi_iface(self) -> str:
        if self.iface:
            return self.iface
        if self._iface_cached:
            return self._iface_cached
        dev = "en0"
        txt = _run(["networksetup", "-listallhardwareports"])
        m = re.search(r"Hardware Port:\s*Wi-?Fi\s*\nDevice:\s*(\w+)", txt)
        if m:
            dev = m.group(1)
        self._iface_cached = dev
        return dev

    def _ssid_via_networksetup(self) -> Optional[str]:
        now = time.time()
        if self._ssid_cache is not None and now - self._ssid_cache_t < 5.0:
            return self._ssid_cache or None
        txt = _run(["networksetup", "-getairportnetwork", self._wifi_iface()])
        m = re.search(r"Current Wi-?Fi Network:\s*(.+)", txt)
        name = m.group(1).strip() if m else None
        if _looks_redacted(name):
            name = None
        self._ssid_cache = name or ""
        self._ssid_cache_t = now
        return name

    def _name_for(self, found: Optional[str]) -> Optional[str]:
        if not _looks_redacted(found):
            return found
        return self._ssid_via_networksetup()

    # ---- backend protocol ---------------------------------------------
    def available(self) -> bool:
        if os.path.exists(_AIRPORT):
            self._mode = "airport"
            self.description = "macOS airport RSSI (fast)"
            return True
        if self.allow_sudo:
            self._mode = "wdutil"
            self.description = "macOS wdutil RSSI (sudo)"
            return True
        self._mode = "sysprofiler"
        self.description = "macOS system_profiler RSSI (~1 Hz, no sudo)"
        return True

    def read(self) -> Optional[Sample]:
        if self._mode == "airport":
            return self._read_airport()
        if self._mode == "wdutil":
            return self._read_wdutil()
        return self._read_sysprofiler()

    def _read_airport(self) -> Optional[Sample]:
        txt = _run([_AIRPORT, "-I"])
        m = re.search(r"agrCtlRSSI:\s*(-?\d+)", txt)
        b = re.search(r"BSSID:\s*([0-9a-fA-F:]+)", txt)
        ssid = re.search(r"\bSSID:\s*(.+)", txt)
        if not m:
            return None
        bssid = b.group(1) if b else "connected"
        s = Sample(ts=time.time(), primary=bssid, unit="dBm")
        s.links[bssid] = float(m.group(1))
        name = self._name_for(ssid.group(1).strip() if ssid else None)
        if name:
            s.ssids[bssid] = name
        return s

    def _read_wdutil(self) -> Optional[Sample]:
        txt = _run(["sudo", "-n", "wdutil", "info"])
        m = re.search(r"RSSI\s*:\s*(-?\d+)", txt)
        b = re.search(r"BSSID\s*:\s*([0-9a-fA-F:]+)", txt)
        ssid = re.search(r"\bSSID\s*:\s*(.+)", txt)
        if not m:
            return self._read_sysprofiler()
        bssid = b.group(1) if b else "connected"
        s = Sample(ts=time.time(), primary=bssid, unit="dBm")
        s.links[bssid] = float(m.group(1))
        name = self._name_for(ssid.group(1).strip() if ssid else None)
        if name:
            s.ssids[bssid] = name
        return s

    def _read_sysprofiler(self) -> Optional[Sample]:
        txt = _run(["system_profiler", "SPAirPortDataType"], timeout=8.0)
        s = Sample(ts=time.time(), unit="dBm")

        # --- connected AP (read fresh each call; this is the live link) ---
        cur = _networks_in(_section_body(txt, "Current Network Information"))
        if cur:
            raw, rssi = cur[0]
            resolved = self._name_for(None if _looks_redacted(raw) else raw)
            key = resolved or (raw if not _looks_redacted(raw) else "connected")
            s.primary = key
            s.links[key] = rssi
            if resolved:
                s.ssids[key] = resolved

        # --- neighbors from the last scan (slow refresh; extra illuminators)
        for raw, rssi in _networks_in(_section_body(txt, "Other Local Wi-Fi Networks")):
            if _looks_redacted(raw):
                continue  # no stable identifier — skip rather than add noise
            if raw in s.links:
                continue
            s.links[raw] = rssi
            s.ssids[raw] = raw

        return s if s.links else None
