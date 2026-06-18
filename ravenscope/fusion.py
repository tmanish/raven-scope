"""Multi-AP fusion and coarse bearing.

RuView's README calls out using "your neighbors' routers as free radar
illuminators." We lean into that: every visible access point is an independent
illuminator. We fuse their per-link motion energies into one robust room-energy
value (coherence-weighted, outlier-trimmed), and — when several APs are visible
— estimate a *coarse* sector for where the motion is strongest.

The bearing is intentionally labelled coarse/experimental: with RSSI and APs at
unknown physical positions we can only say "more perturbation is showing up on
this subset of links," mapped to left / center / right style sectors by the
order APs were discovered. It is a genuine relative cue, not a calibrated angle.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .dsp import LinkStats


def fuse_energy(links: Dict[str, LinkStats]) -> Tuple[float, int]:
    """Fuse per-link motion energy into one room-energy value (~0..1).

    Motion perturbs the link(s) whose path a moving body actually crosses — not
    every visible access point. Averaging across all links therefore lets quiet
    or *stale* illuminators drown out the one link that is moving. This bites
    hardest on macOS, where the connected AP is read live every call but
    neighbouring APs come from the system scan and only refresh every ~30-60s,
    so between scans they report zero motion. We instead fuse toward the
    most-perturbed trustworthy illuminator, so quiet/frozen links can never pull
    the result down; they still contribute to the radar and to direction.

    Returns (fused_energy in ~0..1, n_reliable_links).
    """
    eligible = [s for s in links.values() if s.samples >= 5]
    if not eligible:
        return 0.0, 0

    # Prefer coherence-gated links; fall back to any with enough samples.
    pool = [s for s in eligible if s.reliable] or eligible
    # The strongest reliable illuminator drives detection. A quiet/stale link
    # has ~0 energy and simply loses the max, so it cannot dilute a real signal.
    fused = max(s.energy for s in pool)

    n_reliable = sum(1 for s in links.values() if s.reliable)
    return fused, n_reliable


def sector_energies(links: Dict[str, LinkStats], order: List[str],
                    sectors: int = 3) -> List[float]:
    """Split discovered APs into N ordered sectors and sum motion energy in
    each. With the default 3 sectors this reads as left / center / right."""
    out = [0.0] * sectors
    counts = [0] * sectors
    n = len(order)
    if n == 0:
        return out
    for i, bssid in enumerate(order):
        st = links.get(bssid)
        if not st:
            continue
        sec = min(sectors - 1, int(i * sectors / max(1, n)))
        out[sec] += st.energy
        counts[sec] += 1
    return [out[i] / counts[i] if counts[i] else 0.0 for i in range(sectors)]


def dominant_sector(sector_e: List[float]) -> Tuple[int, float]:
    if not sector_e:
        return -1, 0.0
    idx = max(range(len(sector_e)), key=lambda i: sector_e[i])
    total = sum(sector_e) or 1.0
    confidence = sector_e[idx] / total
    return idx, confidence
