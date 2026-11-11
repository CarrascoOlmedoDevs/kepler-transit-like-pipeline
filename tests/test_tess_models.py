"""Modelo RF propio de TESS: etiquetas TFOPWG, rutas separadas y triage."""
import unittest
from pathlib import Path
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd

from config import Config
from test_pipeline import temporary_project, synthetic
from kepler_detector import ml_paths
from kepler_detector.builder import build, tess_selection
from kepler_detector.ml import train, evaluate, load_model, model_path
from kepler_detector.triage import assess_candidate
from kepler_detector.utils import setup


def toi_frame():
    rows = []
    for i in range(12):
        rows.append(dict(tic=1000 + i, toi=f'{100 + i}.01', disposition=['CP', 'KP', 'FP', 'FA'][i % 4], period=3.2))
    rows += [dict(tic=2000, toi='900.01', disposition='PC', period=3.2),
             dict(tic=2001, toi='901.01', disposition='FP', period=float('nan'))]
    return pd.DataFrame(rows)


class TessModelTests(unittest.TestCase):
    def test_selection_uses_final_tfopwg_dispositions_only(self):
        chosen, stars = tess_selection(toi_frame(), Config())
        self.assertEqual(len(chosen), 12)
        self.assertNotIn(2000, set(chosen.kepid))       # PC sin veredicto
        self.assertNotIn(2001, set(chosen.kepid))       # sin período
        self.assertEqual(chosen.label.sum(), 6)
        self.assertTrue(chosen.kepoi_name.str.startswith('TOI-').all())
        self.assertEqual(sorted(stars), sorted(chosen.kepid.unique()))

    def test_build_and_train_write_tess_paths_only(self):
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            paths = ml_paths.get('tess')
            curve, _ = synthetic()
            toi_frame().to_csv(root / 'data/tess_toi.csv', index=False)
            with patch('kepler_detector.builder.download', return_value=curve) as download:
                counts = build(root, Config(), per_class=6, mission='tess')
            self.assertEqual(counts, {0: 6, 1: 6})
            self.assertEqual(download.call_args.kwargs.get('mission'), 'tess')
            self.assertTrue((root / paths.dataset).exists())
            self.assertFalse((root / 'data/training_features.csv').exists())
            frame = pd.read_csv(root / paths.dataset)
            # Variables distintas por clase para que el RF tenga algo que aprender.
            frame.loc[frame.label == 1, 'depth_snr'] += 50
            frame.to_csv(root / paths.dataset, index=False)
            metrics = train(root, mission='tess')
            self.assertEqual(metrics['mission'], 'tess')
            self.assertTrue((root / paths.model).exists())
            self.assertFalse((root / 'models/kepler_rf.joblib').exists())
            self.assertEqual(joblib.load(root / paths.model)['mission'], 'tess')
            self.assertEqual(model_path(root, 'tess'), root / paths.model)
            self.assertEqual(evaluate(root, mission='tess')['mission'], 'tess')

    def test_tess_falls_back_to_kepler_model(self):
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            self.assertEqual(model_path(root, 'tess'), root / 'models/kepler_rf.joblib')
            self.assertEqual(model_path(root, 'k2'), root / 'models/kepler_rf.joblib')

    def test_triage_with_own_rf_and_no_cnn(self):
        row = dict(rf_probability=0.85, cnn_probability=float('nan'), priority_score=60, depth_snr=15,
                   number_of_transits=6, transit_depth_consistency=0.9, vetting_flags='',
                   model_calibrated='True', cnn_calibrated='False')
        self.assertEqual(assess_candidate(row)['triage_label'], 'prioritario')
        self.assertEqual(assess_candidate(dict(row, rf_probability=0.7))['triage_label'], 'revisar')
        self.assertEqual(assess_candidate(dict(row, model_calibrated='False'))['triage_label'], 'revisar')


if __name__ == '__main__':
    unittest.main()
