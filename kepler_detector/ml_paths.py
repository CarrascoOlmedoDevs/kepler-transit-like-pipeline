"""Rutas de entrenamiento y modelos por misión.

Kepler conserva sus rutas históricas. TESS tiene dataset, manifiesto y modelo propios,
entrenados con las disposiciones TFOPWG de los TOI. Para reutilizar el mismo código, en el
dataset TESS la columna `kepid` guarda el TIC y `kepoi_name` el nombre del TOI."""
from dataclasses import dataclass


@dataclass(frozen=True)
class MLPaths:
    mission: str
    dataset: str
    manifest: str
    progress: str
    errors: str
    model: str
    metrics: str
    results: str
    predictions: str
    importance: str
    label_column: str


PATHS = {
    'kepler': MLPaths('kepler', 'data/training_features.csv', 'data/build_manifest.json',
                      'data/build_progress.json', 'logs/build_errors.log', 'models/kepler_rf.joblib',
                      'models/kepler_rf_metrics.json', 'results', 'results/test_predictions.csv',
                      'results/feature_importance', 'koi_pdisposition'),
    'tess': MLPaths('tess', 'data/tess_training_features.csv', 'data/tess_build_manifest.json',
                    'data/tess_build_progress.json', 'logs/tess_build_errors.log', 'models/tess_rf.joblib',
                    'models/tess_rf_metrics.json', 'results/tess_rf', 'results/tess_rf/test_predictions.csv',
                    'results/tess_rf/feature_importance', 'tfopwg_disp'),
}


def get(mission='kepler'):
    try:
        return PATHS[str(mission).lower()]
    except KeyError:
        raise ValueError(f'Sin modelo propio para la misión {mission}; use kepler o tess') from None
