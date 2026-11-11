"""¿Los tránsitos de un candidato caen en las mismas fechas que los de otras estrellas?

Un planeta real tiene sus fechas propias. Un efecto del instrumento (reinicios, cambios térmicos)
produce "tránsitos" a la vez en muchas estrellas. Recorre todos los candidatos analizados,
cuenta cuántas estrellas distintas tienen un evento en cada fecha y compara con lo esperado por azar.

Uso: python coincidencias.py [KIC ...]   (por defecto 6881353 y 9344010)"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'results/seguimiento/coincidencias'
KEPLER_START, KEPLER_END = 120.0, 1592.0
WINDOW = 0.5  # días


def load_candidates():
    rows = []
    sources = list((ROOT / 'results/local_panel').glob('*/workspace/results/KIC_*/candidates.csv'))
    sources += list((ROOT / 'results/seguimiento').glob('KIC_*/candidates.csv'))
    for path in sources:
        try:
            frame = pd.read_csv(path)
        except Exception:  # noqa: BLE001 - CSV vacío
            continue
        if frame.empty or 'epoch_bkjd' not in frame:
            continue
        frame['kic'] = int(path.parent.name.split('_')[1])
        rows.append(frame[['kic', 'period_days', 'epoch_bkjd', 'duration_hours', 'depth_snr', 'number_of_transits']])
    data = pd.concat(rows, ignore_index=True).drop_duplicates(['kic', 'period_days'])
    return data, len({p.parent.name for p in sources})


def events(row):
    p, t0 = float(row.period_days), float(row.epoch_bkjd)
    k0 = int(np.ceil((KEPLER_START - t0) / p))
    return [t0 + k * p for k in range(k0, k0 + 400) if KEPLER_START <= t0 + k * p <= KEPLER_END]


def main():
    targets = [int(a) for a in sys.argv[1:]] or [6881353, 9344010]
    OUT.mkdir(parents=True, exist_ok=True)
    data, stars = load_candidates()
    # Señales de período largo y pocos tránsitos: donde un evento instrumental pesa más.
    long = data[(data.period_days > 100) & (data.number_of_transits <= 8)]
    times, owners = [], []
    for row in long.itertuples():
        for t in events(row):
            times.append(t)
            owners.append(row.kic)
    times, owners = np.array(times), np.array(owners)
    others_stars = long.kic.nunique()
    print(f'{stars} estrellas analizadas; {len(long)} señales de período largo en {others_stars} estrellas; '
          f'{len(times)} eventos', flush=True)
    report = dict(stars=stars, long_signals=int(len(long)), long_stars=int(others_stars), targets={})
    # Esperado por azar: eventos de otras estrellas en una ventana de ±0,5 d.
    density = len(times) / (KEPLER_END - KEPLER_START)
    for kic in targets:
        rows = data[data.kic == kic].sort_values('depth_snr', ascending=False)
        if rows.empty:
            print(f'KIC {kic}: no está entre los analizados')
            continue
        row = rows.iloc[0]
        own = events(row)
        detail = []
        for t in own:
            near = (np.abs(times - t) <= WINDOW) & (owners != kic)
            others = sorted(set(owners[near].tolist()))
            detail.append(dict(bkjd=round(t, 2), other_stars=len(others), kics=others[:15]))
        expected = density * 2 * WINDOW
        observed = float(np.mean([d['other_stars'] for d in detail])) if detail else 0.0
        report['targets'][kic] = dict(period=float(row.period_days), epoch=float(row.epoch_bkjd),
                                      events=detail, expected_per_event=round(expected, 2),
                                      observed_per_event=round(observed, 2))
        print(f'\nKIC {kic}: P={row.period_days:.3f} d')
        for d in detail:
            print(f"  BKJD {d['bkjd']:>8}: {d['other_stars']} otras estrellas con evento a ±{WINDOW} d")
        print(f'  media {observed:.1f} por evento; esperado por azar ≈ {expected:.1f}')
    # Histograma de todas las fechas: picos = posibles eventos instrumentales.
    bins = np.arange(KEPLER_START, KEPLER_END + 1, 1.0)
    counts, _ = np.histogram(times, bins)
    peaks = np.argsort(counts)[::-1][:15]
    report['busiest_days'] = [dict(bkjd=float(bins[i]), events=int(counts[i])) for i in peaks]
    fig, ax = plt.subplots(figsize=(12, 3.6))
    ax.bar(bins[:-1], counts, width=1.0, color='0.55')
    colors = ['#c0392b', '#1f5fbf', '#27ae60', '#8e44ad']
    for color, (kic, info) in zip(colors, report['targets'].items()):
        for d in info['events']:
            ax.axvline(d['bkjd'], color=color, lw=1.2, alpha=0.8)
        ax.plot([], [], color=color, label=f'KIC {kic}')
    ax.axhline(density, color='k', ls='--', lw=0.8, label='media')
    ax.set_xlabel('BKJD (días)')
    ax.set_ylabel('eventos de señales largas por día')
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / 'coincidencias.png', dpi=120)
    plt.close(fig)
    (OUT / 'coincidencias.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('\nDías con más eventos (posibles fallos del instrumento):')
    for d in report['busiest_days'][:8]:
        print(f"  BKJD {d['bkjd']:.0f}: {d['events']} eventos")
    print(f'\nResultados en {OUT}')


if __name__ == '__main__':
    main()
