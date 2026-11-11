"""Examen a nivel de píxel y vecinos de Gaia para un candidato.

1) Imagen diferencia (fuera - dentro del tránsito) en cada tránsito: si la luz que falta
   sale del centro de la estrella, la señal es suya; si sale desplazada, es de otra.
   El error se mide con "tránsitos falsos" en épocas sin tránsito (ruido real de esos píxeles).
2) Vecinos en Gaia DR3 hasta 30": qué profundidad de eclipse necesitaría cada uno.

Uso: python examen_pixeles.py [KIC] [periodo_d] [epoca_bkjd] [duracion_h]
Resultados en results/seguimiento/KIC_<kic>/pixeles/."""
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
DEFAULT = (6881353, 233.9407736958617, 349.9347535910972, 12.636)
DEPTH_PPM = 60.4
FAKE_EPOCHS = 40
warnings.filterwarnings('ignore')


def epochs_in(t, period, epoch, dur):
    first = int(np.floor((t.min() - epoch) / period)) - 1
    last = int(np.ceil((t.max() - epoch) / period)) + 1
    result = []
    for k in range(first, last + 1):
        e = epoch + k * period
        inside = np.abs(t - e) < dur / 2
        if inside.sum() >= 5:
            result.append(e)
    return result


def difference_image(t, cube, e, dur):
    """(imagen media fuera, imagen diferencia fuera-dentro) con tendencia lineal por píxel."""
    dt = t - e
    inside = np.abs(dt) < dur / 2
    outside = (np.abs(dt) > 0.75 * dur) & (np.abs(dt) < 3 * dur)
    if inside.sum() < 5 or outside.sum() < 10:
        return None
    flat = cube.reshape(len(t), -1)
    good = np.all(np.isfinite(flat[inside | outside]), axis=0)
    diff = np.full(flat.shape[1], np.nan)
    mean = np.full(flat.shape[1], np.nan)
    design = np.vstack([np.ones(outside.sum()), dt[outside]]).T
    coef, *_ = np.linalg.lstsq(design, flat[outside][:, good], rcond=None)
    model_in = coef[0] + np.outer(dt[inside], coef[1])
    diff[good] = (model_in - flat[inside][:, good]).mean(axis=0)
    mean[good] = coef[0]
    shape = cube.shape[1:]
    return mean.reshape(shape), diff.reshape(shape)


def robust(values):
    values = np.asarray(values)
    return float(1.4826 * np.median(np.abs(values - np.median(values))))


def centroid(image, mask):
    rows, cols = np.indices(image.shape)
    values = np.where(mask & np.isfinite(image), image, 0.0)
    total = values.sum()
    if not np.isfinite(total) or abs(total) < 1e-12:
        return np.nan, np.nan
    return float((values * cols).sum() / total), float((values * rows).sum() / total)


def analyse_quarter(tpf, period, epoch, dur, rng):
    from scipy.ndimage import binary_dilation
    t = np.asarray(tpf.time.value, dtype=float)
    cube = np.asarray(tpf.flux.value, dtype=float)
    ok = np.isfinite(t) & np.isfinite(cube.reshape(len(t), -1)).any(axis=1)
    t, cube = t[ok], cube[ok]
    mask = binary_dilation(tpf.pipeline_mask, iterations=1)
    wcs = tpf.wcs
    results = []
    transit_epochs = epochs_in(t, period, epoch, dur)
    for e in transit_epochs:
        images = difference_image(t, cube, e, dur)
        if images is None:
            continue
        mean, diff = images
        cx, cy = centroid(mean, mask)
        dx, dy = centroid(diff, mask)
        fraction = np.nansum(diff[mask]) / np.nansum(mean[mask])
        # Tránsitos falsos: misma medida en épocas sin tránsito → dispersión real del método.
        span = (t.min() + 3 * dur, t.max() - 3 * dur)
        nulls = []
        for fake in rng.uniform(*span, size=FAKE_EPOCHS * 3):
            if min(abs(fake - x) for x in transit_epochs) < 3 * dur:
                continue
            fake_images = difference_image(t, cube, fake, dur)
            if fake_images is None:
                continue
            # Nulo: ruido real de una época sin tránsito + la misma señal colocada sobre la estrella.
            nulls.append(centroid(fake_images[1] + mean * fraction, mask))
            if len(nulls) >= FAKE_EPOCHS:
                break
        nulls = np.array([n for n in nulls if np.all(np.isfinite(n))])
        if len(nulls) < 8:
            continue
        sky_c = wcs.pixel_to_world(cx, cy)
        sky_d = wcs.pixel_to_world(dx, dy)
        null_sky = wcs.pixel_to_world(nulls[:, 0], nulls[:, 1])
        dra = (sky_d.ra.deg - sky_c.ra.deg) * np.cos(np.radians(sky_c.dec.deg)) * 3600
        ddec = (sky_d.dec.deg - sky_c.dec.deg) * 3600
        null_dra = (null_sky.ra.deg - sky_c.ra.deg) * np.cos(np.radians(sky_c.dec.deg)) * 3600
        null_ddec = (null_sky.dec.deg - sky_c.dec.deg) * 3600
        results.append(dict(quarter=int(tpf.quarter), epoca_bkjd=round(e, 3),
                            dra_arcsec=float(dra - np.median(null_dra)), ddec_arcsec=float(ddec - np.median(null_ddec)),
                            err_ra_arcsec=robust(null_dra), err_dec_arcsec=robust(null_ddec),
                            depth_aperture_ppm=float(np.nansum(diff[tpf.pipeline_mask]) /
                                                     np.nansum(mean[tpf.pipeline_mask]) * 1e6),
                            mean=mean, diff=diff, centro=(cx, cy), diff_centro=(dx, dy)))
    return results


def gaia_neighbours(ra, dec, radius=30.0):
    from astroquery.gaia import Gaia
    Gaia.ROW_LIMIT = 200
    query = f"""SELECT source_id, ra, dec, phot_g_mean_mag, ruwe,
                DISTANCE(POINT('ICRS', ra, dec), POINT('ICRS', {ra}, {dec})) * 3600 AS sep
                FROM gaiadr3.gaia_source
                WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, {radius / 3600})) = 1
                ORDER BY sep"""
    table = Gaia.launch_job(query).get_results()
    return [dict(source_id=int(r['source_id']), ra=float(r['ra']), dec=float(r['dec']),
                 sep_arcsec=round(float(r['sep']), 2), g=round(float(r['phot_g_mean_mag']), 2),
                 ruwe=None if np.ma.is_masked(r['ruwe']) else round(float(r['ruwe']), 2)) for r in table]


def main():
    import lightkurve as lk
    args = sys.argv[1:]
    kic = int(args[0]) if args else DEFAULT[0]
    period, epoch, hours = (float(a) for a in args[1:4]) if len(args) >= 4 else DEFAULT[1:]
    dur = hours / 24
    out = ROOT / f'results/seguimiento/KIC_{kic}/pixeles'
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    print(f'KIC {kic}: buscando imágenes de píxeles (TPF) en MAST...', flush=True)
    found = lk.search_targetpixelfile(f'KIC {kic}', mission='Kepler', cadence='long')
    print(f'  {len(found)} trimestres (unos 3 MB cada uno)', flush=True)
    events, header = [], None
    for index in range(len(found)):
        tpf = found[index].download(quality_bitmask='default', download_dir=str(ROOT / 'cache/mast'))
        if tpf is None:
            continue
        header = header or tpf.get_header()
        quarter_events = analyse_quarter(tpf, period, epoch, dur, rng)
        for ev in quarter_events:
            print(f"  Q{ev['quarter']:>2} BKJD {ev['epoca_bkjd']}: desplazamiento "
                  f"{ev['dra_arcsec']:+.2f}±{ev['err_ra_arcsec']:.2f}\" RA, "
                  f"{ev['ddec_arcsec']:+.2f}±{ev['err_dec_arcsec']:.2f}\" Dec", flush=True)
        events.extend(quarter_events)
    report = dict(kic=kic, periodo_d=period, epoca_bkjd=epoch)
    if events:
        ra = np.array([e['dra_arcsec'] for e in events])
        dec = np.array([e['ddec_arcsec'] for e in events])
        era = np.array([e['err_ra_arcsec'] for e in events])
        edec = np.array([e['err_dec_arcsec'] for e in events])
        wra, wdec = 1 / era ** 2, 1 / edec ** 2
        mra, mdec = (ra * wra).sum() / wra.sum(), (dec * wdec).sum() / wdec.sum()
        sra, sdec = 1 / np.sqrt(wra.sum()), 1 / np.sqrt(wdec.sum())
        offset = float(np.hypot(mra, mdec))
        sigma = float(np.hypot(mra / sra, mdec / sdec) / np.sqrt(2)) if sra > 0 and sdec > 0 else float('nan')
        report['imagen_diferencia'] = dict(
            eventos=[{k: v for k, v in e.items() if k not in ('mean', 'diff', 'centro', 'diff_centro')} for e in events],
            desplazamiento_ra_arcsec=round(float(mra), 2), error_ra_arcsec=round(float(sra), 2),
            desplazamiento_dec_arcsec=round(float(mdec), 2), error_dec_arcsec=round(float(sdec), 2),
            desplazamiento_arcsec=round(offset, 2), significancia_sigma=round(sigma, 2))
        cols = len(events)
        fig, axes = plt.subplots(2, cols, figsize=(3.2 * cols, 6.4), squeeze=False)
        for i, ev in enumerate(events):
            axes[0][i].imshow(ev['mean'], origin='lower', cmap='viridis')
            axes[0][i].set_title(f"Q{ev['quarter']} media fuera", fontsize=9)
            axes[1][i].imshow(ev['diff'], origin='lower', cmap='magma')
            axes[1][i].set_title(f"diferencia (luz que falta)", fontsize=9)
            for ax in (axes[0][i], axes[1][i]):
                ax.plot(*ev['centro'], 'w+', ms=14, mew=2)
                ax.plot(*ev['diff_centro'], 'cx', ms=10, mew=2)
                ax.set_xticks([])
                ax.set_yticks([])
        fig.suptitle('+ blanco: centro de la estrella · x cian: centro de la luz que falta', fontsize=10)
        fig.tight_layout()
        fig.savefig(out / '1_imagenes_diferencia.png', dpi=120)
        plt.close(fig)
    if header is not None:
        ra0, dec0 = float(header['RA_OBJ']), float(header['DEC_OBJ'])
        print('Consultando vecinos en Gaia DR3...', flush=True)
        try:
            stars = gaia_neighbours(ra0, dec0)
            target = stars[0] if stars and stars[0]['sep_arcsec'] < 2 else None
            for star in stars:
                if target and star is not target:
                    dmag = star['g'] - target['g']
                    star['delta_g'] = round(dmag, 2)
                    # Profundidad del eclipse que necesitaría ese vecino para fingir nuestra señal.
                    star['eclipse_necesario'] = round(DEPTH_PPM * 1e-6 * 10 ** (0.4 * dmag), 4)
            report['gaia'] = dict(objetivo=target, vecinos=[s for s in stars if s is not target])
        except Exception as error:  # noqa: BLE001 - sin red no se para el informe
            report['gaia'] = dict(error=str(error))
    (out / 'informe.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, default=float), encoding='utf-8')

    print('\n=== Imagen diferencia ===')
    if events:
        r = report['imagen_diferencia']
        print(f"  desplazamiento medio {r['desplazamiento_arcsec']}\" "
              f"(RA {r['desplazamiento_ra_arcsec']:+}±{r['error_ra_arcsec']}, "
              f"Dec {r['desplazamiento_dec_arcsec']:+}±{r['error_dec_arcsec']}) · {r['significancia_sigma']} σ")
        print('  (un píxel de Kepler mide 3,98"; < 3 σ = la luz que falta sale de la estrella)')
    else:
        print('  sin eventos medibles')
    gaia = report.get('gaia', {})
    print('\n=== Gaia DR3 (30") ===')
    if 'error' in gaia:
        print('  error:', gaia['error'])
    else:
        if gaia.get('objetivo'):
            print(f"  objetivo: G={gaia['objetivo']['g']} RUWE={gaia['objetivo']['ruwe']}")
        for s in gaia.get('vecinos', [])[:10]:
            need = s.get('eclipse_necesario')
            verdict = '' if need is None else (' → imposible (>100 %)' if need > 1 else f' → necesitaría eclipse del {need * 100:.1f} %')
            print(f"  {s['sep_arcsec']:>5}\"  G={s['g']}  ΔG={s.get('delta_g')}{verdict}")
        if not gaia.get('vecinos'):
            print('  ningún vecino en 30"')
    print(f'\nGráficas e informe en: {out}')


if __name__ == '__main__':
    main()
