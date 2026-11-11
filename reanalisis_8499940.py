"""Reanálisis de KIC 8499940 (señal 247.53 d): tránsitos individuales y prueba
excluyendo el tramo perturbado BKJD 1270-1310. Uso: python reanalisis_8499940.py"""
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from config import Config  # noqa: E402
from kepler_detector.mast import download  # noqa: E402
from kepler_detector.preprocessing import preprocess  # noqa: E402
from kepler_detector.bls import search  # noqa: E402
from kepler_detector.utils import setup, robust_std  # noqa: E402

KIC = 8499940
P, T0, DUR = 247.5298758491699, 290.8456821359217, 12.1524 / 24
BAD = (1270.0, 1310.0)
OUT = ROOT / 'results' / f'reanalisis_{KIC}'


def depth_at(t, y, epoch, dur):
    dt = t - epoch
    inn = np.abs(dt) < dur / 2
    out = (np.abs(dt) > dur) & (np.abs(dt) < 3 * dur)
    if inn.sum() < 5 or out.sum() < 10:
        return None
    sig = robust_std(y[out])
    d = (np.median(y[out]) - np.mean(y[inn])) * 1e6
    e = sig / np.sqrt(inn.sum()) * 1e6
    return d, e, int(inn.sum()), float(sig * 1e6)


def guided(curve, label):
    cands, _ = search(curve, Config(), known_period=P)
    c = cands[0]
    return {'caso': label, 'periodo': c.period, 'epoca': c.epoch, 'duracion_h': c.duration * 24,
            'profundidad_ppm': c.depth * 1e6, 'snr': c.snr}


def main():
    setup(ROOT)
    OUT.mkdir(parents=True, exist_ok=True)
    print('Descargando/cargando curva de MAST (la primera vez tarda unos minutos)...')
    flat = preprocess(download(ROOT, KIC), Config())
    t, y = flat.time, flat.flux
    global_rms = robust_std(y) * 1e6
    bad = (t > BAD[0]) & (t < BAD[1])
    seg_rms = robust_std(y[bad]) * 1e6 if bad.sum() > 20 else None

    # 1) Tránsitos individuales
    epochs = [T0 + k * P for k in range(-2, 8) if t.min() - 1 < T0 + k * P < t.max() + 1]
    rows = []
    for ep in epochs:
        r = depth_at(t, y, ep, DUR)
        rows.append({'epoca_bkjd': round(ep, 3), 'con_datos': r is not None,
                     'en_tramo_malo': BAD[0] < ep < BAD[1],
                     **({'profundidad_ppm': round(r[0], 1), 'error_ppm': round(r[1], 1),
                         'puntos_dentro': r[2], 'rms_local_ppm': round(r[3], 1)} if r else {})})
    valid = [r for r in rows if r['con_datos']]
    if len(valid) >= 2:
        d = np.array([r['profundidad_ppm'] for r in valid]); e = np.array([r['error_ppm'] for r in valid])
        w = 1 / e ** 2; m = (d * w).sum() / w.sum()
        chi2 = float(((d - m) ** 2 * w).sum())
    else:
        m, chi2 = float('nan'), float('nan')

    fig, axes = plt.subplots(1, len(epochs), figsize=(3.2 * len(epochs), 3.2), sharey=True, squeeze=False)
    for ax, ep, r in zip(axes[0], epochs, rows):
        sel = np.abs(t - ep) < 1.5
        hrs = (t[sel] - ep) * 24
        ax.plot(hrs, (y[sel] - 1) * 1e6, '.', ms=2, color='0.6')
        if sel.sum() > 10:
            bins = np.arange(-36, 37, 1.0)
            idx = np.digitize(hrs, bins)
            bx = [hrs[idx == i].mean() for i in range(1, len(bins)) if (idx == i).sum() > 2]
            by = [((y[sel][idx == i] - 1) * 1e6).mean() for i in range(1, len(bins)) if (idx == i).sum() > 2]
            ax.plot(bx, by, 'o', ms=3, color='C3')
        ax.axvspan(-DUR * 12, DUR * 12, color='C0', alpha=0.15)
        ax.axhline(0, color='k', lw=0.5)
        title = f'BKJD {ep:.1f}'
        if r.get('con_datos'):
            title += f"\n{r['profundidad_ppm']:.0f}±{r['error_ppm']:.0f} ppm"
        else:
            title += '\nsin datos'
        if r['en_tramo_malo']:
            title += ' (tramo malo)'
        ax.set_title(title, fontsize=8)
        ax.set_xlim(-36, 36); ax.set_ylim(-400, 300); ax.set_xlabel('h')
    axes[0][0].set_ylabel('flujo relativo (ppm)')
    fig.suptitle(f'KIC {KIC} — tránsitos individuales (P = {P:.3f} d)')
    fig.tight_layout(); fig.savefig(OUT / 'transitos_individuales.png', dpi=130); plt.close(fig)

    # 2) BLS guiado con y sin el tramo malo
    print('BLS guiado con y sin el tramo 1270-1310...')
    full = guided(flat, 'curva completa')
    masked_curve = flat.subset(~bad)
    sin_malo = guided(masked_curve, f'sin BKJD {BAD[0]:.0f}-{BAD[1]:.0f}')

    # 3) Búsqueda ciega sin el tramo malo
    print('Búsqueda ciega sin el tramo malo (puede tardar)...')
    blind, _ = search(masked_curve, Config())
    blind = sorted(blind, key=lambda c: -c.snr)[:8]
    blind_rows = [{'periodo': round(c.period, 4), 'duracion_h': round(c.duration * 24, 2),
                   'profundidad_ppm': round(c.depth * 1e6, 1), 'snr': round(c.snr, 2)} for c in blind]
    reaparece = any(abs(c.period - P) / P < 0.01 for c in blind)

    resumen = {'kic': KIC, 'rms_global_ppm': round(global_rms, 1),
               'rms_tramo_1270_1310_ppm': round(seg_rms, 1) if seg_rms else None,
               'transitos': rows, 'media_ponderada_ppm': round(float(m), 1),
               'chi2_consistencia': round(chi2, 2), 'gl': len(valid) - 1,
               'bls_guiado': [full, sin_malo],
               'busqueda_ciega_sin_tramo': blind_rows,
               'reaparece_247d_en_ciega': reaparece}
    (OUT / 'resumen.json').write_text(json.dumps(resumen, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(resumen, indent=2, ensure_ascii=False))
    print(f'\nListo. Resultados en {OUT}')


if __name__ == '__main__':
    main()
