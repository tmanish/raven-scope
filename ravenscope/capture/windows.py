"""Windows WiFi RSSI capture via `netsh wlan`.

`netsh wlan show interfaces` gives the connected AP's signal as a percentage.
`netsh wlan show networks mode=bssid` enumerates every visible BSSID with a
per-BSSID signal percentage, which we use for multi-AP fusion. Windows reports
signal *quality* (0-100%), not dBm, so we map it onto a pseudo-dBm scale.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from typing import Optional

from .base import CaptureBackend, Sample, percent_to_pseudo_dbm


def _run(cmd, timeout=6.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout)
        return out.stdout or ""
    except Exception:
        return ""


class WindowsBackend(CaptureBackend):
    name = "windows"
    description = "Windows netsh RSSI (signal %)"
    quantity = "rssi"

    def available(self) -> bool:
        return shutil.which("netsh") is not None

    def read(self) -> Optional[Sample]:
        s = Sample(ts=time.time(), unit="dBm")

        # 1) connected AP (fast, always works)
        itxt = _run(["netsh", "wlan", "show", "interfaces"])
        bssid = None
        bm = re.search(r"BSSID\s*:\s*([0-9a-fA-F:]+)", itxt)
        sm = re.search(r"Signal\s*:\s*(\d+)%", itxt)
        nm = re.search(r"\bSSID\s*:\s*(.+)", itxt)
        if bm and sm:
            bssid = bm.group(1).strip()
            s.primary = bssid
            s.links[bssid] = percent_to_pseudo_dbm(float(sm.group(1)))
            if nm:
                s.ssids[bssid] = nm.group(1).strip()

        # 2) all visible BSSIDs (richer fusion). This scan is a touch slower; if
        #    it returns nothing we still have the connected link above.
        ntxt = _run(["netsh", "wlan", "show", "networks", "mode=bssid"])
        cur_ssid = ""
        for line in ntxt.splitlines():
            ssm = re.match(r"^SSID\s+\d+\s*:\s*(.*)$", line)
            if ssm:
                cur_ssid = ssm.group(1).strip()
                continue
            bm2 = re.match(r"^\s*BSSID\s+\d+\s*:\s*([0-9a-fA-F:]+)", line)
            if bm2:
                _cur_bssid = bm2.group(1).strip()
                s.ssids[_cur_bssid] = cur_ssid
                continue
            sm2 = re.match(r"^\s*Signal\s*:\s*(\d+)%", line)
            if sm2 and "_cur_bssid" in dir():
                pass
        # The above loop needs the bssid carried across lines; do it cleanly:
        s2 = self._parse_networks(ntxt)
        for b, (pct, ss) in s2.items():
            s.links.setdefault(b, percent_to_pseudo_dbm(pct))
            if ss:
                s.ssids.setdefault(b, ss)
        return s if s.links else None

    @staticmethod
    def _parse_networks(txt: str):
        out = {}
        cur_ssid = ""
        cur_bssid = None
        for line in txt.splitlines():
            ssm = re.match(r"^SSID\s+\d+\s*:\s*(.*)$", line)
            if ssm:
                cur_ssid = ssm.group(1).strip()
                cur_bssid = None
                continue
            bm = re.match(r"^\s*BSSID\s+\d+\s*:\s*([0-9a-fA-F:]+)", line)
            if bm:
                cur_bssid = bm.group(1).strip()
                continue
            sm = re.match(r"^\s*Signal\s*:\s*(\d+)%", line)
            if sm and cur_bssid:
                out[cur_bssid] = (float(sm.group(1)), cur_ssid)
                cur_bssid = None
        return out
