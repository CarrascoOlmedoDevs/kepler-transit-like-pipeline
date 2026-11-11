"""Días malos de Kepler y exigencia extra a las señales de período largo."""
import unittest

from kepler_detector import systematics, triage
from kepler_detector.triage import assess_candidate


def signal(period, epoch, transits=5, snr=12):
    return dict(period_days=period, epoch_bkjd=epoch, number_of_transits=transits, depth_snr=snr,
                rf_probability=0.6, cnn_probability=0.5, priority_score=50, transit_depth_consistency=0.9,
                vetting_flags='', duration_hours=12)


class SystematicsTests(unittest.TestCase):
    def tearDown(self):
        triage.set_bad_days([])

    def test_shared_day_is_marked_and_flags_signals_on_it(self):
        stars = {i: [signal(200 + 7 * i, 500.2)] for i in range(12)}            # todas pasan por 500
        stars.update({100 + i: [signal(230 + 3 * i, 130 + 9 * i)] for i in range(30)})
        result = systematics.bad_days(stars)
        self.assertIn(500.5, result['days'])
        self.assertGreaterEqual(result['threshold'], systematics.MIN_EVENTS)
        triage.set_bad_days(result['days'])
        on_day = signal(250.0, 500.2)
        self.assertGreaterEqual(systematics.epochs_on_bad_days(on_day, result['days']), 1)
        two = dict(on_day, number_of_transits=3)
        self.assertEqual(assess_candidate(two)['triage_label'], 'descartable')
        self.assertIn('systematic-epochs', assess_candidate(two)['triage_reason'])

    def test_quiet_sample_marks_nothing(self):
        stars = {i: [signal(180 + 5.3 * i, 130 + 11.7 * i)] for i in range(40)}
        self.assertEqual(systematics.bad_days(stars)['days'], [])

    def test_long_period_needs_snr_10_to_review(self):
        self.assertEqual(assess_candidate(signal(234.0, 349.9, snr=9.8))['triage_label'], 'débil')
        self.assertIn('período largo', assess_candidate(signal(234.0, 349.9, snr=9.8))['triage_reason'])
        self.assertEqual(assess_candidate(signal(234.0, 349.9, snr=10.5))['triage_label'], 'revisar')
        self.assertEqual(assess_candidate(signal(40.0, 130.0, snr=9.0))['triage_label'], 'revisar')

    def test_other_missions_ignore_kepler_days(self):
        triage.set_bad_days([500.5])
        row = dict(signal(250.0, 500.2, transits=3), mission='tess')
        self.assertFalse(triage.systematic_flag(row))


if __name__ == '__main__':
    unittest.main()
