"""Parámetros estelares actualizados de un KIC: Gaia–Kepler (Berger et al. 2020) y Gaia DR3 FLAME.
Uso: python parametros_estrella.py [KIC]  → results/seguimiento/KIC_<kic>/parametros_estrella.json"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
kic = int(sys.argv[1]) if len(sys.argv) > 1 else 6881353
out = {}
try:
    from astroquery.vizier import Vizier
    v = Vizier(columns=['**'], row_limit=5)
    for name, cat in (('berger2020_estrellas', 'J/AJ/159/280/table2'), ('berger2020_entrada', 'J/AJ/159/280/table1')):
        res = v.query_constraints(catalog=cat, KIC=str(kic))
        out[name] = [{k: (None if hasattr(r[k], 'mask') and r[k] is not None and str(r[k]) == '--' else
                          (r[k].item() if hasattr(r[k], 'item') else str(r[k]))) for k in r.colnames}
                     for t in res for r in t]
except Exception as error:  # noqa: BLE001
    out['berger2020_error'] = str(error)
try:
    from astroquery.gaia import Gaia
    q = """SELECT g.source_id, g.parallax, g.parallax_error, g.ruwe, a.radius_flame, a.radius_gspphot,
           a.mass_flame, a.lum_flame, a.teff_gspphot, a.logg_gspphot, a.age_flame
           FROM gaiadr3.gaia_source AS g JOIN gaiadr3.astrophysical_parameters AS a USING (source_id)
           WHERE g.source_id = 2077172048372984192"""
    t = Gaia.launch_job_async(q).get_results()
    out['gaia_dr3'] = [{k: (None if str(r[k]) == '--' else float(r[k]) if k != 'source_id' else int(r[k]))
                        for k in t.colnames} for r in t]
except Exception as error:  # noqa: BLE001
    out['gaia_error'] = str(error)
path = ROOT / f'results/seguimiento/KIC_{kic}/parametros_estrella.json'
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(out, indent=2, default=str), encoding='utf-8')
print(json.dumps(out, indent=2, default=str))
print(f'\nGuardado en {path}')
