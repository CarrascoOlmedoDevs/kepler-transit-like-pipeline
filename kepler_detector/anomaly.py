"""Detector opcional funcional; score alto = inusual, no más planetario."""
import joblib
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from .ml import feature_frame


def fit_anomaly(frame, output, seed=42):
    model = make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True),
                          IsolationForest(n_estimators=200, random_state=seed, n_jobs=1))
    model.fit(feature_frame(frame))
    joblib.dump(model, output)
    return model


def anomaly_scores(model, frame):
    return -model.score_samples(feature_frame(frame))
