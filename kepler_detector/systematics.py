"""Días malos de Kepler: fechas en que muchas estrellas muestran a la vez un "tránsito".

Un planeta tiene fechas propias. Un fallo del instrumento (reinicio tras modo seguro, cambio
térmico, descarga de datos) aparece el mismo día en estrellas sin relación. Las fechas se deducen
de las propias señales de período largo analizadas, que son las más sensibles a estos eventos."""
import math

import numpy as np

KEPLER_START, KEPLER_END = 120.0, 1592.0
WINDOW_DAYS = 0.5
MIN_PERIOD = 100.0
MAX_TRANSITS = 8
MIN_EVENTS = 6           # nunca se marca un día con menos estrellas que esto
FALSE_ALARM = 1e-3       # probabilidad por día de superar el umbral por azar


def _float(row, key):
    try:
        value = float(row.get(key))
    except (TypeError, ValueError):
        return math.nan
    return value


def transit_times(period, epoch, start=KEPLER_START, end=KEPLER_END):
    if not (math.isfinite(period) and math.isfinite(epoch) and period > 0):
        return []
    first = math.ceil((start - epoch) / period)
    times = []
    k = first
    while epoch + k * period <= end and len(times) < 2000:
        times.append(epoch + k * period)
        k += 1
    return times


def _poisson_threshold(rate):
    """Menor k con P(X >= k) < FALSE_ALARM para X ~ Poisson(rate)."""
    k, term, tail = 0, math.exp(-rate), 1.0
    while tail >= FALSE_ALARM and k < 200:
        tail -= term
        k += 1
        term *= rate / k
    return max(MIN_EVENTS, k)


def bad_days(stars):
    """stars: {kic: [filas de candidates.csv]} → dict(days, rate, threshold, stars, events)."""
    counts = np.zeros(int(KEPLER_END - KEPLER_START) + 1, dtype=int)
    contributing = set()
    for kic, rows in stars.items():
        days = set()  # una estrella cuenta como mucho una vez por día
        for row in rows:
            period, transits = _float(row, 'period_days'), _float(row, 'number_of_transits')
            if not (period > MIN_PERIOD and 0 < transits <= MAX_TRANSITS):
                continue
            for t in transit_times(period, _float(row, 'epoch_bkjd')):
                days.add(int(t - KEPLER_START))
        for day in days:
            counts[day] += 1
        if days:
            contributing.add(kic)
    total = int(counts.sum())
    rate = total / len(counts)
    threshold = _poisson_threshold(rate)
    marked = [round(KEPLER_START + i + 0.5, 1) for i in np.flatnonzero(counts >= threshold)]
    return dict(days=marked, rate=round(rate, 3), threshold=int(threshold),
                stars=len(contributing), events=total)


def epochs_on_bad_days(row, days, window=WINDOW_DAYS):
    """Cuántos tránsitos de la señal caen a menos de `window` (+ medio día de cajón) de un día malo."""
    if not days:
        return 0
    marks = np.asarray(days, dtype=float)
    hits = 0
    for t in transit_times(_float(row, 'period_days'), _float(row, 'epoch_bkjd')):
        if np.min(np.abs(marks - t)) <= window + 0.5:
            hits += 1
    return hits
