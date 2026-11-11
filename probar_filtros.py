"""Prueba los filtros v2.1 (curva invertida + consistencia de profundidades) en dos estrellas:
KIC 5610665 (señal dudosa de 215 d) y KIC 10854555 (planeta confirmado, control)."""
from pathlib import Path

from config import Config
from kepler_detector.scanner import scan
from kepler_detector.triage import assess_candidate

ROOT = Path(__file__).resolve().parent
COLUMNS = ['period_days', 'depth_ppm', 'depth_snr', 'noise_floor_snr', 'inverted_max_snr',
           'depth_chi2', 'depth_chi2_dof', 'depth_chi2_pvalue', 'min_transit_snr']

for kic, what in ((10854555, 'planeta confirmado (control: NO debe salir descartable)'),
                  (5610665, 'señal de 215 d del borrador')):
    print(f'\n=== KIC {kic}: {what} ===', flush=True)
    result = scan(ROOT, kic, Config(), force=True)
    if not len(result):
        print('Sin candidatos por encima del umbral.')
        continue
    for _, row in result.iterrows():
        verdict = assess_candidate(row.to_dict())
        print(f"#{int(row['rank'])} " + ' '.join(f'{name}={row[name]:.4g}' for name in COLUMNS))
        print(f"    alertas: {row['vetting_flags'] or 'ninguna'}")
        print(f"    triage: {verdict['triage_label']} ({verdict['triage_reason']})")
