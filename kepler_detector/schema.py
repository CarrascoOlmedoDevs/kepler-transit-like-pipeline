"""Single source of truth for ML artifact compatibility (no schema migration)."""
import hashlib
import json
from datetime import datetime, timezone
from . import FEATURE_VERSION, PIPELINE_VERSION

RF_FEATURE_COLUMNS = [
    'period_days', 'duration_hours', 'depth_ppm', 'depth_snr', 'bls_power',
    'bls_log_likelihood', 'number_of_transits', 'duty_cycle', 'odd_depth_ppm',
    'even_depth_ppm', 'odd_even_sigma', 'secondary_depth_ppm', 'secondary_snr',
    'secondary_depth_ratio', 'half_period_depth', 'double_period_score', 'oot_rms_ppm',
    'in_transit_rms_ppm', 'transit_symmetry_score', 'baseline_days', 'number_of_points',
    'number_of_in_transit_points', 'phase_coverage', 'transit_coverage', 'fit_improvement',
]
FEATURE_SCHEMA_HASH = hashlib.sha256(
    (FEATURE_VERSION + '|' + '|'.join(RF_FEATURE_COLUMNS)).encode('utf-8')).hexdigest()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def schema_metadata():
    return dict(feature_version=FEATURE_VERSION, pipeline_version=PIPELINE_VERSION,
                feature_schema_hash=FEATURE_SCHEMA_HASH, feature_columns=list(RF_FEATURE_COLUMNS))


def compatible(metadata):
    return isinstance(metadata, dict) and all(
        metadata.get(key) == schema_metadata()[key]
        for key in ('feature_version', 'feature_schema_hash', 'feature_columns'))


def read_manifest(root):
    try:
        return json.loads((root / 'data/build_manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def validate_dataset(root):
    manifest = read_manifest(root)
    if not compatible(manifest):
        raise ValueError('Training feature dataset incompatible with current feature schema. '
                         'Rebuild using: python kepler_rf_detector.py build --per-class 500 --force')
    return manifest
