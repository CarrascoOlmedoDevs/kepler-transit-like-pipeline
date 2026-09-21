import os
import tempfile
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'kepler_matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from .features import binned_views
from .bls import phase


def save(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def lightcurve(curve, path, title):
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(curve.time, curve.flux, '.', ms=1, alpha=0.5, rasterized=True)
    ax.set(xlabel='Tiempo BKJD (TDB)', ylabel='Flujo normalizado', title=title)
    save(fig, path)


def periodogram(result, path):
    fig, ax = plt.subplots(figsize=(12, 4))
    if result is not None:
        ax.plot(result.period, result.power, lw=0.6)
        ax.set_xscale('log')
    ax.set(xlabel='Período (días)', ylabel='BLS Δ log likelihood', title='BLS ciego: primera iteración')
    save(fig, path)


def folded(curve, candidate, row, path, kic):
    views = binned_views(curve, candidate)
    ph = phase(curve.time, candidate.period, candidate.epoch)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    baseline = np.median(curve.flux[np.abs(ph) > candidate.duration])
    for ax, name in zip(axes, ('global', 'local')):
        view = views[name]
        span = abs(view['time']).max()
        selection = abs(ph) <= span
        ax.scatter(ph[selection] * 24, (curve.flux[selection] - baseline) * 1e6,
                   s=2, alpha=0.15, color='slateblue', rasterized=True)
        ax.plot(view['time'] * 24, (view['flux'] - baseline) * 1e6, '.', ms=3, color='black', label='Binned')
        grid = np.linspace(-span, span, 3000)
        ax.plot(grid * 24, -candidate.depth * 1e6 * (abs(grid) < candidate.duration / 2),
                color='tomato', lw=1.5, label='Modelo BLS')
        ax.set(xlabel='Horas desde el centro del tránsito', ylabel='Flujo relativo (ppm)', xlim=(-24 * span, 24 * span))
        ax.legend(fontsize=8)
    rf = row.get('rf_probability', np.nan)
    fig.suptitle(f'KIC {kic} | Period = {candidate.period:.6f} d | Depth = {row["depth_ppm"]:.0f} ppm\n'
                 f'SNR = {row["depth_snr"]:.1f} | RF = {rf:.3f} | Known KOI = {row.get("known_koi", False)}')
    save(fig, path)
