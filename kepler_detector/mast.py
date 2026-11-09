"""Descarga Lightkurve/MAST, long cadence, PDCSAP, caché separada por cobertura."""
from dataclasses import dataclass
import json
import logging
import os
import numpy as np
from .utils import retry
from . import missions


@dataclass
class Curve:
    time: np.ndarray
    flux: np.ndarray
    error: np.ndarray
    segment: np.ndarray
    metadata: dict

    def subset(self, mask):
        return Curve(self.time[mask], self.flux[mask], self.error[mask], self.segment[mask], self.metadata.copy())


def save_curve(curve, path):
    temp = path.with_suffix('.tmp')
    with open(temp, 'wb') as stream:
        np.savez_compressed(stream, time=curve.time, flux=curve.flux, error=curve.error,
                            segment=curve.segment, metadata=json.dumps(curve.metadata))
    os.replace(temp, path)


def load_curve(path):
    with np.load(path, allow_pickle=False) as values:
        return Curve(*(values[k].copy() for k in ('time', 'flux', 'error', 'segment')),
                     json.loads(str(values['metadata'])))


def cache_path(root, target, segment=None, mission='kepler'):
    info = missions.get(mission)
    if info.key == 'kepler':  # Ruta histórica: conserva la caché Kepler existente.
        suffix = '' if segment is None else f'_Q{segment:02d}'
        return root / f'cache/lightcurves/KIC_{int(target):08d}{suffix}.npz'
    suffix = '' if segment is None else f'_{info.segment[0].upper()}{segment:03d}'
    return root / f'cache/lightcurves/{info.prefix}_{int(target):010d}{suffix}.npz'


def _search(info, target, segment):
    import lightkurve as lk
    for author, extra in info.searches:
        options = dict(extra)
        if segment is not None:
            options[info.segment] = segment
        found = retry(lambda: lk.search_lightcurve(info.label(target), mission=info.lk_mission,
                                                  author=author, **options))
        if len(found):
            return found, author
    return found, None


def download(root, kic, quarter=None, force=False, mission='kepler'):
    """Descarga y normaliza PDCSAP. `kic` es el identificador de la misión (KIC/EPIC/TIC)
    y `quarter` el segmento (quarter/campaign/sector). Tiempos en BJD-2454833 para todas."""
    info = missions.get(mission)
    if int(kic) <= 0:
        raise ValueError(f'{info.prefix} debe ser positivo')
    path = cache_path(root, kic, quarter, info.key)
    if path.exists() and not force:
        return load_curve(path)
    logger = logging.getLogger('kepler')
    logger.info('MAST: %s, %s %s', info.label(kic), info.segment, quarter if quarter is not None else 'todos')
    found, author = _search(info, kic, quarter)
    if len(found) == 0:
        raise ValueError(f'MAST no encontró curvas para {info.label(kic)}')
    pieces, sources = [], []
    # Descargas secuenciales por estrella; archivos FITS también quedan en caché LK.
    for index in range(len(found)):
        lc = retry(lambda: found[index].download(quality_bitmask='default',
                                                download_dir=str(root / 'cache/mast'), flux_column='pdcsap_flux'))
        if lc is None:
            raise RuntimeError(f'Producto {index} no descargado; no se guarda caché incompleta')
        flux = np.asarray(lc.flux.value, dtype=float)
        error = np.asarray(lc.flux_err.value, dtype=float)
        times = np.asarray(lc.time.tdb.jd, dtype=float) - 2454833.0
        valid = np.isfinite(times) & np.isfinite(flux) & (flux > 0)
        median = np.median(flux[valid])
        if valid.sum() < 20 or not np.isfinite(median) or median <= 0:
            raise ValueError(f'Producto {index} sin PDCSAP válido')
        flux, error = flux[valid] / median, error[valid] / median
        fallback = max(1e-7, np.nanmedian(np.abs(np.diff(flux))) / 0.954)
        error[~np.isfinite(error) | (error <= 0)] = fallback
        pieces.append((times[valid], flux, error, np.full(valid.sum(), index)))
        number = lc.meta.get(info.segment.upper())
        sources.append({info.segment: int(number) if number is not None else None,
                        'filename': str(lc.meta.get('FILENAME', '')), 'points': int(valid.sum())})
    arrays = [np.concatenate([p[i] for p in pieces]) for i in range(4)]
    order = np.argsort(arrays[0])
    arrays = [a[order] for a in arrays]
    unique = np.r_[True, np.diff(arrays[0]) > 1e-8]
    metadata = {'kic': int(kic), 'time_format': 'BKJD',
                'time_scale': 'TDB', 'bjd_offset': 2454833.0, 'flux': 'PDCSAP_FLUX',
                'quality_bitmask': 'default', 'products': sources, 'quarter_requested': quarter}
    if info.key != 'kepler':
        # 'kic' se mantiene por compatibilidad interna; el identificador real es target.
        metadata.update(mission=info.key, target=info.label(kic), author=author,
                        segment_requested=quarter)
    curve = Curve(*(a[unique] for a in arrays), metadata)
    save_curve(curve, path)
    return curve
