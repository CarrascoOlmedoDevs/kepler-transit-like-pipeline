"""RF desacoplado: imputa NaN y reserva estrellas completas para test."""
import logging
import platform
import importlib.metadata
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.model_selection import GroupShuffleSplit
from sklearn.metrics import (accuracy_score, precision_score, recall_score, f1_score,
                             roc_auc_score, average_precision_score, confusion_matrix,
                             ConfusionMatrixDisplay, RocCurveDisplay, PrecisionRecallDisplay)
from .schema import RF_FEATURE_COLUMNS, schema_metadata, compatible, validate_dataset, timestamp

FEATURES = RF_FEATURE_COLUMNS
from . import FEATURE_VERSION
from .utils import atomic_json, atomic_csv, file_hash
from .plotting import plt, save


def feature_frame(frame):
    if not set(FEATURES).issubset(frame.columns):
        raise ValueError('Missing RF feature columns; rebuild the training dataset with build --force')
    return frame.reindex(columns=FEATURES).astype(float).replace([np.inf, -np.inf], np.nan)


def load_model(root):
    artifact = joblib.load(root / 'models/kepler_rf.joblib')
    validate_model(artifact)
    return artifact


def validate_model(artifact):
    if not compatible(artifact) or 'model' not in artifact:
        version = artifact.get('feature_version', 'unknown') if isinstance(artifact, dict) else 'unknown'
        raise ValueError('RF model incompatible with current feature schema.\n'
                         f'Model feature version: {version}\n'
                         f'Current feature version: {FEATURE_VERSION}\n'
                         'Retrain using the current training dataset.')


def predict(artifact, rows):
    validate_model(artifact)
    index = list(artifact['model'].classes_).index(1)
    return artifact['model'].predict_proba(feature_frame(pd.DataFrame(rows)))[:, index]


def report(artifact, test, root):
    y = test.label.astype(int)
    probability = predict(artifact, test)
    predicted = (probability >= 0.5).astype(int)
    metrics = {name: float(func(y, predicted, zero_division=0)) for name, func in
               [('precision', precision_score), ('recall', recall_score), ('f1', f1_score)]}
    metrics.update(accuracy=float(accuracy_score(y, predicted)), roc_auc=float(roc_auc_score(y, probability)),
                   average_precision=float(average_precision_score(y, probability)),
                   confusion_matrix=confusion_matrix(y, predicted, labels=[0, 1]).tolist(),
                   test_rows=len(test), test_stars=int(test.kepid.nunique()),
                   train_stars=len(artifact['train_kepids']), seed=artifact['seed'],
                   train_rows=artifact['train_rows'],
                   small_sample_warning=artifact['train_rows'] < 200,
                   dataset_sha256=artifact['dataset_sha256'],
                   warning='Clasificación de etiquetas DR25, no probabilidad de existencia de un planeta')
    atomic_json(metrics, root / 'models/kepler_rf_metrics.json')
    atomic_csv(test[['kepid', 'kepoi_name', 'label']].assign(rf_probability=probability),
               root / 'results/test_predictions.csv')
    for filename, display, args in [
        ('confusion_matrix.png', ConfusionMatrixDisplay, {'y_pred': predicted, 'labels': [0, 1]}),
        ('roc_curve.png', RocCurveDisplay, {'y_pred': probability}),
        ('precision_recall_curve.png', PrecisionRecallDisplay, {'y_pred': probability})]:
        fig, ax = plt.subplots(figsize=(6, 5))
        display.from_predictions(y, ax=ax, **args)
        save(fig, root / 'results' / filename)
    return metrics


def train(root, seed=42, test_size=0.25):
    manifest = validate_dataset(root)
    path = root / 'data/training_features.csv'
    data = pd.read_csv(path)
    feature_frame(data)
    if data.kepoi_name.duplicated().any() or not set(data.label.unique()) == {0, 1}:
        raise ValueError('Dataset requiere KOI únicos y dos clases 0/1')
    if data.groupby('label').kepid.nunique().min() < 4:
        raise ValueError('Se requieren al menos 4 estrellas por clase para un split útil')
    # Primer split con ambas clases; no se selecciona según las métricas obtenidas.
    split = GroupShuffleSplit(n_splits=100, test_size=test_size, random_state=seed)
    selected = None
    for a, b in split.split(data, data.label, data.kepid):
        if data.iloc[a].label.nunique() == 2 and data.iloc[b].label.nunique() == 2:
            selected = a, b
            break
    if selected is None:
        raise ValueError('No se pudo dividir por estrella conservando ambas clases')
    a, b = selected
    training, testing = data.iloc[a], data.iloc[b]
    pipeline = Pipeline([('impute', SimpleImputer(strategy='median', add_indicator=True, keep_empty_features=True)),
                         ('rf', RandomForestClassifier(n_estimators=400, min_samples_leaf=2,
                                                      class_weight='balanced', random_state=seed, n_jobs=1))])
    pipeline.fit(feature_frame(training), training.label.astype(int))
    artifact = {'model': pipeline, **schema_metadata(),
                'model_type': 'RandomForestClassifier', 'training_timestamp': timestamp(),
                'train_kepids': sorted(map(int, training.kepid.unique())),
                'test_kepids': sorted(map(int, testing.kepid.unique())),
                'dataset_sha256': file_hash(path), 'seed': seed, 'build_manifest': manifest,
                'train_rows': len(training),
                'python': platform.python_version(), 'versions': {x: importlib.metadata.version(x)
                for x in ('numpy', 'pandas', 'scikit-learn', 'astropy', 'lightkurve')}}
    joblib.dump(artifact, root / 'models/kepler_rf.joblib')
    names = pipeline.named_steps['impute'].get_feature_names_out(FEATURES)
    importance = pd.DataFrame({'feature': names, 'importance': pipeline.named_steps['rf'].feature_importances_})
    importance = importance.sort_values('importance', ascending=False)
    atomic_csv(importance, root / 'results/feature_importance.csv')
    fig, ax = plt.subplots(figsize=(9, 9))
    importance.iloc[::-1].plot.barh(x='feature', y='importance', ax=ax, legend=False)
    ax.set_title('Importancia por impureza (puede estar sesgada)')
    save(fig, root / 'results/feature_importance.png')
    metrics = report(artifact, testing, root)
    logging.getLogger('kepler').info('Métricas test por estrella: %s', metrics)
    return metrics


def evaluate(root):
    artifact = load_model(root)
    validate_dataset(root)
    path = root / 'data/training_features.csv'
    if file_hash(path) != artifact['dataset_sha256']:
        raise ValueError('Dataset cambió después del entrenamiento: reentrene antes de evaluar')
    frame = pd.read_csv(path)
    testing = frame[frame.kepid.isin(artifact['test_kepids'])]
    if set(testing.kepid) & set(artifact['train_kepids']):
        raise ValueError('Solapamiento de estrellas train/test')
    return report(artifact, testing, root)
