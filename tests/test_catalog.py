from pathlib import Path
import tempfile
import unittest

import pandas as pd

from kepler_detector.catalog import (discovery_batches, replenish_discovery_batches,
                                     select_discovery_targets)


class DiscoveryBatchTests(unittest.TestCase):
    def test_discovery_batches_partition_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            source = pd.DataFrame({
                'queue_rank': range(1, 26),
                'kepid': range(1001, 1026),
                'kepmag': [12.0] * 25,
            })
            source.to_csv(root / 'data/kepler_discovery_targets.csv', index=False)

            manifest = discovery_batches(root, batch_size=10)

            self.assertEqual(manifest.star_count.tolist(), [10, 10, 5])
            batches = [pd.read_csv(root / 'data/discovery_batches' / name)
                       for name in manifest.file]
            combined = pd.concat(batches, ignore_index=True)
            self.assertEqual(combined.queue_rank.tolist(), source.queue_rank.tolist())
            self.assertEqual(combined.kepid.nunique(), 25)
            self.assertTrue((manifest.status == 'pending').all())

    def test_rebuild_preserves_status_for_unchanged_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            source = pd.DataFrame({'queue_rank': range(1, 21),
                                   'kepid': range(1001, 1021)})
            source.to_csv(root / 'data/kepler_discovery_targets.csv', index=False)
            discovery_batches(root, batch_size=10)
            manifest_path = root / 'data/discovery_batches/manifest.csv'
            manifest = pd.read_csv(manifest_path)
            manifest.loc[0, ['status', 'completed_stars']] = ['completed', 10]
            manifest.to_csv(manifest_path, index=False)
            extended = pd.DataFrame({'queue_rank': range(1, 31),
                                     'kepid': range(1001, 1031)})
            extended.to_csv(root / 'data/kepler_discovery_targets.csv', index=False)

            rebuilt = discovery_batches(root, batch_size=10)

            self.assertEqual(len(rebuilt), 3)
            self.assertEqual(rebuilt.iloc[0].status, 'completed')
            self.assertEqual(rebuilt.iloc[0].completed_stars, 10)
            self.assertEqual(rebuilt.iloc[2].status, 'pending')

    def test_replenish_appends_next_targets_without_repeats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            stellar = pd.DataFrame({
                'kepid': range(1001, 1031), 'kepmag': [12.0] * 30,
                'teff': [5700] * 30, 'logg': [4.4] * 30,
                'dutycycle': [.9] * 30, 'dataspan': [1400] * 30,
                'rrmscdpp06p0': range(20, 50), 'nkoi': [0] * 30,
                'ntce': [0] * 30, 'nconfp': [0] * 30,
            })
            stellar.to_csv(root / 'data/kepler_stellar_dr25.csv', index=False)
            pd.DataFrame(columns=['kepid', 'kepoi_name', 'koi_pdisposition', 'koi_period']).to_csv(
                root / 'data/koi_dr25.csv', index=False)
            first = select_discovery_targets(stellar, set(), set(), limit=10)
            first.to_csv(root / 'data/kepler_discovery_targets.csv', index=False)

            result = replenish_discovery_batches(root, block_size=10, batch_size=5)

            targets = pd.read_csv(root / 'data/kepler_discovery_targets.csv')
            self.assertEqual(result['total_targets'], 20)
            self.assertEqual(targets.kepid.nunique(), 20)
            self.assertEqual(targets.iloc[:10].kepid.tolist(), first.kepid.tolist())
            self.assertEqual(result['total_batches'], 4)


if __name__ == '__main__':
    unittest.main()
