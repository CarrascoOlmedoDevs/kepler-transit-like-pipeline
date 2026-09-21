import unittest

from kepler_detector.triage import assess_candidate


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


if __name__ == '__main__':
    unittest.main()
