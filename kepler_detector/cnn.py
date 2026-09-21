"""Experimental ExoMiner-inspired classifier for Kepler transit-like signals.

This module is intentionally decoupled from the frozen Pipeline 2.0 scanner.
It builds phase-folded morphology views from the project's own BLS candidates and
trains a small Keras CNN on DR25 CANDIDATE/FALSE POSITIVE labels.

TensorFlow is imported lazily so the base detector keeps its lightweight
dependencies and existing CLI/tests continue to work without the optional CNN stack.
"""
from __future__ import annotations

import json
import logging
import re
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import GroupShuffleSplit

from .bls import phase, search
from .catalog import get_catalog
from .mast import download
from .preprocessing import preprocess
from .schema import RF_FEATURE_COLUMNS, timestamp
from .utils import atomic_csv, atomic_json

EXOMINER_LITE_VERSION = "0.1"
VIEW_SPEC = {
    "global": 1001,
    "local": 201,
    "odd": 201,
    "even": 201,
    "secondary": 201,
}


def _tensorflow():
    try:
        import tensorflow as tf
    except ImportError as error:
        raise RuntimeError(
            "ExoMiner-lite requiere TensorFlow opcional. "
            "Instale: pip install -r requirements-cnn.txt"
        ) from error
    return tf


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value))


def _fill_and_normalize(mean, count):
    """Normaliza morfología y conserva cobertura por bin como segundo canal."""
    mean = np.asarray(mean, dtype=float)
    count = np.asarray(count, dtype=float)
    valid = np.isfinite(mean)
    if not valid.any():
        raise ValueError("Vista sin bins de flujo válidos")
    x = np.arange(len(mean))
    filled = mean.copy()
    if valid.sum() == 1:
        filled[~valid] = filled[valid][0]
    else:
        filled[~valid] = np.interp(x[~valid], x[valid], filled[valid])
    centered = filled - np.median(filled[valid])
    scale = np.percentile(np.abs(centered[valid]), 95)
    if not np.isfinite(scale) or scale < 1e-8:
        scale = max(float(np.std(centered[valid])), 1e-8)
    flux = np.clip(centered / scale, -10, 10)
    coverage = np.log1p(np.clip(count, 0, None))
    maximum = float(coverage.max()) if len(coverage) else 0.0
    if maximum > 0:
        coverage /= maximum
    return np.column_stack([flux, coverage]).astype(np.float32)


def _bin_view(curve, phases, span, bins, mask=None):
    mask = np.ones(len(curve.time), dtype=bool) if mask is None else np.asarray(mask, dtype=bool)
    mask &= np.isfinite(phases) & np.isfinite(curve.flux)
    edges = np.linspace(-span, span, bins + 1)
    count, _ = np.histogram(phases[mask], edges)
    total, _ = np.histogram(phases[mask], edges, weights=curve.flux[mask])
    mean = np.divide(total, count, out=np.full(bins, np.nan), where=count > 0)
    return _fill_and_normalize(mean, count)


def make_views(curve, candidate):
    """Crea ramas morfológicas compactas tipo ExoMiner desde un candidato BLS."""
    p, d, epoch = float(candidate.period), float(candidate.duration), float(candidate.epoch)
    if not (0 < d < p):
        raise ValueError("Período/duración inválidos para vistas CNN")
    ph = phase(curve.time, p, epoch)
    local_span = min(2.5 * d, p / 2)
    events = np.floor((curve.time - epoch) / p + 0.5).astype(int)
    secondary_ph = phase(curve.time, p, epoch + p / 2)
    return {
        "global": _bin_view(curve, ph, p / 2, VIEW_SPEC["global"]),
        "local": _bin_view(curve, ph, local_span, VIEW_SPEC["local"]),
        "odd": _bin_view(curve, ph, local_span, VIEW_SPEC["odd"], events % 2 != 0),
        "even": _bin_view(curve, ph, local_span, VIEW_SPEC["even"], events % 2 == 0),
        "secondary": _bin_view(curve, secondary_ph, local_span, VIEW_SPEC["secondary"]),
    }


def _view_path(root, kepoi_name):
    return root / "data/cnn_views" / f"{_safe_name(kepoi_name)}.npz"


def build_dataset(root, config, force=False, limit=None):
    """Backfill de vistas CNN para el training_features.csv etiquetado existente."""
    root = Path(root)
    features_path = root / "data/training_features.csv"
    if not features_path.exists():
        raise FileNotFoundError("Falta data/training_features.csv; ejecute build primero")
    frame = pd.read_csv(features_path)
    required = {"kepid", "kepoi_name", "label", *RF_FEATURE_COLUMNS}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"training_features.csv incompleto: faltan {sorted(missing)}")

    catalog = get_catalog(root)
    catalog = catalog.drop_duplicates("kepoi_name").set_index("kepoi_name", drop=False)
    output_dir = root / "data/cnn_views"
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = root / "data/cnn_view_index.csv"
    existing = pd.DataFrame() if force or not index_path.exists() else pd.read_csv(index_path)
    completed = set(existing.kepoi_name.astype(str)) if len(existing) else set()
    records = [] if force else existing.to_dict("records")
    logger = logging.getLogger("kepler")
    processed = 0

    for kic, group in frame.groupby("kepid", sort=True):
        pending = [
            row
            for row in group.itertuples()
            if force
            or str(row.kepoi_name) not in completed
            or not _view_path(root, row.kepoi_name).exists()
        ]
        if not pending:
            continue
        try:
            curve = preprocess(download(root, int(kic), config.quarter), config)
        except Exception as error:
            logger.warning("CNN build KIC %s falló: %s", kic, error)
            continue
        for row in pending:
            if limit is not None and processed >= limit:
                atomic_csv(pd.DataFrame(records), index_path)
                return {"completed": len(records), "requested_limit": limit}
            try:
                if row.kepoi_name not in catalog.index:
                    raise KeyError(f"{row.kepoi_name} no está en catálogo")
                known_period = float(catalog.loc[row.kepoi_name, "koi_period"])
                candidates, _ = search(curve, config, known_period=known_period)
                if not candidates:
                    raise ValueError("BLS guiado no devolvió candidato")
                candidate = candidates[0]
                views = make_views(curve, candidate)
                path = _view_path(root, row.kepoi_name)
                np.savez_compressed(
                    path,
                    **views,
                    period=np.float64(candidate.period),
                    duration=np.float64(candidate.duration),
                    epoch=np.float64(candidate.epoch),
                )
                records = [
                    item
                    for item in records
                    if str(item.get("kepoi_name")) != str(row.kepoi_name)
                ]
                records.append(
                    {
                        "kepid": int(row.kepid),
                        "kepoi_name": str(row.kepoi_name),
                        "label": int(row.label),
                        "view_file": str(path.relative_to(root)).replace("\\", "/"),
                        "candidate_period": float(candidate.period),
                        "candidate_duration": float(candidate.duration),
                        "candidate_epoch": float(candidate.epoch),
                    }
                )
                completed.add(str(row.kepoi_name))
                processed += 1
                atomic_csv(pd.DataFrame(records), index_path)
            except Exception as error:
                with open(root / "logs/cnn_build_errors.log", "a", encoding="utf-8") as stream:
                    stream.write(f"{row.kepoi_name}\n{traceback.format_exc()}\n")
                logger.warning("CNN build %s falló: %s", row.kepoi_name, error)
        del curve

    atomic_csv(pd.DataFrame(records), index_path)
    return {"completed": len(records), "new": processed}


def _split_both_classes(frame, test_size, seed):
    splitter = GroupShuffleSplit(n_splits=100, test_size=test_size, random_state=seed)
    for train_index, test_index in splitter.split(frame, frame.label, frame.kepid):
        train, test = frame.iloc[train_index], frame.iloc[test_index]
        if train.label.nunique() == 2 and test.label.nunique() == 2:
            return train_index, test_index
    raise ValueError("No se pudo dividir por estrella conservando ambas clases")


def _load_arrays(root):
    root = Path(root)
    feature_path = root / "data/training_features.csv"
    index_path = root / "data/cnn_view_index.csv"
    if not feature_path.exists() or not index_path.exists():
        raise FileNotFoundError("Ejecute build y después cnn-build antes de entrenar")
    features = pd.read_csv(feature_path)
    index = pd.read_csv(index_path)
    frame = features.merge(
        index[["kepid", "kepoi_name", "label", "view_file"]],
        on=["kepid", "kepoi_name", "label"],
        how="inner",
    )
    if not len(frame):
        raise ValueError("No hay ejemplos CNN compatibles")
    arrays = {name: [] for name in VIEW_SPEC}
    keep = []
    for row in frame.itertuples():
        path = root / row.view_file
        if not path.exists():
            continue
        with np.load(path) as data:
            if any(name not in data for name in VIEW_SPEC):
                continue
            for name in VIEW_SPEC:
                arrays[name].append(np.asarray(data[name], dtype=np.float32))
        keep.append(row.Index)
    frame = frame.loc[keep].reset_index(drop=True)
    arrays = {name: np.stack(values) for name, values in arrays.items()}
    scalars = (
        frame[RF_FEATURE_COLUMNS]
        .astype(float)
        .replace([np.inf, -np.inf], np.nan)
        .to_numpy()
    )
    labels = frame.label.astype(np.float32).to_numpy()
    return frame, arrays, scalars, labels


def _fit_scalar_transform(train_scalars):
    median = np.nanmedian(train_scalars, axis=0)
    median = np.where(np.isfinite(median), median, 0.0)
    filled = np.where(np.isfinite(train_scalars), train_scalars, median)
    mean = filled.mean(axis=0)
    std = filled.std(axis=0)
    std = np.where(std > 1e-8, std, 1.0)
    return median, mean, std


def _transform_scalars(values, median, mean, std):
    values = np.asarray(values, dtype=float)
    filled = np.where(np.isfinite(values), values, median)
    return ((filled - mean) / std).astype(np.float32)


def _build_model(tf, scalar_dim):
    def branch(name, bins, pool):
        inp = tf.keras.Input(shape=(bins, 2), name=name)
        x = tf.keras.layers.Conv1D(8, 5, padding="same", activation="relu")(inp)
        x = tf.keras.layers.MaxPool1D(pool_size=pool)(x)
        x = tf.keras.layers.Conv1D(16, 5, padding="same", activation="relu")(x)
        x = tf.keras.layers.GlobalAveragePooling1D()(x)
        x = tf.keras.layers.Dense(8, activation="relu")(x)
        return inp, x

    inputs, encoded = [], []
    for name in ("global", "local", "odd", "even", "secondary"):
        inp, out = branch(name, VIEW_SPEC[name], 8 if name == "global" else 2)
        inputs.append(inp)
        encoded.append(out)

    scalar_input = tf.keras.Input(shape=(scalar_dim,), name="scalars")
    scalar_encoded = tf.keras.layers.Dense(32, activation="relu")(scalar_input)
    scalar_encoded = tf.keras.layers.Dropout(0.15)(scalar_encoded)
    inputs.append(scalar_input)
    encoded.append(scalar_encoded)

    x = tf.keras.layers.Concatenate()(encoded)
    x = tf.keras.layers.Dense(48, activation="relu")(x)
    x = tf.keras.layers.Dropout(0.20)(x)
    x = tf.keras.layers.Dense(16, activation="relu")(x)
    output = tf.keras.layers.Dense(
        1, activation="sigmoid", name="candidate_probability"
    )(x)
    return tf.keras.Model(inputs=inputs, outputs=output, name="kepler_exominer_lite")


def _inputs(arrays, scalars, indices):
    result = {name: arrays[name][indices] for name in VIEW_SPEC}
    result["scalars"] = scalars[indices]
    return result


def _metrics(y, probability, threshold=0.5):
    prediction = (np.asarray(probability) >= threshold).astype(int)
    y = np.asarray(y).astype(int)
    return {
        "accuracy": float(accuracy_score(y, prediction)),
        "precision": float(precision_score(y, prediction, zero_division=0)),
        "recall": float(recall_score(y, prediction, zero_division=0)),
        "f1": float(f1_score(y, prediction, zero_division=0)),
        "roc_auc": float(roc_auc_score(y, probability)),
        "average_precision": float(average_precision_score(y, probability)),
        "confusion_matrix": confusion_matrix(y, prediction, labels=[0, 1]).tolist(),
        "test_rows": int(len(y)),
    }


def train(root, seed=42, test_size=0.25, epochs=60, batch_size=32):
    tf = _tensorflow()
    tf.keras.utils.set_random_seed(seed)
    try:
        tf.config.experimental.enable_op_determinism()
    except Exception:
        pass

    root = Path(root)
    frame, arrays, scalars, labels = _load_arrays(root)
    train_idx, test_idx = _split_both_classes(frame, test_size, seed)
    inner = frame.iloc[train_idx].reset_index(drop=True)
    inner_train_rel, val_rel = _split_both_classes(inner, 0.2, seed + 1)
    inner_train_idx = np.asarray(train_idx)[inner_train_rel]
    val_idx = np.asarray(train_idx)[val_rel]

    median, mean, std = _fit_scalar_transform(scalars[inner_train_idx])
    normalized = _transform_scalars(scalars, median, mean, std)

    model = _build_model(tf, len(RF_FEATURE_COLUMNS))
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=1e-3),
        loss="binary_crossentropy",
        metrics=[
            tf.keras.metrics.AUC(curve="PR", name="auc_pr"),
            tf.keras.metrics.AUC(name="roc_auc"),
            tf.keras.metrics.Precision(name="precision"),
            tf.keras.metrics.Recall(name="recall"),
        ],
    )
    counts = np.bincount(labels[inner_train_idx].astype(int), minlength=2)
    total = counts.sum()
    class_weight = {
        index: float(total / (2 * count))
        for index, count in enumerate(counts)
        if count
    }
    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_auc_pr", mode="max", patience=8, restore_best_weights=True
        )
    ]
    history = model.fit(
        _inputs(arrays, normalized, inner_train_idx),
        labels[inner_train_idx],
        validation_data=(_inputs(arrays, normalized, val_idx), labels[val_idx]),
        epochs=epochs,
        batch_size=batch_size,
        class_weight=class_weight,
        callbacks=callbacks,
        verbose=2,
    )

    models_dir = root / "models"
    models_dir.mkdir(exist_ok=True)
    model_path = models_dir / "kepler_exominer_lite.keras"
    model.save(model_path)

    probability = model.predict(
        _inputs(arrays, normalized, test_idx), verbose=0
    ).reshape(-1)
    metrics = _metrics(labels[test_idx], probability)
    metadata = {
        "model_type": "ExoMinerLiteCNN",
        "architecture_version": EXOMINER_LITE_VERSION,
        "training_timestamp": timestamp(),
        "seed": seed,
        "test_size": test_size,
        "feature_columns": list(RF_FEATURE_COLUMNS),
        "view_spec": VIEW_SPEC,
        "scalar_median": median.tolist(),
        "scalar_mean": mean.tolist(),
        "scalar_std": std.tolist(),
        "train_kepids": sorted(map(int, frame.iloc[train_idx].kepid.unique())),
        "test_kepids": sorted(map(int, frame.iloc[test_idx].kepid.unique())),
        "train_rows": int(len(train_idx)),
        "test_rows": int(len(test_idx)),
        "epochs_ran": int(len(history.history.get("loss", []))),
        "warning": (
            "Clasificación experimental CANDIDATE/FALSE POSITIVE inspirada en la "
            "metodología ExoMiner; no valida ni confirma planetas."
        ),
    }
    atomic_json(metadata, models_dir / "kepler_exominer_lite_metadata.json")
    atomic_json(metrics, models_dir / "kepler_exominer_lite_metrics.json")
    predictions = frame.iloc[test_idx][["kepid", "kepoi_name", "label"]].copy()
    predictions["cnn_probability"] = probability
    atomic_csv(predictions, root / "results/cnn_test_predictions.csv")
    return metrics


def evaluate(root):
    tf = _tensorflow()
    root = Path(root)
    metadata_path = root / "models/kepler_exominer_lite_metadata.json"
    model_path = root / "models/kepler_exominer_lite.keras"
    if not metadata_path.exists() or not model_path.exists():
        raise FileNotFoundError("Falta modelo CNN; ejecute cnn-train")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("architecture_version") != EXOMINER_LITE_VERSION:
        raise ValueError("Versión de arquitectura CNN incompatible")
    frame, arrays, scalars, labels = _load_arrays(root)
    test_set = set(map(int, metadata["test_kepids"]))
    test_idx = np.flatnonzero(frame.kepid.astype(int).isin(test_set).to_numpy())
    if not len(test_idx):
        raise ValueError("No se encontró el conjunto test CNN")
    median = np.asarray(metadata["scalar_median"], dtype=float)
    mean = np.asarray(metadata["scalar_mean"], dtype=float)
    std = np.asarray(metadata["scalar_std"], dtype=float)
    normalized = _transform_scalars(scalars, median, mean, std)
    model = tf.keras.models.load_model(model_path, compile=False)
    probability = model.predict(
        _inputs(arrays, normalized, test_idx), verbose=0
    ).reshape(-1)
    metrics = _metrics(labels[test_idx], probability)
    atomic_json(metrics, root / "models/kepler_exominer_lite_metrics.json")
    predictions = frame.iloc[test_idx][["kepid", "kepoi_name", "label"]].copy()
    predictions["cnn_probability"] = probability
    atomic_csv(predictions, root / "results/cnn_test_predictions.csv")
    return metrics


def load_predictor(root):
    """Load the optional CNN and its training-only scalar transformation."""
    root = Path(root)
    metadata_path = root / "models/kepler_exominer_lite_metadata.json"
    model_path = root / "models/kepler_exominer_lite.keras"
    if not metadata_path.exists() or not model_path.exists():
        return None
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("architecture_version") != EXOMINER_LITE_VERSION:
        raise ValueError("Versión de arquitectura CNN incompatible")
    if metadata.get("feature_columns") != list(RF_FEATURE_COLUMNS):
        raise ValueError("Esquema de variables CNN incompatible")
    tf = _tensorflow()
    return {
        "model": tf.keras.models.load_model(model_path, compile=False),
        "median": np.asarray(metadata["scalar_median"], dtype=float),
        "mean": np.asarray(metadata["scalar_mean"], dtype=float),
        "std": np.asarray(metadata["scalar_std"], dtype=float),
        "architecture_version": metadata["architecture_version"],
    }


def predict_candidate(predictor, curve, candidate, features):
    """Score one pipeline candidate without changing RF ranking or vetting."""
    if predictor is None:
        return np.nan
    views = make_views(curve, candidate)
    scalar_values = np.asarray(
        [[features.get(name, np.nan) for name in RF_FEATURE_COLUMNS]], dtype=float
    )
    normalized = _transform_scalars(
        scalar_values, predictor["median"], predictor["mean"], predictor["std"]
    )
    inputs = {name: np.expand_dims(values, axis=0) for name, values in views.items()}
    inputs["scalars"] = normalized
    probability = predictor["model"].predict(inputs, verbose=0).reshape(-1)
    return float(probability[0])
