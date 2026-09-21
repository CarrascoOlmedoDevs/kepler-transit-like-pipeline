# Frozen blind end-to-end benchmark — Pipeline 2.0

Evaluation date: 2026-09-21. Author: Javier Carrasco.

The frozen cohort contains 92 TEST stars and 123 KOI signals with
`koi_pdisposition=CANDIDATE`. The scanner received KepID and a catalog-free cached
light curve. Catalog ephemerides were used only after scanning for evaluation.
KIC 10854555 was evaluated separately as a regression control.

## Results

| Metric | N / denominator | Percent |
|---|---:|---:|
| Fundamental recovery in raw detections | 75/123 | 61.0% |
| Fundamental recovery in candidate families | 73/123 | 59.3% |
| Fundamental + harmonic raw recovery | 77/123 | 62.6% |
| Fundamental Top 1 | 49/123 | 39.8% |
| Fundamental Top 3 | 72/123 | 58.5% |
| Fundamental Top 5 | 73/123 | 59.3% |
| Stars with at least one recovered KOI | 57/92 | 62.0% |
| RF positive conditional on fundamental BLS recovery | 60/75 | 80.0% |

Median recovered fundamental rank: 1. Mean: 1.452. MRR over all 123 KOIs: 0.485.
The fixed scanner limit is five detections per star, so Top 10 equals Top 5 by
construction. There were 407 raw detections and 386 candidate families.

The two harmonic-only recoveries correspond to catalog periods above 300 days and
were recovered at approximately P/2. The local frozen KOI catalog did not contain
`koi_depth`, so the depth analysis is explicitly unavailable rather than replaced
with detector-derived depth.

## Integrity and interpretation

The source, model, feature dataset, catalog, cached curves and the first 22 completed
outputs passed hash verification. Four scans were repeated only after documented
external interruption; their original partial artifacts were archived locally.
One-to-one matching and separation of BLS, family ranking and RF metrics passed an
independent audit.

Known Pipeline 2.0 issue: a possible resonance/harmonic family collision can remove
a raw fundamental match at family assignment. This behavior was retained for the
entire frozen evaluation and is visible for K01563.04 and K02748.02. It was not tuned
after observing results.

An unmatched family means only that no eligible period-and-phase match exists to the
frozen reference KOIs. It is not automatically a false positive, a discovery, or a
validated astrophysical object.

See `CONTINUATION_PROTOCOL.md`, `evaluation_manifest.json`, `summary.json`, the CSV
tables and figures in this directory for reproducible definitions and detailed data.
