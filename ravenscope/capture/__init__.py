"""Capture backends + autodetection."""

from __future__ import annotations

import sys
from typing import Optional

from .base import CaptureBackend, Sample
from .linux import LinuxBackend
from .macos import MacOSBackend
from .ping import PingBackend
from .simulate import SimulateBackend
from .windows import WindowsBackend

__all__ = [
    "CaptureBackend", "Sample", "select_backend",
    "LinuxBackend", "MacOSBackend", "WindowsBackend", "PingBackend",
    "SimulateBackend",
]


def _native_backend(iface: Optional[str], allow_sudo: bool) -> Optional[CaptureBackend]:
    plat = sys.platform
    if plat.startswith("linux"):
        b = LinuxBackend(iface=iface)
    elif plat == "darwin":
        b = MacOSBackend(iface=iface, allow_sudo=allow_sudo)
    elif plat == "win32":
        b = WindowsBackend()
    else:
        return None
    return b if b.available() else None


def select_backend(kind: str = "auto", iface: Optional[str] = None,
                   allow_sudo: bool = False, ping_host: Optional[str] = None
                   ) -> CaptureBackend:
    """Pick a capture backend.

    kind: auto | linux | macos | windows | ping | simulate
    'auto' tries the OS-native RSSI backend, then falls back to the universal
    ping-jitter proxy so SOMETHING always works.
    """
    kind = (kind or "auto").lower()
    if kind == "simulate":
        return SimulateBackend()
    if kind == "ping":
        b = PingBackend(host=ping_host)
        if not b.available():
            raise RuntimeError("ping backend unavailable: no default gateway")
        return b
    if kind in ("linux", "macos", "windows"):
        cls = {"linux": LinuxBackend, "macos": MacOSBackend,
               "windows": WindowsBackend}[kind]
        b = cls(iface=iface) if kind != "windows" else cls()
        if not b.available():
            raise RuntimeError(f"{kind} backend unavailable on this machine")
        return b

    # auto
    native = _native_backend(iface, allow_sudo)
    if native is not None:
        return native
    ping = PingBackend(host=ping_host)
    if ping.available():
        return ping
    raise RuntimeError(
        "No capture backend available. Try --backend ping, or --simulate to "
        "preview the dashboard.")
