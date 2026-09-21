import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

from kepler_detector.bls import Candidate, phase
from kepler_detector.cnn import VIEW_SPEC, make_views, predict_candidate
from kepler_detector.catalog import select_discovery_targets
from kepler_detector.mast import Curve
from kepler_detector.schema import RF_FEATURE_COLUMNS
from kepler_detector.utils import atomic_csv


def synthetic(period=3.2, duration=0.16, depth=0.003, days=45):
    time = np.arange(0, days, 0.0204) + 120.0
    epoch = 120.7
    rng = np.random.default_rng(7)
    flux = 1 + rng.normal(0, 0.00015, len(time))
    events = np.floor((time - epoch) / period + 0.5).astype(int)
    primary = np.abs(phase(time, period, epoch)) < duration / 2
    flux -= depth * primary
    flux -= 0.0002 * primary * (events % 2 != 0)
    flux -= 0.0004 * (np.abs(phase(time, period, epoch + period / 2)) < duration / 2)
    curve = Curve(
        time,
        flux,
        np.full(len(time), 0.00015),
        np.zeros(len(time)),
        {"time_format": "BKJD", "time_scale": "TDB"},
    )
    candidate = Candidate(period, duration, epoch, 1000, 1000, depth, 50)
    return curve, candidate


class ExoMinerLiteViewTests(unittest.TestCase):
    def test_predict_candidate_uses_views_and_scalar_transform(self):
        class Model:
            def predict(self, inputs, verbose=0):
                self.inputs = inputs
                return np.asarray([[0.73]], dtype=np.float32)
        curve, candidate = synthetic()
        model = Model()
        predictor = {'model': model, 'median': np.zeros(len(RF_FEATURE_COLUMNS)),
                     'mean': np.zeros(len(RF_FEATURE_COLUMNS)),
                     'std': np.ones(len(RF_FEATURE_COLUMNS))}
        probability = predict_candidate(
            predictor, curve, candidate, {name: 0.0 for name in RF_FEATURE_COLUMNS}
        )
        self.assertAlmostEqual(probability, 0.73, places=5)
        self.assertEqual(model.inputs['global'].shape, (1, VIEW_SPEC['global'], 2))
        self.assertEqual(model.inputs['scalars'].shape, (1, len(RF_FEATURE_COLUMNS)))

    def test_discovery_selection_excludes_known_and_model_stars(self):
        import pandas as pd
        rows = []
        for kepid, noise in [(1, 20), (2, 10), (3, 30), (4, 15)]:
            rows.append(dict(kepid=kepid, kepmag=12, teff=5500, logg=4.4,
                             dutycycle=.95, dataspan=1400, rrmscdpp06p0=noise,
                             nkoi=0, ntce=0, nconfp=0))
        selected = select_discovery_targets(pd.DataFrame(rows), {2}, {4}, limit=2)
        self.assertEqual(selected.kepid.tolist(), [1, 3])

    def test_atomic_csv_retries_brief_windows_lock(self):
        import os
        import pandas as pd
        real_replace = os.replace
        calls = []
        def briefly_locked(source, destination):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(5, 'Acceso denegado')
            return real_replace(source, destination)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'index.csv'
            with patch('kepler_detector.utils.os.replace', side_effect=briefly_locked), \
                    patch('kepler_detector.utils.time.sleep'):
                atomic_csv(pd.DataFrame({'value': [1]}), target)
            self.assertEqual(target.read_text(encoding='utf-8'), 'value\n1\n')
            self.assertEqual(len(calls), 3)

    def test_views_have_expected_shapes_and_are_finite(self):
        curve, candidate = synthetic()
        views = make_views(curve, candidate)
        self.assertEqual(set(views), set(VIEW_SPEC))
        for name, bins in VIEW_SPEC.items():
            self.assertEqual(views[name].shape, (bins, 2))
            self.assertEqual(views[name].dtype, np.float32)
            self.assertTrue(np.isfinite(views[name]).all())
            self.assertTrue(((views[name][:, 1] >= 0) & (views[name][:, 1] <= 1)).all())

    def test_odd_even_views_are_not_identical_when_depths_differ(self):
        curve, candidate = synthetic()
        views = make_views(curve, candidate)
        self.assertGreater(
            np.max(np.abs(views["odd"][:, 0] - views["even"][:, 0])),
            1e-3,
        )


if __name__ == "__main__":
    unittest.main()
