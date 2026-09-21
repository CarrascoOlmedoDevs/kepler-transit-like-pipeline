"""Medidas compartidas por build/scan y diagnósticos de consistencia temporal."""
import json
import numpy as np
from astropy.timeseries import BoxLeastSquares
from .bls import phase, transit_mask
from .utils import robust_std

from .schema import RF_FEATURE_COLUMNS

MODEL_FEATURES = RF_FEATURE_COLUMNS

DIAGNOSTIC_FEATURES = ['secondary_abs_snr', 'duration_at_boundary']
FEATURES = MODEL_FEATURES + DIAGNOSTIC_FEATURES

CONSISTENCY_FEATURES = [
    'individual_transit_depths', 'individual_transit_snr', 'median_transit_depth',
    'transit_depth_mad', 'transit_depth_consistency', 'quarters_with_transits',
    'number_of_independent_quarters', 'max_single_transit_contribution',
    'jackknife_min_snr_fraction',
]


def weighted_mean(y, error):
    if len(y) < 2:
        return float('nan'), float('nan')
    weights = 1 / error ** 2
    mean = np.average(y, weights=weights)
    # Conservador frente a exceso de dispersión dentro de la ventana.
    variance = max(1 / weights.sum(), np.var(y) / len(y))
    return float(mean), float(np.sqrt(variance))


def extract(curve, candidate):
    t, y, error = curve.time, curve.flux, curve.error
    p, d, epoch = candidate.period, candidate.duration, candidate.epoch
    if not 0 < d < p or len(t) < 20:
        raise ValueError('Candidato/curva no válido para extracción')
    ph = phase(t, p, epoch)
    inside = transit_mask(t, candidate)
    secondary_phase = phase(t, p, epoch + p / 2)
    secondary = np.abs(secondary_phase) < d / 2
    outside = (~transit_mask(t, candidate, 1.5)) & (np.abs(secondary_phase) > d * 0.75)
    if inside.sum() < 2 or outside.sum() < 10:
        raise ValueError('Muestreo insuficiente del tránsito/fuera de tránsito')
    # Piso de ruido robusto reestimado, compartido por todas las medidas de profundidad.
    errors = np.maximum(error, robust_std(y[outside]))
    base, base_err = weighted_mean(y[outside], errors[outside])

    def depth(mask):
        avg, err = weighted_mean(y[mask], errors[mask])
        return base - avg, np.hypot(base_err, err)

    dep, dep_err = depth(inside)
    events = np.floor((t - epoch) / p + 0.5).astype(int)
    odd, odd_err = depth(inside & (events % 2 != 0))
    even, even_err = depth(inside & (events % 2 == 0))
    sec, sec_err = depth(secondary)
    left, left_err = depth(inside & (ph < 0))
    right, right_err = depth(inside & (ph >= 0))
    asymmetry = abs(left - right) / np.hypot(left_err, right_err)
    _, counts = np.unique(events[inside], return_counts=True)
    observed_events = int(np.sum(counts >= 2))
    expected = max(1, int(np.floor((t.max() - epoch) / p) - np.ceil((t.min() - epoch) / p) + 1))
    half_mask = np.abs(phase(t, p / 2, epoch)) < min(d, p / 4) / 2
    half_depth, _ = depth(half_mask)
    model = BoxLeastSquares(t, y, errors)
    # Misma curva, ruido y duración para comparar armónicos.
    powers = model.power([p, 2 * p], [d], objective='likelihood')
    double_score = float(powers.power[1] / max(powers.power[0], 1e-12))
    predicted = base - dep * inside
    chi_flat = np.sum(((y - base) / errors) ** 2)
    chi_model = np.sum(((y - predicted) / errors) ** 2)
    secondary_snr = sec / sec_err
    values = [p, 24 * d, dep * 1e6, dep / dep_err, candidate.power,
              candidate.log_likelihood, observed_events, d / p, odd * 1e6, even * 1e6,
              abs(odd - even) / np.hypot(odd_err, even_err), sec * 1e6, secondary_snr,
              sec / dep if dep > 0 else np.nan, half_depth * 1e6, double_score,
              np.std(y[outside]) * 1e6, np.std(y[inside]) * 1e6,
              1 / (1 + asymmetry), np.ptp(t), len(t), inside.sum(),
              np.count_nonzero(np.histogram(ph / p, bins=100, range=(-0.5, 0.5))[0]) / 100,
              min(1, observed_events / expected), max(0, (chi_flat - chi_model) / max(chi_flat, 1e-12)),
              abs(secondary_snr), bool(candidate.duration_at_boundary)]
    return {name: (bool(value) if name == 'duration_at_boundary' else
                   float(value) if np.isfinite(value) else float('nan'))
            for name, value in zip(FEATURES, values)}


def transit_consistency(curve, candidate):
    """Mide profundidades por evento y sensibilidad jackknife sin alterar la curva."""
    t, y, error = curve.time, curve.flux, curve.error
    p, d, epoch = candidate.period, candidate.duration, candidate.epoch
    ph = phase(t, p, epoch)
    inside = np.abs(ph) < d / 2
    events = np.floor((t - epoch) / p + 0.5).astype(int)
    broad_outside = np.abs(ph) > 1.5 * d
    global_base = float(np.median(y[broad_outside]))
    noise = max(float(np.median(error)), robust_std(y[broad_outside]))
    depths, snrs, quarters = [], [], set()
    for event in np.unique(events[inside]):
        event_inside = inside & (events == event)
        if event_inside.sum() < 2:
            continue
        center = epoch + event * p
        local_distance = np.abs(t - center)
        local_outside = (local_distance >= d) & (local_distance <= 3 * d)
        base = float(np.median(y[local_outside])) if local_outside.sum() >= 4 else global_base
        depth = base - float(np.mean(y[event_inside]))
        uncertainty = max(noise / np.sqrt(event_inside.sum()), 1e-12)
        depths.append(depth * 1e6)
        snrs.append(depth / uncertainty)
        quarters.update(int(value) for value in np.unique(curve.segment[event_inside]))
    depths = np.asarray(depths, dtype=float)
    snrs = np.asarray(snrs, dtype=float)
    if len(depths):
        median = float(np.median(depths))
        mad = float(np.median(np.abs(depths - median)))
        positive_fraction = float(np.mean(depths > 0))
        consistency = positive_fraction / (1 + mad / max(abs(median), 1.0))
        weights = np.square(np.clip(snrs, 0, None))
        max_contribution = float(weights.max() / weights.sum()) if weights.sum() else 1.0
        total_snr = float(np.sqrt(weights.sum()))
        if len(weights) > 1 and total_snr > 0:
            jackknife = np.sqrt(np.maximum(0, weights.sum() - weights)) / total_snr
            jackknife_fraction = float(jackknife.min())
        else:
            jackknife_fraction = 0.0
    else:
        median = mad = consistency = float('nan')
        max_contribution, jackknife_fraction = 1.0, 0.0
    clean_depths = [round(float(value), 6) for value in depths]
    clean_snrs = [round(float(value), 6) for value in snrs]
    return {
        'individual_transit_depths': json.dumps(clean_depths, separators=(',', ':')),
        'individual_transit_snr': json.dumps(clean_snrs, separators=(',', ':')),
        'median_transit_depth': median,
        'transit_depth_mad': mad,
        'transit_depth_consistency': consistency,
        'quarters_with_transits': json.dumps(sorted(quarters), separators=(',', ':')),
        'number_of_independent_quarters': len(quarters),
        'max_single_transit_contribution': max_contribution,
        'jackknife_min_snr_fraction': jackknife_fraction,
    }


def binned_views(curve, candidate, global_bins=2001, local_bins=201):
    """Entrada futura para CNN: medias + cuentas; bins vacíos son NaN, no ceros."""
    ph = phase(curve.time, candidate.period, candidate.epoch)
    result = {}
    for name, span, bins in [('global', candidate.period / 2, global_bins),
                             ('local', min(2.5 * candidate.duration, candidate.period / 2), local_bins)]:
        edges = np.linspace(-span, span, bins + 1)
        count, _ = np.histogram(ph, edges)
        total, _ = np.histogram(ph, edges, weights=curve.flux)
        mean = np.divide(total, count, out=np.full(bins, np.nan), where=count > 0)
        result[name] = {'time': (edges[:-1] + edges[1:]) / 2, 'flux': mean, 'count': count}
    return result
