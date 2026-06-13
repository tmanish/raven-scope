"""Room state machine.

Turns the continuous fused motion-energy stream into a small set of meaningful,
stable states with hysteresis and debouncing, so the UI and any automations
react to real transitions instead of flickering on every sample.

States:
  EMPTY   - energy at/near the calibrated floor for a sustained period
  STILL   - low but non-floor energy: someone present but not moving (sitting)
  MOTION  - clear movement in the space
  ACTIVE  - vigorous / continuous movement

Thresholds are set relative to the calibrated quiet baseline (energy ~0 when
still), so the same code adapts to any room after the 30 s calibration.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple

EMPTY = "EMPTY"
STILL = "STILL"
MOTION = "MOTION"
ACTIVE = "ACTIVE"

_ORDER = {EMPTY: 0, STILL: 1, MOTION: 2, ACTIVE: 3}


@dataclass
class StateConfig:
    still_enter: float = 0.10   # energy above this => at least STILL
    motion_enter: float = 0.28  # => MOTION
    active_enter: float = 0.62  # => ACTIVE
    hysteresis: float = 0.06    # must drop this far below to step down
    debounce: int = 3           # consecutive samples required to change
    empty_hold: float = 6.0     # seconds of floor energy before declaring EMPTY


class RoomState:
    def __init__(self, cfg: Optional[StateConfig] = None):
        self.cfg = cfg or StateConfig()
        self.state = EMPTY
        self.since = time.time()
        self._pending: Optional[str] = None
        self._pending_count = 0
        self._below_floor_since: Optional[float] = None

    def _target(self, energy: float) -> str:
        c = self.cfg
        cur = _ORDER[self.state]

        def lvl(e):
            if e >= c.active_enter:
                return ACTIVE
            if e >= c.motion_enter:
                return MOTION
            if e >= c.still_enter:
                return STILL
            return EMPTY

        t = lvl(energy)
        # hysteresis: to step DOWN a level, require energy below the lower
        # level's enter threshold minus the hysteresis band.
        if _ORDER[t] < cur:
            thresholds = {ACTIVE: c.active_enter, MOTION: c.motion_enter,
                          STILL: c.still_enter, EMPTY: 0.0}
            step_down_to = self.state
            for s in (ACTIVE, MOTION, STILL, EMPTY):
                if _ORDER[s] < cur and energy < thresholds[s] - c.hysteresis:
                    step_down_to = s
            return step_down_to
        return t

    def update(self, energy: float, now: Optional[float] = None
               ) -> Tuple[str, Optional[str]]:
        """Feed one fused energy value. Returns (state, transition_event|None)."""
        now = now or time.time()
        target = self._target(energy)

        # EMPTY requires a sustained quiet hold to avoid declaring "empty" the
        # instant someone pauses.
        if target == EMPTY and self.state != EMPTY:
            if self._below_floor_since is None:
                self._below_floor_since = now
            if now - self._below_floor_since < self.cfg.empty_hold:
                target = STILL  # hold occupancy until the quiet period elapses
        else:
            self._below_floor_since = None

        if target == self.state:
            self._pending = None
            self._pending_count = 0
            return self.state, None

        if target == self._pending:
            self._pending_count += 1
        else:
            self._pending = target
            self._pending_count = 1

        if self._pending_count >= self.cfg.debounce:
            prev = self.state
            self.state = target
            self.since = now
            self._pending = None
            self._pending_count = 0
            return self.state, f"{prev}->{self.state}"
        return self.state, None

    @property
    def dwell(self) -> float:
        return time.time() - self.since
