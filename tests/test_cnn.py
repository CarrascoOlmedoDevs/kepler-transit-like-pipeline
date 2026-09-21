import unittest

import numpy as np

from kepler_detector.bls import Candidate, phase
from kepler_detector.cnn import VIEW_SPEC, make_views
from kepler_detector.mast import Curve


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
