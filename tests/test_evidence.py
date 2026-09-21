import csv
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kepler_detector.evidence import REPORT_NAME, record_serious_evidence


class EvidenceReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.results = self.root / 'star'
        self.results.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write_candidates(self, rows):
        fields = ['rank', 'period_days', 'rf_probability', 'cnn_probability',
                  'priority_score', 'depth_snr', 'number_of_transits',
                  'transit_depth_consistency', 'depth_ppm', 'duration_hours',
                  'classification', 'vetting_flags']
        with (self.results / 'candidates.csv').open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def candidate(self, **changes):
        row = dict(rank=1, period_days=12.5, rf_probability=.91,
                   cnn_probability=.84, priority_score=66, depth_snr=16,
                   number_of_transits=9, transit_depth_consistency=.72,
                   depth_ppm=410, duration_hours=3.2,
                   classification='interesting candidate', vetting_flags='')
        row.update(changes)
        return row

    def test_serious_candidate_is_written_without_duplicates(self):
        self.write_candidates([self.candidate()])
        report_dir = self.root / 'desktop'
        first = record_serious_evidence(self.root, 'job1', 123, self.results, report_dir)
        second = record_serious_evidence(self.root, 'job1', 123, self.results, report_dir)
        text = first.read_text(encoding='utf-8')
        self.assertEqual(first, second)
        self.assertEqual(text.count('KIC 123 · rango 1'), 1)
        self.assertIn('RF: 0.910', text)
        self.assertIn('CNN: 0.840', text)
        ledger = json.loads((self.root / 'results/local_panel/serious_evidence.json').read_text())
        self.assertEqual(len(ledger['candidates']), 1)

    def test_blocked_candidate_is_not_reported(self):
        self.write_candidates([self.candidate(vetting_flags='odd-even')])
        report = record_serious_evidence(self.root, 'job1', 123, self.results,
                                         self.root / 'desktop')
        text = report.read_text(encoding='utf-8')
        self.assertIn('superan el filtro estricto: 0', text)
        self.assertNotIn('KIC 123 · rango', text)


if __name__ == '__main__':
    unittest.main()
