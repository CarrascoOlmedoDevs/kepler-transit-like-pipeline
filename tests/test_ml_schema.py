import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import joblib
import pandas as pd
from config import Config
from test_pipeline import temporary_project, synthetic
from kepler_detector import FEATURE_VERSION, PIPELINE_VERSION
from kepler_detector.builder import build
from kepler_detector.features import extract, MODEL_FEATURES
from kepler_detector.mast import save_curve
from kepler_detector.ml import load_model, train, predict, evaluate, feature_frame
from kepler_detector.schema import (RF_FEATURE_COLUMNS, FEATURE_SCHEMA_HASH,
                                    schema_metadata, validate_dataset)
from kepler_detector.utils import setup, atomic_json, file_hash


class SchemaTests(unittest.TestCase):
    def test_schema_hash_and_single_source(self):
        self.assertEqual(FEATURE_VERSION, '2.0')
        self.assertEqual(PIPELINE_VERSION, '2.2')
        self.assertIs(MODEL_FEATURES, RF_FEATURE_COLUMNS)
        self.assertEqual(FEATURE_SCHEMA_HASH, hashlib.sha256(
            ('2.0|' + '|'.join(RF_FEATURE_COLUMNS)).encode()).hexdigest())
        self.assertEqual(list(feature_frame(pd.DataFrame(
            [{name: 1 for name in reversed(RF_FEATURE_COLUMNS)}])).columns), RF_FEATURE_COLUMNS)

    def test_dataset_compatibility(self):
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            for change in ({'feature_version': '1.0'}, {'feature_schema_hash': 'wrong'},
                           {'feature_columns': list(reversed(RF_FEATURE_COLUMNS))}):
                with self.subTest(change=change):
                    atomic_json({**schema_metadata(), **change}, root / 'data/build_manifest.json')
                    with self.assertRaisesRegex(ValueError, 'build --per-class 500 --force'):
                        validate_dataset(root)
            atomic_json(schema_metadata(), root / 'data/build_manifest.json')
            self.assertEqual(validate_dataset(root), schema_metadata())

    def test_model_compatibility(self):
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            good = {**schema_metadata(), 'model': 'placeholder'}
            for artifact in ({**good, 'feature_version': '1.0'},
                             {**good, 'feature_schema_hash': 'wrong'},
                             {**good, 'feature_columns': RF_FEATURE_COLUMNS[::-1]},
                             {'feature_version': '1.0', 'pipeline': 'old'}, 'bare estimator'):
                with self.subTest(artifact=artifact):
                    joblib.dump(artifact, root / 'models/kepler_rf.joblib')
                    with self.assertRaisesRegex(ValueError, 'RF model incompatible'):
                        load_model(root)
                    with self.assertRaisesRegex(ValueError, 'RF model incompatible'):
                        predict(artifact, [])
                    with self.assertRaisesRegex(ValueError, 'RF model incompatible'):
                        evaluate(root)
            joblib.dump(good, root / 'models/kepler_rf.joblib')
            self.assertEqual(load_model(root), good)

    def test_train_rejects_incompatible_dataset_before_fit(self):
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            for metadata in ({}, {**schema_metadata(), 'feature_version': '1.0'},
                             {**schema_metadata(), 'feature_schema_hash': 'wrong'},
                             {**schema_metadata(), 'feature_columns': []}):
                atomic_json(metadata, root / 'data/build_manifest.json')
                with patch('kepler_detector.ml.RandomForestClassifier') as estimator:
                    with self.assertRaisesRegex(ValueError, 'build --per-class 500 --force'):
                        train(root)
                    estimator.assert_not_called()

    def test_scan_and_scan_list_reject_old_model_even_with_checkpoint(self):
        from kepler_detector.scanner import scan, scan_list, signature
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            joblib.dump({'feature_version': '1.0'}, root / 'models/kepler_rf.joblib')
            cfg = Config()
            output = root / 'results/KIC_123'
            output.mkdir()
            atomic_json({'status': 'complete', 'signature': signature(root, cfg)},
                        output / 'metadata.json')
            targets = root / 'data/targets.csv'
            pd.DataFrame({'kepid': [123]}).to_csv(targets, index=False)
            with self.assertRaisesRegex(ValueError, 'RF model incompatible'):
                scan(root, 123, cfg)
            with self.assertRaisesRegex(ValueError, 'RF model incompatible'):
                scan_list(root, targets, cfg)

    def check_build(self, force):
        with temporary_project() as tmp:
            root = setup(Path(tmp))
            curve, candidate = synthetic()
            cache = root / 'cache/lightcurves/KIC_00000123.npz'
            save_curve(curve, cache)
            catalog = pd.DataFrame([dict(kepid=123, kepoi_name='K001.01',
                                        koi_pdisposition='CANDIDATE', koi_period=3.2)])
            catalog.to_csv(root / 'data/koi_dr25.csv', index=False)
            catalog.to_csv(root / 'data/tce_dr25.csv', index=False)
            preserved = {p: (file_hash(p), p.stat().st_mtime_ns) for p in (
                cache, root / 'data/koi_dr25.csv', root / 'data/tce_dr25.csv')}
            pd.DataFrame([dict(kepid=123, kepoi_name='old', label=1)]).to_csv(
                root / 'data/training_features.csv', index=False)
            atomic_json({'old': {'status': 'complete'}, 'K001.01': {'status': 'failed'}},
                        root / 'data/build_progress.json')
            atomic_json(schema_metadata() if force else {'feature_version': '1.0'},
                        root / 'data/build_manifest.json')
            # Real download() must hit the NPZ; no network fallback is permitted.
            with patch('lightkurve.search_lightcurve', side_effect=AssertionError('network used')), \
                 patch('kepler_detector.builder.get_catalog', return_value=catalog), \
                 patch('kepler_detector.builder.preprocess', return_value=curve), \
                 patch('kepler_detector.builder.search', return_value=([candidate], None)), \
                 patch('kepler_detector.builder.extract', wraps=extract) as extractor:
                build(root, Config(), per_class=1, force=force)
                extractor.assert_called_once()
            frame = pd.read_csv(root / 'data/training_features.csv')
            self.assertEqual(frame.kepoi_name.tolist(), ['K001.01'])
            self.assertEqual([c for c in frame if c in RF_FEATURE_COLUMNS], RF_FEATURE_COLUMNS)
            manifest = validate_dataset(root)
            self.assertEqual(manifest['row_count'], 1)
            self.assertIn('created_at', manifest)
            progress = json.loads((root / 'data/build_progress.json').read_text())
            self.assertNotIn('old', progress)
            self.assertEqual(progress['K001.01']['status'], 'complete')
            for path, original in preserved.items():
                self.assertEqual((file_hash(path), path.stat().st_mtime_ns), original)
            # A matching manifest resumes without recomputing completed rows.
            with patch('kepler_detector.builder.get_catalog', return_value=catalog), \
                 patch('kepler_detector.builder.download', side_effect=AssertionError('recomputed')):
                build(root, Config(), per_class=1)

    def test_build_force_preserves_lightcurve_cache(self):
        self.check_build(force=True)

    def test_build_invalidates_v1_and_reuses_cached_lightcurves(self):
        self.check_build(force=False)
