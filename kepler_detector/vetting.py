import math
import numpy as np


def finite(feature, name, default=0.0):
    value = float(feature.get(name, default))
    return value if math.isfinite(value) else default


def vet(feature, min_snr=7.1, min_transits=2):
    flags = []
    if finite(feature, 'odd_even_sigma') > 3:
        flags.append('odd-even')
    secondary_snr = finite(feature, 'secondary_snr')
    secondary_ratio = finite(feature, 'secondary_depth_ratio')
    secondary_abs_snr = abs(secondary_snr)
    secondary_eclipse = secondary_snr > 3 and secondary_ratio > 0.1
    secondary_brightening = secondary_snr < -3 and abs(secondary_ratio) > 0.1
    phase_anomaly = secondary_abs_snr > 3 and abs(secondary_ratio) > 0.1
    if secondary_eclipse:
        flags.append('secondary-eclipse')
    if secondary_brightening:
        flags.append('secondary-brightening')
    if phase_anomaly:
        flags.append('phase-0.5-anomaly')
    if finite(feature, 'double_period_score') > 1.15:
        flags.append('double-period-preferred')
    depth = finite(feature, 'depth_ppm')
    if depth > 0 and finite(feature, 'half_period_depth') / depth > 0.8 and finite(feature, 'secondary_snr') > 3:
        flags.append('half-period-consistent')
    if finite(feature, 'depth_snr') < min_snr:
        flags.append('low-snr')
    if finite(feature, 'number_of_transits') < min_transits:
        flags.append('few-transits')
    if bool(feature.get('duration_at_boundary', False)):
        flags.append('duration-at-boundary')
    if finite(feature, 'transit_coverage', 1) < 0.5 or finite(feature, 'phase_coverage', 1) < 0.5:
        flags.append('poor-transit-coverage')
    if finite(feature, 'max_single_transit_contribution') > 0.5:
        flags.append('single-event-dominated')
    if not math.isfinite(float(feature.get('odd_even_sigma', float('nan')))):
        flags.append('odd-even-unavailable')
    if not math.isfinite(float(feature.get('secondary_snr', float('nan')))):
        flags.append('secondary-unavailable')
    fp = any(f in flags for f in ('odd-even', 'secondary-eclipse'))
    status = 'false-positive-like' if fp else ('low-confidence candidate' if flags else 'interesting candidate')
    return {'vetting_flags': '|'.join(flags), 'classification': status,
            'secondary_eclipse': secondary_eclipse,
            'secondary_brightening': secondary_brightening,
            'phase_0_5_anomaly': phase_anomaly}


def priority_score(feature, rf_probability, flags=None):
    """Heurística observacional [0,100]; RF se informa aparte y no entra en el cálculo."""
    snr = np.clip(finite(feature, 'depth_snr') / 20, 0, 1)
    events = np.clip(finite(feature, 'number_of_transits') / 3, 0, 1)
    fit = np.sqrt(np.clip(finite(feature, 'fit_improvement'), 0, 1))
    consistency = np.clip(finite(feature, 'transit_depth_consistency', 0.5), 0, 1)
    active = set((flags or vet(feature)['vetting_flags']).split('|')) - {''}
    penalties = {
        'odd-even': 0.35,
        'secondary-eclipse': 0.35,
        'phase-0.5-anomaly': 0.60,
        'duration-at-boundary': 0.60,
        'poor-transit-coverage': 0.70,
        'harmonic-duplicate': 0.65,
        'single-event-dominated': 0.65,
    }
    penalty = math.prod(penalties[name] for name in active if name in penalties)
    base = 100 * (0.45 * snr + 0.20 * events + 0.15 * fit + 0.20 * consistency)
    return float(np.clip(base * penalty, 0, 100))
