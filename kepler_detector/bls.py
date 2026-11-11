"""BLS ciego/guiado, refinamiento adaptativo y familias de candidatos."""
from dataclasses import dataclass, asdict, field
import logging
import numpy as np
from scipy.signal import find_peaks
from astropy.timeseries import BoxLeastSquares


@dataclass
class Candidate:
    period: float
    duration: float
    epoch: float
    power: float
    log_likelihood: float
    depth: float
    snr: float
    duration_at_boundary: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class CandidateFamily:
    """Agrupa ajustes equivalentes y armónicos sin confundirlos con planetas."""
    family_id: str
    primary_period: float
    best_candidate: int
    detections: list[int] = field(default_factory=list)
    aliases: list[float] = field(default_factory=list)
    harmonics: list[dict] = field(default_factory=list)

    @property
    def n_detections(self):
        return len(self.detections)


def phase(time, period, epoch):
    return (np.asarray(time) - epoch + period / 2) % period - period / 2


def transit_mask(time, candidate, width=1.0):
    return np.abs(phase(time, candidate.period, candidate.epoch)) < candidate.duration * width / 2


def harmonic_info(period, reference_period, tolerance=0.01, max_order=8):
    """Devuelve (P/Pref ideal, error %) para relaciones N o 1/N."""
    if min(period, reference_period) <= 0:
        return None
    ratio = period / reference_period
    if ratio >= 1:
        order = int(round(ratio))
        expected = float(order)
    else:
        order = int(round(1 / ratio))
        expected = 1.0 / order if order else 0.0
    if not 2 <= order <= max_order:
        return None
    error = abs(ratio - expected) / expected
    return (expected, 100 * error) if error <= tolerance else None


def harmonic_relation(first, second, tolerance=0.01, max_order=8):
    return harmonic_info(float(first), float(second), tolerance, max_order) is not None


def event_overlap(first, second, time, width=1.5):
    a, b = transit_mask(time, first, width), transit_mask(time, second, width)
    return float(np.sum(a & b) / max(1, min(a.sum(), b.sum())))


def duplicate(first, second, time, tolerance=0.005):
    relative = abs(first.period - second.period) / min(first.period, second.period)
    if relative > tolerance:
        return False
    return event_overlap(first, second, time) >= 0.5


def deduplicate(candidates, time):
    kept = []
    for candidate in sorted(candidates, key=lambda c: c.power, reverse=True):
        if not any(duplicate(candidate, other, time) for other in kept):
            kept.append(candidate)
    return kept


def result_candidate(result, index):
    return Candidate(*(float(result[key][index]) for key in
                       ('period', 'duration', 'transit_time', 'power', 'log_likelihood', 'depth', 'depth_snr')))


SOLAR_DURATION_1YR = 13.0 / 24  # días: tránsito central de un planeta a 1 UA de una estrella tipo Sol


def max_duration(period, config):
    """Duración máxima físicamente plausible (días) para un período dado."""
    return config.max_duration_factor * SOLAR_DURATION_1YR * (float(period) / 365.25) ** (1 / 3)


def duration_limits(periods, curve, config):
    """Límites duros de la malla de duraciones: cadencia abajo; período, detrending y física arriba."""
    cadence = float(np.nanmedian(np.diff(curve.time)))
    lower = max(1.5 * cadence, min(config.durations) / 4)
    shortest = float(np.min(periods))
    upper = min(shortest * 0.25, config.flatten_window / 3, max_duration(shortest, config))
    return lower, max(upper, lower)


def _duration_grid(base, periods, curve, config, expand_low=False, expand_high=False):
    values = np.unique(np.asarray(base, dtype=float))
    lower_limit, upper_limit = duration_limits(periods, curve, config)
    values = np.clip(values, lower_limit, upper_limit)
    low, high = float(values[0]), float(values[-1])
    if expand_low and low > lower_limit * 1.01:
        low = max(lower_limit, low / 2)
    if expand_high and high < upper_limit * 0.99:
        high = min(upper_limit, high * 1.75)
    if high <= low:
        return values
    return np.geomspace(low, high, max(17, 5 * len(config.durations)))


def adaptive_duration_power(model, periods, curve, config, oversample=10, max_rounds=4):
    """Refina duración y expande el grid cuando el óptimo cae en un borde."""
    durations = _duration_grid(config.durations, periods, curve, config)
    candidate = None
    result = None
    for _ in range(max_rounds):
        result = model.power(periods, durations, objective='likelihood', oversample=oversample)
        candidate = result_candidate(result, np.nanargmax(result.power))
        at_low = np.isclose(candidate.duration, durations[0], rtol=0, atol=1e-10)
        at_high = np.isclose(candidate.duration, durations[-1], rtol=0, atol=1e-10)
        if not (at_low or at_high):
            break
        expanded = _duration_grid(durations, periods, curve, config, at_low, at_high)
        if len(expanded) == len(durations) and np.allclose(expanded, durations):
            break
        durations = expanded
    # Refinamiento continuo alrededor del óptimo para no privilegiar antiguos bordes.
    hard_low, hard_high = duration_limits(periods, curve, config)
    lower = max(hard_low, candidate.duration * 0.65)
    upper = min(hard_high, candidate.duration * 1.35)
    if upper > lower * 1.01:
        local_durations = np.linspace(lower, upper, 21)
        result = model.power(periods, local_durations, objective='likelihood', oversample=oversample)
        candidate = result_candidate(result, np.nanargmax(result.power))
    # Alerta sólo si el óptimo final queda en un límite duro (no se pudo ampliar más):
    # la señal "quiere" ser más larga de lo físicamente posible o más corta que la cadencia.
    # Que la malla inicial se quedara corta y se ampliara no es sospechoso (2.0 sí lo marcaba).
    # Tolerancia > un paso de la malla local (~2 %).
    candidate.duration_at_boundary = bool(candidate.duration >= 0.97 * hard_high or
                                          candidate.duration <= 1.03 * hard_low)
    return candidate, result


def search(curve, config, known_period=None):
    t, y, dy = curve.time, curve.flux, curve.error
    if len(t) < 50:
        raise ValueError('BLS requiere al menos 50 puntos')
    baseline = np.ptp(t)
    minimum, maximum = config.period_min, min(config.period_max, baseline / config.min_transits)
    model = BoxLeastSquares(t, y, dy)
    if known_period is not None:
        if not np.isfinite(known_period) or known_period <= max(config.durations):
            raise ValueError('Período KOI inválido para estas duraciones')
        minimum, maximum = known_period * 0.998, known_period * 1.002
        # Resolución que limita la deriva acumulada a ~D/4.
        step = min(config.durations) * known_period / (4 * baseline)
        periods = np.linspace(minimum, maximum, max(101, int((maximum - minimum) / step) + 1))
        candidate, result = adaptive_duration_power(model, periods, curve, config)
        return [candidate], result
    if maximum <= minimum:
        raise ValueError('Baseline demasiado corto para el rango BLS')
    # Espaciado relativo constante: igual deriva temporal acumulada a cualquier P.
    # Una malla lineal en frecuencia de tamaño fijo inframuestrea severamente P largos.
    periods = np.geomspace(minimum, maximum, config.coarse_points)
    log_step = np.log(periods[1] / periods[0])
    result = model.power(periods, config.durations, objective='likelihood', oversample=5)
    peak_indices = np.unique(np.r_[find_peaks(result.power)[0], np.nanargmax(result.power)])
    peak_indices = peak_indices[np.argsort(result.power[peak_indices])[::-1]]
    seeds = []
    # Exclusión local en frecuencia; armónicos se prueban explícitamente después.
    for index in peak_indices:
        p = float(periods[index])
        if not any(abs(np.log(p / s)) < 2 * log_step for s in seeds):
            seeds.append(p)
        if len(seeds) >= config.refine_peaks:
            break
    refined = []
    for seed in seeds:
        for factor in (1.0, 0.5, 2.0):
            p = seed * factor
            if not minimum <= p <= maximum:
                continue
            df = np.expm1(log_step) / p
            lo = max(1 / maximum, 1 / p - 1.5 * df)
            hi = min(1 / minimum, 1 / p + 1.5 * df)
            fine_df = min(config.durations) / (4 * baseline * p)
            n = max(101, int(np.ceil((hi - lo) / fine_df)) + 1)
            fine = 1 / np.linspace(lo, hi, n)[::-1]
            candidate, _ = adaptive_duration_power(model, fine, curve, config)
            refined.append(candidate)
    candidates = deduplicate(refined, t)
    logging.getLogger('kepler').debug('BLS: %d máximos independientes; baseline %.2f', len(candidates), baseline)
    return candidates, result
