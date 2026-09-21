import numpy as np
from scipy.ndimage import median_filter
from .mast import Curve
from .utils import robust_std


def preprocess(curve, config):
    valid = (np.isfinite(curve.time) & np.isfinite(curve.flux) & np.isfinite(curve.error)
             & (curve.error > 0))
    clean = curve.subset(valid)
    order = np.argsort(clean.time)
    clean = clean.subset(order)
    if len(clean.time) < 50 or np.ptp(clean.time) <= 0:
        raise ValueError('Menos de 50 puntos válidos o tiempos degenerados')
    cadence = np.median(np.diff(clean.time))
    breaks = np.flatnonzero((np.diff(clean.segment) != 0) |
                           (np.diff(clean.time) > max(0.5, cadence * 10))) + 1
    flux, error = clean.flux.copy(), clean.error.copy()
    keep = np.ones(len(flux), dtype=bool)
    for indices in np.split(np.arange(len(flux)), breaks):
        if len(indices) < 15:
            keep[indices] = False
            continue
        y = flux[indices]
        dt = np.median(np.diff(clean.time[indices]))
        window = max(5, int(config.flatten_window / dt) | 1)
        window = min(window, len(indices) if len(indices) % 2 else len(indices) - 1)
        trend = median_filter(y, size=window, mode='nearest')
        # Mediana móvil ancha: no ajusta los fondos de tránsitos aislados.
        trend = np.maximum(trend, 1e-8)
        flat = y / trend
        noise = robust_std(np.diff(flat)) / np.sqrt(2)
        # Sólo clipping superior: los eclipses profundos también son señales.
        keep[indices] = flat < 1 + config.sigma_clip * noise
        flux[indices] = flat
        error[indices] = error[indices] / trend
    output = Curve(clean.time, flux, error, clean.segment, clean.metadata.copy()).subset(keep)
    if len(output.time) < 50:
        raise ValueError('Información insuficiente después del detrending')
    output.metadata['preprocessing'] = config.to_dict()
    return output
