"""Post-scan evaluation only. Never imported by the scientific detector."""
import math
import numpy as np
import pandas as pd

PERIOD_TOLERANCE_PERCENT = 1.0
MIN_EPOCH_TOLERANCE_DAYS = 2 * 1765.5 / 86400
HARMONICS = [1 / n for n in range(8, 1, -1)] + list(range(2, 9))


def match_signal(koi, detection):
    p, epoch = float(koi['koi_period']), float(koi['koi_epoch'])
    detected, detected_epoch = float(detection['period_days']), float(detection['epoch_bkjd'])
    if not all(math.isfinite(x) for x in (p, epoch, detected, detected_epoch)) or min(p, detected) <= 0:
        return None
    duration = float(koi['koi_duration']) / 24  # catalog hours -> days
    window = max(1.5 * duration if math.isfinite(duration) and duration > 0 else 0,
                 MIN_EPOCH_TOLERANCE_DAYS)
    for ratio in [1.0, *HARMONICS]:
        error = abs(detected - ratio * p) / (ratio * p) * 100
        if error > PERIOD_TOLERANCE_PERCENT + 1e-12:
            continue
        # Long harmonics must coincide with a KOI event; short harmonics with
        # a subcycle of the KOI ephemeris. No catalog-dependent refitting.
        modulus = p * min(1.0, ratio)
        delta = abs((detected_epoch - epoch + modulus / 2) % modulus - modulus / 2)
        if delta <= window + 1e-12:
            return dict(match_type='fundamental' if ratio == 1 else 'harmonic',
                        harmonic_ratio=ratio, period_error_percent=abs(detected - p) / p * 100,
                        harmonic_period_error_percent=error, epoch_error=delta,
                        epoch_tolerance_days=window, epoch_modulus_days=modulus)
    return None


def matching_edges(known, raw):
    edges = []
    for koi in known.to_dict('records'):
        for detection in raw.to_dict('records'):
            match = match_signal(koi, detection)
            if match:
                edges.append(dict(kepoi_name=koi['kepoi_name'],
                                  detection_order=int(detection['detection_order']),
                                  family_id=detection['family_id'], **match))
    return edges


def assign(edges, resource):
    """Deterministic greedy one-to-one matching; fundamental edges first.

    A KOI with any valid fundamental edge cannot be reclassified as harmonic
    because another KOI took its resource: it is an assignment conflict.
    """
    fundamental = {e['kepoi_name'] for e in edges if e['match_type'] == 'fundamental'}
    ordered = sorted(edges, key=lambda e: (e['match_type'] != 'fundamental',
        e['harmonic_period_error_percent'], e['epoch_error'] / e['epoch_tolerance_days'],
        e['kepoi_name'], e['detection_order']))
    used, selected = set(), {}
    for edge in ordered:
        name = edge['kepoi_name']
        if name in selected or edge[resource] in used:
            continue
        if edge['match_type'] == 'harmonic' and name in fundamental:
            continue
        selected[name] = edge
        used.add(edge[resource])
    return selected


def evaluate_star(known, raw, families, status, seconds, error=''):
    edges = matching_edges(known, raw)
    raw_matches = assign(edges, 'detection_order')
    # Do not promote a raw-stage miss into a family-stage recovery.
    family_edges = [e for e in edges if e['kepoi_name'] in raw_matches and
                    e['match_type'] == raw_matches[e['kepoi_name']]['match_type']]
    family_matches = assign(family_edges, 'family_id')
    matched_families = {e['family_id'] for e in edges}  # potential matches, even conflicts
    family_map = {r['family_id']: r for r in families.to_dict('records')}
    detection_map = {int(r['detection_order']): r for r in raw.to_dict('records')}
    rows = []
    for koi in known.to_dict('records'):
        name = koi['kepoi_name']
        edge = raw_matches.get(name)
        family_edge = family_matches.get(name)
        detection = detection_map[edge['detection_order']] if edge else {}
        family = family_map[family_edge['family_id']] if family_edge else {}
        rank = family.get('rank', np.nan)
        probability = detection.get('rf_probability', np.nan)
        row = {**koi, 'scan_status': status, 'scan_error': error,
               'recovered_raw': bool(edge), 'recovered_family': bool(family_edge),
               'recovered_fundamental': bool(edge and edge['match_type'] == 'fundamental'),
               'recovered_harmonic': bool(edge and edge['match_type'] == 'harmonic'),
               'detected_period': detection.get('period_days', np.nan),
               'detected_epoch': detection.get('epoch_bkjd', np.nan),
               'detection_order': detection.get('detection_order', np.nan),
               'period_error_percent': np.nan, 'epoch_error': np.nan, 'harmonic_ratio': np.nan,
               'family_id': family.get('family_id', ''), 'raw_family_id': detection.get('family_id', ''),
               'rank': rank, 'priority_score': family.get('priority_score', np.nan),
               'raw_priority_score': detection.get('priority_score', np.nan),
               'rf_probability': probability,
               'rf_prediction': int(probability >= 0.5) if math.isfinite(probability) else np.nan,
               'number_raw_detections': len(raw) if status == 'complete' else np.nan,
               'number_candidate_families': len(families) if status == 'complete' else np.nan,
               'match_type': 'none', 'family_match_type': family_edge['match_type'] if family_edge else 'none',
               'family_detected_period': detection_map[family_edge['detection_order']]['period_days'] if family_edge else np.nan,
               'family_detection_order': family_edge['detection_order'] if family_edge else np.nan,
               'family_primary_period': family.get('period_days', np.nan),
               'number_of_families_ranked_above_true_signal': rank - 1 if math.isfinite(rank) else np.nan,
               'number_unmatched_families_above': sum(
                   r['rank'] < rank and r['family_id'] not in matched_families
                   for r in family_map.values()) if math.isfinite(rank) else np.nan,
               'eligible_match_count': sum(e['kepoi_name'] == name for e in edges),
               'raw_assignment_conflict': not edge and any(e['kepoi_name'] == name for e in edges),
               'family_assignment_conflict': bool(edge and not family_edge)}
        if edge:
            row.update({k: v for k, v in edge.items() if k not in ('family_id', 'kepoi_name')})
        rows.append(row)
    result = pd.DataFrame(rows)
    recovered = int(result.recovered_raw.sum())
    star = dict(kepid=int(known.iloc[0].kepid), number_known_koi=len(known),
                number_raw_detections=len(raw) if status == 'complete' else None,
                number_candidate_families=len(families) if status == 'complete' else None,
                number_unmatched_families=len(families) - len(matched_families) if status == 'complete' else None,
                number_recovered_koi=recovered, any_recovered=recovered > 0,
                any_fundamental=bool(result.recovered_fundamental.any()),
                any_top_5=bool((result['rank'] <= 5).any()), any_top_1=bool((result['rank'] <= 1).any()),
                multiplanet=len(known) > 1,
                multiplanet_recovery=('not_multiplanet' if len(known) == 1 else
                                     'all' if recovered == len(known) else 'partial' if recovered else 'none'),
                all_fundamental=bool(result.recovered_fundamental.all()),
                seconds=seconds, scan_status=status, scan_error=error)
    for e in edges:
        e.update(kepid=star['kepid'], selected_raw=raw_matches.get(e['kepoi_name']) == e,
                 selected_family=family_matches.get(e['kepoi_name']) == e)
    return result, star, edges


def rate(n, denominator):
    return dict(n=int(n), denominator=int(denominator), percent=100 * n / denominator if denominator else None)


def summarize(koi, stars, runtime):
    total, nstars = len(koi), len(stars)
    fundamental, harmonic = koi.recovered_fundamental, koi.recovered_harmonic
    ranks = koi['rank'].dropna()
    fundamental_ranks = koi.loc[fundamental, 'rank'].dropna()
    def median_or_none(values):
        return float(values.median()) if values.notna().any() else None
    def rank_stats(values, denominator):
        return dict(median=float(values.median()) if len(values) else None,
                    mean=float(values.mean()) if len(values) else None,
                    mrr_all_koi=float((1 / values).sum() / denominator),
                    mrr_recovered=float((1 / values).mean()) if len(values) else None,
                    distribution={label: int(((values >= lo) & (values <= hi)).sum()) for label, lo, hi in
                                  [('1', 1, 1), ('2-3', 2, 3), ('4-5', 4, 5), ('6-10', 6, 10), ('>10', 11, np.inf)]})
    metrics = dict(total_koi=total, total_stars=nstars,
        fundamental_raw=rate(fundamental.sum(), total),
        fundamental_family=rate((fundamental & koi.recovered_family).sum(), total),
        harmonic_raw=rate(harmonic.sum(), total),
        fundamental_plus_harmonic_raw=rate(koi.recovered_raw.sum(), total),
        not_recovered=rate((~koi.recovered_raw).sum(), total),
        fundamental_top={str(k): rate((fundamental & (koi['rank'] <= k)).sum(), total) for k in (1, 3, 5, 10)},
        harmonic_top={str(k): rate((harmonic & (koi['rank'] <= k)).sum(), total) for k in (1, 3, 5, 10)},
        combined_top={str(k): rate((koi['rank'] <= k).sum(), total) for k in (1, 3, 5, 10)},
        per_star={key: rate(stars[key].sum(), nstars) for key in
                  ('any_recovered', 'any_fundamental', 'any_top_5', 'any_top_1')},
        multiplanet={key: rate((stars.multiplanet_recovery == key).sum(), stars.multiplanet.sum())
                     for key in ('all', 'partial', 'none')},
        rf_positive_given_bls_fundamental=rate((fundamental & (koi.rf_prediction == 1)).sum(), fundamental.sum()),
        rf_missing_given_bls_fundamental=int((fundamental & koi.rf_probability.isna()).sum()),
        rank_combined=rank_stats(ranks, total), rank_fundamental=rank_stats(fundamental_ranks, total),
        median_families_per_star=median_or_none(stars.number_candidate_families),
        median_unmatched_families_per_star=median_or_none(stars.number_unmatched_families),
        median_unmatched_above_recovered=float(koi.number_unmatched_families_above.median()) if len(ranks) else None,
        median_families_above_recovered=float(koi.number_of_families_ranked_above_true_signal.median()) if len(ranks) else None,
        completed_stars=int((stars.scan_status == 'complete').sum()),
        failed_stars=int((stars.scan_status != 'complete').sum()),
        runtime_total_seconds=float(runtime), runtime_mean_per_star_seconds=float(stars.seconds.mean()),
        runtime_sum_star_seconds=float(stars.seconds.sum()))
    return metrics
