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
    """Coherence-weighted, trimmed fusion of per-link motion energy.

    Returns (fused_energy in ~0..1, n_reliable_links).
    """
    contribs: List[Tuple[float, float]] = []  # (energy, weight)
    for s in links.values():
        if s.samples < 5:
            continue
        w = 0.25 + 0.75 * s.coherence  # trustworthy links count more
        contribs.append((s.energy, w))
    if not contribs:
        return 0.0, 0

    # trim the single strongest contributor a touch to resist one flaky AP
    contribs.sort(key=lambda c: c[0])
    if len(contribs) >= 4:
        contribs = contribs[:-1]  # drop the max

    num = sum(e * w for e, w in contribs)
    den = sum(w for _, w in contribs)
    fused = num / den if den else 0.0
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
