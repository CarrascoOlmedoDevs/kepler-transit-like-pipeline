"""Ayudantes sin TensorFlow: guardan entradas CNN y el coordinador las puntúa."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from kepler_detector import cnn
from local_panel.cnn_scorer import CNNScorer


class FakeModel:
    def predict(self, batch, verbose=0):
        assert set(batch) == set(cnn.VIEW_SPEC) | {'scalars'}
        return np.array([[0.73]])


def predictor():
    n = len(cnn.RF_FEATURE_COLUMNS)
    return dict(model=FakeModel(), median=np.zeros(n), mean=np.zeros(n), std=np.ones(n))


def inputs():
    data = {name: np.zeros((spec if isinstance(spec, int) else spec[0], 2)) for name, spec in cnn.VIEW_SPEC.items()}
    data['scalars'] = np.arange(len(cnn.RF_FEATURE_COLUMNS), dtype=float)
    return data


class CNNScorerTests(unittest.TestCase):
    def test_pending_inputs_roundtrip_without_pickle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cnn_inputs.npz'
            cnn.save_pending_inputs(path, {1: inputs(), 3: inputs()})
            loaded = cnn.load_pending_inputs(path)
            self.assertEqual(sorted(loaded), [1, 3])
            self.assertEqual(set(loaded[1]), set(cnn.VIEW_SPEC) | {'scalars'})

    def test_scorer_fills_csv_and_removes_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with (folder / 'candidates.csv').open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, ['rank', 'detection_order', 'cnn_probability', 'cnn_available'])
                writer.writeheader()
                writer.writerow(dict(rank=1, detection_order=2, cnn_probability='', cnn_available='False'))
                writer.writerow(dict(rank=2, detection_order=1, cnn_probability='0.2', cnn_available='True'))
            cnn.save_pending_inputs(folder / 'cnn_inputs.npz', {2: inputs()})
            scorer = CNNScorer(folder)
            with patch('kepler_detector.cnn.load_predictor', return_value=predictor()):
                self.assertEqual(scorer.score(folder), 1)
            rows = list(csv.DictReader((folder / 'candidates.csv').open(encoding='utf-8')))
            self.assertAlmostEqual(float(rows[0]['cnn_probability']), 0.73)
            self.assertEqual(rows[0]['cnn_available'], 'True')
            self.assertEqual(rows[1]['cnn_probability'], '0.2')
            self.assertFalse((folder / 'cnn_inputs.npz').exists())

    def test_scorer_without_tensorflow_keeps_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'candidates.csv').write_text('rank,detection_order,cnn_probability\n1,1,\n', encoding='utf-8')
            cnn.save_pending_inputs(folder / 'cnn_inputs.npz', {1: inputs()})
            with patch('kepler_detector.cnn.load_predictor', side_effect=RuntimeError('sin TensorFlow')):
                self.assertIsNone(CNNScorer(folder).score(folder))
            self.assertTrue((folder / 'cnn_inputs.npz').exists())


if __name__ == '__main__':
    unittest.main()
