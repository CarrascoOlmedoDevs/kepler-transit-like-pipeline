"""Reanaliza una estrella con el pipeline actual (filtros v2.2, RF y CNN) y resume el veredicto.
Uso: python reanalizar_estrella.py <KIC>"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from config import Config  # noqa: E402
from kepler_detector.scanner import scan  # noqa: E402
from kepler_detector.triage import assess_candidate  # noqa: E402
from kepler_detector.utils import setup  # noqa: E402


def fmt(value, digits=3):
    try:
        return f'{float(value):.{digits}g}'
    except (TypeError, ValueError):
        return str(value)


def main():
    kic = int(sys.argv[1]) if len(sys.argv) > 1 else 6881353
    setup(ROOT)
    print(f'Reanalizando KIC {kic} (puede tardar unos minutos)...', flush=True)
    result = scan(ROOT, kic, Config(), force=True)
    if not len(result):
        print('Sin señales por encima del umbral.')
        return
    for _, row in result.head(5).iterrows():
        verdict = assess_candidate(row.to_dict())
        print(f"\n#{int(row['rank'])}  P={fmt(row['period_days'], 6)} d  prof={fmt(row['depth_ppm'])} ppm  SNR={fmt(row['depth_snr'])}  tránsitos={int(row['number_of_transits'])}")
        print(f"    RF={fmt(row['rf_probability'])}  CNN={fmt(row['cnn_probability'])}  "
              f"prioridad={fmt(row['priority_score'])}  suelo de ruido={fmt(row.get('noise_floor_snr'))}")
        print(f"    alertas: {row['vetting_flags'] or 'ninguna'}")
        print(f"    triage: {verdict['triage_label']} ({verdict['triage_reason']})")


if __name__ == '__main__':
    main()
