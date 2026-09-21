import numpy as np


def _column(frame, *names):
    return next((name for name in names if name in frame.columns), None)


def match(kic, period, catalog, tolerance=0.01, tce_catalog=None):
    result = {'known_koi': False, 'matched_koi_name': '', 'known_period': np.nan,
              'period_difference_percent': np.nan, 'known_disposition': '',
              'known_tce': False, 'matched_tce_name': '', 'known_tce_period': np.nan,
              'known_tce_disposition': '', 'previously_known_false_alarm': False,
              'catalog_match_status': 'completely unmatched signal'}
    if catalog is not None:
        rows = catalog[(catalog.kepid == int(kic)) & (catalog.koi_period > 0)].copy()
        if not rows.empty:
            difference = abs(rows.koi_period - period) / rows.koi_period
            index = difference.idxmin()
            if difference.loc[index] <= tolerance:
                row = rows.loc[index]
                disposition = str(row.koi_disposition)
                false_alarm = 'FALSE' in disposition.upper()
                result.update(known_koi=True, matched_koi_name=str(row.kepoi_name),
                              known_period=float(row.koi_period),
                              period_difference_percent=float(100 * difference.loc[index]),
                              known_disposition=disposition,
                              previously_known_false_alarm=false_alarm,
                              catalog_match_status=('previously known false alarm' if false_alarm
                                                    else 'known KOI'))
    if tce_catalog is not None and not tce_catalog.empty:
        kic_col = _column(tce_catalog, 'kepid', 'kepid_int', 'kic')
        period_col = _column(tce_catalog, 'tce_period', 'period', 'orbital_period')
        if kic_col and period_col:
            rows = tce_catalog[(pd_to_number(tce_catalog[kic_col]) == int(kic)) &
                               (pd_to_number(tce_catalog[period_col]) > 0)].copy()
            if not rows.empty:
                periods = pd_to_number(rows[period_col])
                difference = abs(periods - period) / periods
                index = difference.idxmin()
                if difference.loc[index] <= tolerance:
                    name_col = _column(rows, 'tce_id', 'tce_name', 'epic_name')
                    disposition_col = _column(rows, 'tce_disposition', 'disposition', 'av_training_set')
                    disposition = str(rows.loc[index, disposition_col]) if disposition_col else ''
                    false_alarm = any(token in disposition.upper() for token in ('FALSE', 'FP', 'NOT TRANSIT'))
                    result.update(known_tce=True,
                                  matched_tce_name=str(rows.loc[index, name_col]) if name_col else '',
                                  known_tce_period=float(periods.loc[index]),
                                  known_tce_disposition=disposition,
                                  previously_known_false_alarm=(result['previously_known_false_alarm'] or false_alarm))
                    if not result['known_koi']:
                        result['catalog_match_status'] = ('previously known false alarm' if false_alarm
                                                          else 'known TCE')
    if catalog is None and tce_catalog is None:
        result['catalog_match_status'] = 'catalogs unavailable'
    return result


def pd_to_number(series):
    import pandas as pd
    return pd.to_numeric(series, errors='coerce')
