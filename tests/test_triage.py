import unittest

import numpy as np

from config import Config
from kepler_detector.mast import Curve
from kepler_detector.preprocessing import preprocess, biweight_trend
from kepler_detector.scanner import inverted_peaks, noise_floor
from kepler_detector.triage import assess_candidate
from kepler_detector.vetting import depth_consistency, vet


class TriageTests(unittest.TestCase):
    def candidate(self, **changes):
        row = dict(rf_probability=.8, cnn_probability=.75, priority_score=55,
                   depth_snr=14, number_of_transits=5,
                   transit_depth_consistency=.7, vetting_flags='')
        row.update(changes)
        return row

    def test_clean_model_agreement_is_prioritary(self):
        result = assess_candidate(self.candidate())
        self.assertTrue(result['triage_serious'])
        self.assertEqual(result['triage_label'], 'prioritario')

    def test_severe_vetting_flag_blocks_priority(self):
        result = assess_candidate(self.candidate(vetting_flags='single-event-dominated'))
        self.assertFalse(result['triage_serious'])
        self.assertEqual(result['triage_label'], 'descartable')

    def test_moderate_clean_signal_is_review(self):
        result = assess_candidate(self.candidate(rf_probability=.55, cnn_probability=.42,
                                                 priority_score=31))
        self.assertTrue(result['triage_review'])

    def test_legacy_row_with_incompatible_depths_is_discarded(self):
        # KIC 8499940 (P = 247.5 d): resultado anterior a la v2.1, sin la alerta en vetting_flags.
        row = self.candidate(rf_probability=.59, cnn_probability=0, depth_snr=10, number_of_transits=4,
                             individual_transit_depths='[24.320124,94.30853,111.888746,67.336032]',
                             individual_transit_snr='[1.749409,6.923742,8.048444,4.943533]')
        result = assess_candidate(row)
        self.assertFalse(result['triage_review'])
        self.assertEqual(result['triage_label'], 'descartable')
        self.assertIn('inconsistent-depths', result['triage_reason'])


class DepthConsistencyTests(unittest.TestCase):
    def test_repeatable_transits_are_not_flagged(self):
        measured = depth_consistency({'individual_transit_depths': [70, 62, 81, 66, 75],
                                      'individual_transit_snr': [5.0, 4.4, 5.8, 4.7, 5.4]})
        self.assertFalse(measured['inconsistent_depths'])
        self.assertFalse(measured['negative_transit'])
        self.assertEqual(measured['depth_chi2_dof'], 4)

    def test_deep_planet_with_tiny_errors_is_not_flagged(self):
        # 1 % de profundidad, errores de 20 ppm, variación real del 5 %: no es ruido.
        depths = [10000, 10400, 9700, 10200, 9600, 10300]
        measured = depth_consistency({'individual_transit_depths': depths,
                                      'individual_transit_snr': [d / 20 for d in depths]})
        self.assertFalse(measured['inconsistent_depths'])

    def test_negative_event_is_flagged_and_missing_data_is_neutral(self):
        flags = vet({'depth_snr': 12, 'number_of_transits': 4,
                     'individual_transit_depths': '[90,80,-60,75]',
                     'individual_transit_snr': '[6.4,5.7,-4.3,5.4]'})['vetting_flags']
        self.assertIn('negative-transit', flags)
        self.assertIn('inconsistent-depths', flags)
        empty = depth_consistency({})
        self.assertFalse(empty['inconsistent_depths'] or empty['negative_transit'])

    def test_noise_floor_flag_and_comparable_periods(self):
        peaks = [(200.0, 11.0), (2.0, 30.0)]
        self.assertEqual(noise_floor(peaks, 250.0), 11.0)
        self.assertTrue(np.isnan(noise_floor(peaks, 20.0)))
        base = {'depth_snr': 10, 'number_of_transits': 4}
        self.assertIn('below-noise-floor', vet({**base, 'noise_floor_snr': 11.0})['vetting_flags'])
        self.assertNotIn('below-noise-floor', vet({**base, 'noise_floor_snr': 6.0})['vetting_flags'])
        self.assertNotIn('below-noise-floor', vet(base)['vetting_flags'])

    def test_inverted_curve_ignores_real_transits(self):
        rng = np.random.default_rng(3)
        t = np.arange(0, 45, 0.0204) + 120.0
        y = 1 + rng.normal(0, 0.00015, len(t))
        y -= 0.003 * (np.abs((t - 120.7 + 1.6) % 3.2 - 1.6) < 0.08)
        curve = Curve(t, y, np.full(len(t), 0.00015), np.zeros(len(t)), {})
        peaks = inverted_peaks(curve, Config(coarse_points=2000, refine_peaks=4))
        self.assertTrue(peaks)
        self.assertLess(max(snr for _, snr in peaks), 15)  # El planeta real da SNR > 30.


class DetrendTests(unittest.TestCase):
    def curve(self):
        rng = np.random.default_rng(7)
        t = np.arange(0, 80, 0.0204) + 120.0
        y = 1 + rng.normal(0, 0.0001, len(t)) + 0.0004 * np.sin(t / 2.0)  # Ondulación lenta (~12.6 d).
        inside = np.abs((t - 150.0 + 20) % 40 - 20) < 0.25
        y[inside] -= 0.001
        return Curve(t, y, np.full(len(t), 0.0001), np.zeros(len(t)), {}), inside

    def test_biweight_is_default_and_preserves_transit(self):
        self.assertEqual(Config().detrend, 'biweight')
        curve, inside = self.curve()
        flat = preprocess(curve, Config())
        kept = np.isin(curve.time, flat.time)
        depth = np.median(flat.flux[~inside[kept]]) - flat.flux[inside[kept]].mean()
        self.assertGreater(depth, 0.0008)  # > 80 % de los 1000 ppm inyectados.
        self.assertLess(np.std(flat.flux[~inside[kept]]), 0.00013)  # La ondulación desaparece.

    def test_biweight_removes_more_slow_noise_than_median(self):
        curve, inside = self.curve()
        spread = {}
        for name, window in (('median', 5.0), ('biweight', 3.0)):
            flat = preprocess(curve, Config(detrend=name, flatten_window=window))
            outside = ~inside[np.isin(curve.time, flat.time)]
            spread[name] = np.std(flat.flux[outside])
        self.assertLess(spread['biweight'], spread['median'])

    def test_biweight_trend_is_block_independent(self):
        y = 1 + np.random.default_rng(1).normal(0, 0.001, 700)
        self.assertTrue(np.allclose(biweight_trend(y, 51), biweight_trend(y, 51, block=51 * 37)))

    def test_unknown_detrend_is_rejected(self):
        with self.assertRaises(ValueError):
            Config(detrend='spline')


if __name__ == '__main__':
    unittest.main()
