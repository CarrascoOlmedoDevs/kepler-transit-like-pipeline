# ExoMiner-lite experimental classifier

## Purpose

Pipeline 2.0 remains frozen and reproducible. `ExoMiner-lite` is an
**experimental classifier** trained alongside the existing Random Forest without
changing BLS search, deterministic vetting, candidate families, or the frozen
blind benchmark.

The design is inspired by the *methodology* used by NASA ExoMiner: combine several
phase-folded morphology views with scalar diagnostics. It is an independent
implementation for this project. No NASA ExoMiner source code or model weights are
vendored, and this project does not claim to reproduce NASA ExoMiner.

It does not validate or confirm planets.

## Inputs

The current Random Forest sees 25 scalar features. ExoMiner-lite adds morphology
that those summaries can discard:

- global phase-folded flux + coverage;
- local transit flux + coverage;
- odd-transit local view;
- even-transit local view;
- phase-0.5 / secondary local view;
- the same 25 scalar RF features.

Each time-series view has two channels: normalized flux morphology and normalized
per-bin coverage.

## Optional installation

The base detector still uses `requirements.txt`. TensorFlow is optional:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-cnn.txt
```

## Build morphology views

Create the ordinary scalar dataset first:

```powershell
python kepler_rf_detector.py build --per-class 500
```

Then create the CNN views from the same cached Kepler curves:

```powershell
python kepler_rf_detector.py cnn-build
```

Small smoke run:

```powershell
python kepler_rf_detector.py cnn-build --limit 20
```

Artifacts:

```text
data/
  training_features.csv
  cnn_view_index.csv
  cnn_views/
    Kxxxxx.xx.npz
```

`cnn-build` does not modify `training_features.csv`.

## Train

```powershell
python kepler_rf_detector.py cnn-train --seed 42 --test-size 0.25 --epochs 60 --batch-size 32
```

The split is by `kepid`, never by individual rows. A second group split creates
validation data only inside the training set.

Artifacts:

```text
models/
  kepler_exominer_lite.keras
  kepler_exominer_lite_metadata.json
  kepler_exominer_lite_metrics.json

results/
  cnn_test_predictions.csv
```

The model does not use KepID, KOI name, catalog disposition, cross-match result, or
official vetting metrics as inputs.

## Evaluate

```powershell
python kepler_rf_detector.py cnn-evaluate
```

Evaluation reports accuracy, precision, recall, F1, ROC-AUC, Average Precision and
the confusion matrix.

The output score is support for the DR25 training class, **not**
`P(physical planet | data)`.

## Architecture

```text
global view ----- Conv1D ---\
local view ------ Conv1D ----\
odd view -------- Conv1D -----\
even view ------- Conv1D ------> concat -> dense -> P(CANDIDATE label)
secondary view -- Conv1D -----/
25 scalars ------ dense ------/
```

The graph is intentionally compact for the current dataset size. It is not the full
ExoMiner++ network.

## Scientific guardrails

1. Pipeline 2.0 remains unchanged; this CNN is not used by `scan` or
   `priority_score`.
2. The frozen blind benchmark must not be retuned after seeing results.
3. Any future RF+CNN ensemble must be evaluated on held-out stars.
4. A classifier cannot recover a signal that BLS never detected.
5. Before claiming scientific improvement, test blind-search negatives,
   injection/recovery, out-of-distribution cases, and calibration.
6. Preserve model, metadata, dataset/config hashes and dependency versions.

## Relationship to NASA ExoMiner

NASA's current public ExoMiner pipeline is primarily focused on TESS and its shipped
ExoMiner++ models expect richer SPOC-derived inputs such as centroid views, weak
secondaries, periodograms, stellar parameters and difference images.

This experiment intentionally starts only with information already derivable from
the existing Kepler pipeline. If it shows a real held-out gain, later experiments
can add centroid, periodogram, stellar or pixel/difference-image branches one at a
time and measure their incremental value.
