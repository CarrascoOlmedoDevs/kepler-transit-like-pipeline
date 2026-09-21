"""Smoke test offline; artefactos explícitamente sintéticos en results/smoke/.

La prueba real se ejecuta con scan --quarter 5 --no-model --period-max 40.
"""
import json
from pathlib import Path
from config import Config
from tests.test_pipeline import synthetic
from kepler_detector.utils import setup
from kepler_detector.mast import save_curve
from kepler_detector.scanner import scan


def main():
    root = setup(Path(__file__).resolve().parent / 'results/smoke')
    curve, injected = synthetic()
    save_curve(curve, root / 'cache/lightcurves/KIC_00000001.npz')
    config = Config(period_max=10, coarse_points=1500, refine_peaks=4, max_planets=2,
                    durations=(0.1, 0.16, 0.25))
    result = scan(root, 1, config, force=True, no_model=True)
    assert len(result) >= 1, 'BLS no detectó la señal inyectada'
    assert abs(result.iloc[0].period_days - injected.period) < 0.005
    print(json.dumps({'smoke': 'PASS', 'data': 'SYNTHETIC, offline',
                      'period_injected': injected.period, 'period_recovered': result.iloc[0].period_days,
                      'candidates': len(result), 'output': str(root)}, indent=2))


if __name__ == '__main__':
    main()
