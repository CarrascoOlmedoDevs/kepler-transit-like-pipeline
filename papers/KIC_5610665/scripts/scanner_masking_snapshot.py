"""Unmodified masking functions from Sakanet commit 3a90e7d; see repository license."""
import numpy as np
from astropy.timeseries import BoxLeastSquares
from kepler_detector.bls import transit_mask

def _local_bls_power(curve, candidate):
    if len(curve.time) < 50:
        return 0.0
    periods = np.linspace(candidate.period * 0.998, candidate.period * 1.002, 31)
    durations = np.unique(np.clip(candidate.duration * np.array([0.8, 1.0, 1.2]),
                                  1e-4, periods.min() * 0.25))
    try:
        result = BoxLeastSquares(curve.time, curve.flux, curve.error).power(
            periods, durations, objective='likelihood', oversample=5)
        return float(np.nanmax(result.power))
    except (ValueError, FloatingPointError):
        return 0.0


def adaptive_mask(curve, available, candidate, target_effectiveness=0.8,
                  min_width=1.5, max_removed_fraction=0.15):
    """Amplía con límites la ventana hasta suprimir el pico local."""
    residual = curve.subset(available)
    before = max(_local_bls_power(residual, candidate), 1e-12)
    best = None
    for width in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0):
        if width < min_width:
            continue
        remove = transit_mask(curve.time, candidate, width) & available
        removed_fraction = float(remove.sum() / max(1, available.sum()))
        if removed_fraction > max_removed_fraction and best is not None:
            break
        proposed = available & ~remove
        after = _local_bls_power(curve.subset(proposed), candidate)
        effectiveness = float(np.clip(1 - after / before, 0, 1))
        state = (effectiveness, proposed, width, removed_fraction)
        if best is None or effectiveness > best[0]:
            best = state
        if effectiveness >= target_effectiveness:
            break
    return best[1], best[0], best[2], best[3]


