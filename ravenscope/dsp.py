"""
Signal processing for WiFi RSSI motion sensing.

This is a faithful, stdlib-only implementation of the documented RuView
"$0 / Any WiFi" tier pipeline. RuView's full CSI stack uses Hampel outlier
rejection, a self-calibrating coherence gate, and motion-band power on phase;
on a commodity laptop we only have RSSI (one scalar per access point, not the
full 56-subcarrier complex CSI), so we apply the same *ideas* at the resolution
RSSI allows:

    raw RSSI  ->  Hampel despike  ->  EMA baseline / detrend
              ->  motion energy (windowed variance of the detrended signal)
              ->  coherence gate (reject links that are just noisy, not moving)
              ->  adaptive z-score anomaly score

Everything here is intentionally numpy-free so the whole app installs with
nothing but the Python standard library.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional


def _median(values) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def _mean(values) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values, mean: Optional[float] = None) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    m = _mean(values) if mean is None else mean
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


class Hampel:
    """Rolling Hampel filter: replaces outliers more than `k` MADs from the
    local median with the median. This is how RuView rejects the sudden RSSI
    spikes that come from rate adaptation / beacon jitter rather than motion."""

    def __init__(self, window: int = 7, k: float = 3.0):
        self.window = max(3, window | 1)  # force odd
        self.k = k
        self.buf: Deque[float] = deque(maxlen=self.window)

    def push(self, x: float) -> float:
        self.buf.append(x)
        if len(self.buf) < 3:
            return x
        med = _median(self.buf)
        mad = _median([abs(v - med) for v in self.buf]) * 1.4826 + 1e-9
        if abs(x - med) > self.k * mad:
            return med
        return x


@dataclass
class LinkStats:
    """Per-access-point (per-link) running state and derived motion metrics."""

    bssid: str
    ssid: str = ""
    last_rssi: float = 0.0
    baseline: float = 0.0          # slow EMA = the "still room" level
    energy: float = 0.0            # current motion energy (0..1-ish, scaled)
    raw_energy: float = 0.0        # unscaled windowed variance of detrend
    coherence: float = 0.0         # how trustworthy this link is right now
    samples: int = 0
    reliable: bool = False


class LinkProcessor:
    """Turns a stream of RSSI readings for ONE access point into a motion
    energy value, following the RuView RSSI-tier pipeline."""

    def __init__(
        self,
        bssid: str,
        ssid: str = "",
        baseline_alpha: float = 0.02,
        motion_window: int = 16,
        hampel_window: int = 7,
    ):
        self.stats = LinkStats(bssid=bssid, ssid=ssid)
        self.hampel = Hampel(window=hampel_window)
        self.baseline_alpha = baseline_alpha
        self.detrended: Deque[float] = deque(maxlen=motion_window)
        # calibration noise floor for this link (set during calibrate())
        self.noise_floor: float = 0.0
        self.noise_cal: Deque[float] = deque(maxlen=256)
        self._init = False

    def push(self, rssi: float, calibrating: bool) -> LinkStats:
        s = self.stats
        s.last_rssi = rssi
        s.samples += 1

        clean = self.hampel.push(rssi)

        if not self._init:
            s.baseline = clean
            self._init = True
        else:
            a = self.baseline_alpha
            s.baseline = (1 - a) * s.baseline + a * clean

        residual = clean - s.baseline
        self.detrended.append(residual)

        # motion energy = RMS of the detrended residual over the window.
        # A still room sits near the noise floor; a moving body inflates it.
        if len(self.detrended) >= 4:
            rms = math.sqrt(_mean([d * d for d in self.detrended]))
        else:
            rms = abs(residual)
        s.raw_energy = rms

        if calibrating:
            self.noise_cal.append(rms)
            # provisional noise floor while calibrating
            self.noise_floor = _median(self.noise_cal) if self.noise_cal else rms

        # Coherence gate: a link is trustworthy if it actually moves above its
        # own calibrated noise floor. Dead/echoey links contribute ~nothing.
        floor = max(self.noise_floor, 0.3)
        excess = max(0.0, rms - floor)
        # squash into 0..~1 with a soft knee at ~3x the floor
        s.energy = 1.0 - math.exp(-excess / (1.5 * floor))
        s.coherence = min(1.0, excess / (3.0 * floor)) if floor > 0 else 0.0
        s.reliable = s.samples > 8 and floor < 8.0  # absurd noise => unreliable
        return s

    def finalize_calibration(self) -> None:
        if self.noise_cal:
            # use a high-ish percentile so normal jitter doesn't read as motion
            vals = sorted(self.noise_cal)
            idx = min(len(vals) - 1, int(0.75 * len(vals)))
            self.noise_floor = max(vals[idx], 0.3)


class AnomalyZScore:
    """Adaptive z-score on the fused motion energy. Flags bursts that are far
    above the recent rolling distribution (e.g. a sudden entry, a fall-like
    lunge). Mirrors RuView's debounce + cooldown so one spike isn't an alarm."""

    def __init__(self, window: int = 240, k: float = 4.0, debounce: int = 3,
                 cooldown: int = 30):
        self.buf: Deque[float] = deque(maxlen=window)
        self.k = k
        self.debounce = debounce
        self.cooldown = cooldown
        self._hot = 0
        self._cool = 0

    def push(self, energy: float):
        """Returns (z_score, is_anomaly)."""
        if self._cool > 0:
            self._cool -= 1
        z = 0.0
        if len(self.buf) >= 20:
            m = _mean(self.buf)
            sd = _stdev(self.buf, m)
            if sd > 1e-6:
                z = (energy - m) / sd
        self.buf.append(energy)

        is_anom = False
        if z >= self.k and self._cool == 0:
            self._hot += 1
            if self._hot >= self.debounce:
                is_anom = True
                self._hot = 0
                self._cool = self.cooldown
        else:
            self._hot = max(0, self._hot - 1)
        return z, is_anom
