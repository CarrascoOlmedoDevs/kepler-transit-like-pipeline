"""Conservative review labels for the local panel; never a validation claim."""
import math
from .vetting import consistency_flags


SEVERE_FLAGS = {
    'odd-even', 'secondary-eclipse', 'phase-0.5-anomaly',
    'poor-transit-coverage', 'single-event-dominated',
    'duration-at-boundary', 'harmonic-duplicate',
    'inconsistent-depths', 'negative-transit', 'below-noise-floor',
}


def _number(row, name, default=0.0):
    try:
        value = float(row.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def assess_candidate(row):
    """Return an explainable, deliberately strict follow-up priority."""
    rf = _number(row, 'rf_probability')
    cnn = _number(row, 'cnn_probability')
    priority = _number(row, 'priority_score')
    snr = _number(row, 'depth_snr')
    transits = int(_number(row, 'number_of_transits'))
    consistency = _number(row, 'transit_depth_consistency')
    flags = set(str(row.get('vetting_flags') or '').split('|')) - {''}
    # Resultados anteriores a la v2.1 no traen estas alertas: se recalculan de sus tránsitos.
    flags.update(consistency_flags(row))
    blockers = sorted(flags & SEVERE_FLAGS)

    # Modelos entrenados con Kepler aplicados a K2/TESS: sus probabilidades no bastan para "prioritario".
    calibrated = str(row.get('model_calibrated', 'True')).strip().lower() not in ('false', '0')
    # Sin CNN propia de la misión (TESS con RF propio), el RF solo debe ser más exigente.
    cnn_calibrated = str(row.get('cnn_calibrated', 'True')).strip().lower() not in ('false', '0')
    models_agree = (rf >= 0.65 and cnn >= 0.60) if cnn_calibrated else rf >= 0.80
    serious = (
        calibrated and models_agree and priority >= 35
        and snr >= 10 and transits >= 3 and consistency >= 0.50
        and not blockers
    )
    review = (
        not serious and not blockers and snr >= 8 and transits >= 3
        and priority >= 25 and (rf >= 0.50 or cnn >= 0.50)
    )
    if serious:
        label = 'prioritario'
        reason = ('RF y CNN altos; señal repetida, consistente y sin alertas graves' if cnn_calibrated else
                  'RF propio de la misión muy alto; señal repetida, consistente y sin alertas graves')
    elif review:
        label = 'revisar'
        reason = ('señal sin alertas graves; modelo Kepler no calibrado para esta misión, revisar a mano'
                  if not calibrated else 'señal moderada sin alertas graves; requiere inspección manual')
    elif blockers:
        label = 'descartable'
        reason = 'alertas: ' + ', '.join(blockers)
    else:
        label = 'débil'
        reason = 'no alcanza los umbrales conservadores de revisión'
    return {
        'triage_label': label,
        'triage_serious': serious,
        'triage_review': review,
        'triage_reason': reason,
    }
