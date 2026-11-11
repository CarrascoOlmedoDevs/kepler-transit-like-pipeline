"""Busca en los datos de TESS los tránsitos previstos de un candidato de Kepler.

Uso: python tess_comprobar.py  (por defecto KIC 6881353, P=233.9408 d)
Mide la profundidad en cada tránsito previsto que cae en un sector observado y la combina.
Con 60 ppm y una estrella de Tmag ≈ 11, TESS sólo puede poner un límite superior."""
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
warnings.filterwarnings('ignore')
RA, DEC = 296.575566905, 42.346715796
T0_BJD, PERIOD, PERIOD_ERR, DUR_H, DEPTH_PPM = 2455182.935, 233.9408, 0.01, 13.45, 60.4
OUT = ROOT / 'results/seguimiento/KIC_6881353/tess'


def robust_std(x):
    x = x[np.isfinite(x)]
    return 1.4826 * np.median(np.abs(x - np.median(x))) if len(x) else np.nan


def main():
    import lightkurve as lk
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    OUT.mkdir(parents=True, exist_ok=True)
    coord = SkyCoord(RA * u.deg, DEC * u.deg)
    print('Buscando curvas de TESS (SPOC, TESS-SPOC y QLP)...', flush=True)
    found = lk.search_lightcurve(coord, radius=10 * u.arcsec, mission='TESS')
    print(found, flush=True)
    if not len(found):
        print('TESS no tiene curvas de esta estrella.')
        return
    dur = DUR_H / 24
    events, plots = [], []
    seen = set()
    for index in range(len(found)):
        row = found[index]
        author, sector = str(row.author[0]), int(row.mission[0].split()[-1])
        try:
            lc = row.download(download_dir=str(ROOT / 'cache/mast'), quality_bitmask='default')
        except Exception as error:  # noqa: BLE001
            print(f'  S{sector} {author}: no se pudo descargar ({error})')
            continue
        if lc is None:
            continue
        lc = lc.remove_nans().normalize()
        t = lc.time.value + 2457000.0  # BTJD -> BJD
        f = np.asarray(lc.flux.value, dtype=float)
        n = np.round((t.mean() - T0_BJD) / PERIOD)
        for k in (n - 1, n, n + 1):
            mid = T0_BJD + k * PERIOD
            if not (t.min() - dur < mid < t.max() + dur):
                continue
            key = (sector, author, int(k))
            if key in seen:
                continue
            seen.add(key)
            dt = t - mid
            near = np.abs(dt) < 2.5
            inside = np.abs(dt) < dur / 2
            outside = near & (np.abs(dt) > 0.75 * dur)
            if inside.sum() < 3 or outside.sum() < 10:
                print(f'  S{sector} {author}: tránsito previsto BJD {mid:.2f} cae en un hueco')
                continue
            # Tendencia local: recta fuera del tránsito.
            fit = np.polyfit(dt[outside], f[outside], 1)
            resid = f - np.polyval(fit, dt)
            sigma = robust_std(resid[outside])
            depth = -np.mean(resid[inside]) * 1e6
            error = sigma / np.sqrt(inside.sum()) * 1e6
            # Ruido rojo: misma medida en ventanas sin tránsito del mismo sector.
            nulls = []
            for c in np.arange(t.min() + 2.5, t.max() - 2.5, dur):
                if abs(c - mid) < 2 * dur:
                    continue
                d2 = t - c
                i2, o2 = np.abs(d2) < dur / 2, (np.abs(d2) < 2.5) & (np.abs(d2) > 0.75 * dur)
                if i2.sum() >= 3 and o2.sum() >= 10:
                    fit2 = np.polyfit(d2[o2], f[o2], 1)
                    nulls.append(-np.mean((f - np.polyval(fit2, d2))[i2]) * 1e6)
            error = max(error, float(np.std(nulls))) if len(nulls) > 5 else error
            events.append(dict(sector=sector, author=author, epoch=int(k), bjd=round(mid, 3),
                               timing_error_d=round(abs(k) * PERIOD_ERR, 2), points=int(inside.sum()),
                               depth_ppm=round(depth, 1), error_ppm=round(error, 1)))
            plots.append((sector, author, dt[near] * 24, resid[near] * 1e6))
            print(f'  S{sector} {author}: BJD {mid:.2f} → {depth:+.0f} ± {error:.0f} ppm')
    result = dict(predicted_depth_ppm=DEPTH_PPM, events=events)
    if events:
        d = np.array([e['depth_ppm'] for e in events])
        e = np.array([e['error_ppm'] for e in events])
        w = 1 / e ** 2
        mean, err = float((d * w).sum() / w.sum()), float(1 / np.sqrt(w.sum()))
        result.update(combined_depth_ppm=round(mean, 1), combined_error_ppm=round(err, 1),
                      upper_limit_3sigma_ppm=round(mean + 3 * err, 1),
                      snr_expected=round(DEPTH_PPM / err, 2))
        fig, axes = plt.subplots(1, len(plots), figsize=(4 * len(plots), 3.4), sharey=True, squeeze=False)
        for ax, (sector, author, x, y) in zip(axes[0], plots):
            ax.plot(x, y, '.', ms=2, color='0.6')
            bins = np.arange(-60, 60.1, 2)
            idx = np.digitize(x, bins)
            bx = [x[idx == i].mean() for i in range(1, len(bins)) if (idx == i).sum() > 2]
            by = [y[idx == i].mean() for i in range(1, len(bins)) if (idx == i).sum() > 2]
            ax.plot(bx, by, 'o', ms=3, color='#1f5fbf')
            ax.axvspan(-DUR_H / 2, DUR_H / 2, color='#e8a33d', alpha=0.2)
            ax.axhline(-DEPTH_PPM, color='#c0392b', lw=1, ls='--')
            ax.set_title(f'TESS S{sector} · {author}', fontsize=9)
            ax.set_xlabel('hours from predicted mid-transit')
            ax.set_ylim(-1000, 1000)
        axes[0][0].set_ylabel('relative flux (ppm)')
        fig.tight_layout()
        fig.savefig(OUT / 'tess_transitos_previstos.png', dpi=120)
        plt.close(fig)
    (OUT / 'tess.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('\n=== Resumen ===')
    if events:
        print(f"  profundidad combinada {result['combined_depth_ppm']} ± {result['combined_error_ppm']} ppm "
              f"(esperada {DEPTH_PPM}); SNR esperado en TESS ≈ {result['snr_expected']}")
        print(f"  límite superior (3σ): {result['upper_limit_3sigma_ppm']} ppm")
    else:
        print('  Ningún tránsito previsto cae en datos de TESS.')
    print(f'Resultados en {OUT}')


if __name__ == '__main__':
    sys.exit(main())
