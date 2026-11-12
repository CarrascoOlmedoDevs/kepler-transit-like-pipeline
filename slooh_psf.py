"""Prueba de viabilidad con FITS de Slooh: ¿se puede medir la vecina de KIC 6881353 al 1 %?

Uso: python slooh_psf.py CARPETA_CON_FITS [--escala 0.63]

Para cada FITS:
  1. Localiza KIC 6881353 (por WCS si la cabecera lo trae; si no, la estrella más brillante cerca del centro).
  2. Comprueba saturación (pico frente a SATURATE / 65535).
  3. Ajusta a la vez dos Moffat con la misma forma: la principal y la vecina Gaia DR3 2077172048363770240
     (G ≈ 17,4, 2,65″ al sur). Con WCS la posición de la vecina queda fija; sin WCS se busca en un anillo
     de 2,65″ alrededor de la principal.
  4. Mide estrellas de comparación del campo con apertura y calcula flujo relativo de la vecina.
Después: dispersión del flujo relativo entre tomas (precisión por toma y binada) e inyección-recuperación
de una caída del 1 % en la vecina sobre las propias imágenes.
Resultados: results/seguimiento/KIC_6881353/slooh/informe.json y diagnostico.png"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import maximum_filter, median_filter
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parent
TARGET = (296.575566905, 42.346715796)            # KIC 6881353, Gaia DR3 2077172048372984192
NEIGHBOUR_SEP, NEIGHBOUR_PA = 2.65, 182.0         # ″, grados (de N hacia E)
FLUX_RATIO = 10 ** (-0.4 * 5.69)                  # vecina/principal en G
OUT = ROOT / 'results/seguimiento/KIC_6881353/slooh'


# ------------------------------------------------------------------ modelo
def moffat(x, y, x0, y0, flux, alpha, beta):
    r2 = ((x - x0) ** 2 + (y - y0) ** 2) / alpha ** 2
    norm = (beta - 1) / (np.pi * alpha ** 2)
    return flux * norm * (1 + r2) ** (-beta)


def two_star_model(p, x, y, neighbour_offset=None):
    x1, y1, f1, alpha, beta, sky = p[:6]
    if neighbour_offset is None:
        x2, y2, f2 = p[6:9]
    else:
        x2, y2, f2 = x1 + neighbour_offset[0], y1 + neighbour_offset[1], p[6]
    return sky + moffat(x, y, x1, y1, f1, alpha, beta) + moffat(x, y, x2, y2, f2, alpha, beta)


def fit_pair(cut, x0, y0, offset, scale, saturation):
    """Ajuste conjunto. offset: (dx, dy) en píxeles de la vecina o None si hay que buscarla."""
    ny, nx = cut.shape
    y, x = np.mgrid[0:ny, 0:nx]
    good = np.isfinite(cut) & (cut < 0.95 * saturation)
    sky0 = float(np.median(cut[good]))
    total = float(np.nansum(np.clip(cut - sky0, 0, None)))
    fwhm_guess = 1.5 / scale
    beta0 = 3.0
    alpha0 = fwhm_guess / (2 * np.sqrt(2 ** (1 / beta0) - 1))
    p0 = [x0, y0, total, alpha0, beta0, sky0]
    lo = [x0 - 3, y0 - 3, 0, 0.3, 1.2, -np.inf]
    hi = [x0 + 3, y0 + 3, np.inf, 20, 15, np.inf]
    if offset is None:
        sep = NEIGHBOUR_SEP / scale
        p0 += [x0, y0 - sep, total * FLUX_RATIO]
        lo += [x0 - 1.6 * sep, y0 - 1.6 * sep, -np.inf]
        hi += [x0 + 1.6 * sep, y0 + 1.6 * sep, np.inf]
    else:
        p0 += [total * FLUX_RATIO]
        lo += [-np.inf]
        hi += [np.inf]
    noise = np.sqrt(np.clip(cut, 1, None))  # Poisson aproximado (ADU ~ e-)

    def residual(p):
        return ((two_star_model(p, x, y, offset) - cut) / noise)[good]
    fit = least_squares(residual, p0, bounds=(lo, hi), x_scale='jac', max_nfev=4000)
    try:
        cov = np.linalg.inv(fit.jac.T @ fit.jac) * max(1.0, np.sum(fit.fun ** 2) / max(fit.fun.size - len(p0), 1))
        errors = np.sqrt(np.diag(cov))
    except np.linalg.LinAlgError:
        errors = np.full(len(p0), np.nan)
    p = fit.x
    f2_index = 6 if offset is not None else 8
    alpha, beta = p[3], p[4]
    fwhm = 2 * alpha * np.sqrt(2 ** (1 / beta) - 1)
    return dict(params=p, errors=errors, f1=p[2], f2=p[f2_index], f2_err=errors[f2_index],
                fwhm_px=float(fwhm), offset=offset if offset is not None else (p[6] - p[0], p[7] - p[1]),
                model=two_star_model(p, x, y, offset), chi2=float(np.sum(fit.fun ** 2) / max(fit.fun.size, 1)))


# ------------------------------------------------------------------ comparación
def find_stars(image, saturation, exclude, count=8):
    smooth = median_filter(image, size=3)
    sky = np.nanmedian(smooth)
    sigma = 1.4826 * np.nanmedian(np.abs(smooth - sky))
    peaks = (smooth == maximum_filter(smooth, size=15)) & (smooth > sky + 20 * sigma) & (image < 0.7 * saturation)
    ys, xs = np.nonzero(peaks)
    keep = []
    border = 25
    for yy, xx in sorted(zip(ys, xs), key=lambda p: -smooth[p]):
        if not (border < yy < image.shape[0] - border and border < xx < image.shape[1] - border):
            continue
        if np.hypot(xx - exclude[0], yy - exclude[1]) < 40:
            continue
        keep.append((xx, yy))
        if len(keep) == count:
            break
    return keep


def aperture(image, x, y, radius):
    yy, xx = np.mgrid[0:image.shape[0], 0:image.shape[1]]
    r = np.hypot(xx - x, yy - y)
    sky = np.nanmedian(image[(r > radius * 2) & (r < radius * 3)])
    return float(np.nansum(image[r < radius] - sky))


# ------------------------------------------------------------------ por imagen
def locate(header, data):
    try:
        from astropy.wcs import WCS
        wcs = WCS(header)
        if wcs.has_celestial:
            x, y = wcs.all_world2pix([[TARGET[0], TARGET[1]]], 0)[0]
            pa = np.radians(NEIGHBOUR_PA)
            dra = NEIGHBOUR_SEP * np.sin(pa) / 3600 / np.cos(np.radians(TARGET[1]))
            ddec = NEIGHBOUR_SEP * np.cos(pa) / 3600
            x2, y2 = wcs.all_world2pix([[TARGET[0] + dra, TARGET[1] + ddec]], 0)[0]
            from astropy.wcs.utils import proj_plane_pixel_scales
            scale = float(np.mean(np.abs(proj_plane_pixel_scales(wcs.celestial))) * 3600)
            return float(x), float(y), (float(x2 - x), float(y2 - y)), scale, 'wcs'
    except Exception:  # noqa: BLE001 - cabecera sin WCS válido
        pass
    ny, nx = data.shape
    box = data[ny // 4: 3 * ny // 4, nx // 4: 3 * nx // 4]
    yy, xx = np.unravel_index(np.nanargmax(median_filter(box, size=3)), box.shape)
    return float(xx + nx // 4), float(yy + ny // 4), None, None, 'brillante-central'


def measure(path, args):
    from astropy.io import fits
    with fits.open(path) as hdul:
        hdu = next(h for h in hdul if getattr(h, 'data', None) is not None and np.ndim(h.data) == 2)
        data = np.asarray(hdu.data, dtype=float)
        header = hdu.header
    saturation = float(header.get('SATURATE', header.get('DATAMAX', 65535)))
    x, y, offset, scale, how = locate(header, data)
    scale = scale or args.escala
    half = int(np.ceil(6 / scale)) + 6
    xi, yi = int(round(x)), int(round(y))
    cut = data[yi - half: yi + half + 1, xi - half: xi + half + 1]
    if cut.shape != (2 * half + 1, 2 * half + 1):
        raise ValueError('la estrella queda demasiado cerca del borde')
    peak = float(np.nanmax(cut))
    fit = fit_pair(cut, x - xi + half, y - yi + half, offset, scale, saturation)
    comps = find_stars(data, saturation, (x, y))
    radius = max(3.0, 2 * fit['fwhm_px'])
    comp_flux = [aperture(data, cx, cy, radius) for cx, cy in comps]
    return dict(file=path.name, date=str(header.get('DATE-OBS', '')), exptime=float(header.get('EXPTIME', np.nan)),
                filter=str(header.get('FILTER', '')), located_by=how, scale=scale, saturation=saturation,
                peak=peak, saturated=bool(peak >= 0.95 * saturation), nonlinear=bool(peak >= 0.7 * saturation),
                fwhm_arcsec=fit['fwhm_px'] * scale, f1=float(fit['f1']), f2=float(fit['f2']),
                f2_err=float(fit['f2_err']), ratio_measured=float(fit['f2'] / fit['f1']) if fit['f1'] else np.nan,
                offset_arcsec=[float(fit['offset'][0] * scale), float(fit['offset'][1] * scale)],
                comparison=comps, comparison_flux=comp_flux, chi2=fit['chi2'],
                _cut=cut, _model=fit['model'], _fit=fit, _offset=offset, _origin=(xi, yi, half))


# ------------------------------------------------------------------ inyección
def inject(rows, depth=0.01, seed=3):
    """Resta un depth de la vecina ajustada en la mitad de las tomas, reajusta y mide la caída."""
    rng = np.random.default_rng(seed)
    flags = np.zeros(len(rows), bool)
    flags[rng.permutation(len(rows))[: len(rows) // 2]] = True
    ratios = []
    for row, dimmed in zip(rows, flags):
        cut = row['_cut'].copy()
        p = row['_fit']['params']
        ny, nx = cut.shape
        y, x = np.mgrid[0:ny, 0:nx]
        if dimmed:
            off = row['_offset'] if row['_offset'] is not None else (p[6] - p[0], p[7] - p[1])
            cut -= depth * moffat(x, y, p[0] + off[0], p[1] + off[1], row['f2'], p[3], p[4])
        fit = fit_pair(cut, p[0], p[1], row['_offset'], row['scale'], row['saturation'])
        ratios.append(fit['f2'] / np.sum(row['comparison_flux']))
    ratios = np.array(ratios)
    out_mean, in_mean = ratios[~flags].mean(), ratios[flags].mean()
    err = np.sqrt(ratios[~flags].var(ddof=1) / (~flags).sum() + ratios[flags].var(ddof=1) / flags.sum())
    return dict(injected=depth, recovered=float(1 - in_mean / out_mean), error=float(err / out_mean),
                frames_in=int(flags.sum()), frames_out=int((~flags).sum()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('carpeta', type=Path)
    parser.add_argument('--escala', type=float, default=0.63, help='″/píxel si el FITS no trae WCS')
    args = parser.parse_args()
    files = sorted([*args.carpeta.glob('*.fits'), *args.carpeta.glob('*.fit'), *args.carpeta.glob('*.fts')])
    if not files:
        print('No hay FITS en', args.carpeta)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in files:
        try:
            row = measure(path, args)
            rows.append(row)
            print(f"{path.name}: pico {row['peak']:.0f}/{row['saturation']:.0f}"
                  f"{' SATURADA' if row['saturated'] else (' no lineal' if row['nonlinear'] else '')} · "
                  f"FWHM {row['fwhm_arcsec']:.2f}″ · vecina/principal {row['ratio_measured']:.4f} "
                  f"(esperado {FLUX_RATIO:.4f}) · {row['located_by']}", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f'{path.name}: no se pudo medir ({error})')
    usable = [r for r in rows if not r['saturated'] and r['comparison_flux'] and np.isfinite(r['f2'])]
    report = dict(files=len(files), measured=len(rows), usable=len(usable),
                  expected_ratio=FLUX_RATIO, frames=[{k: v for k, v in r.items() if not k.startswith('_')} for r in rows])
    if len(usable) >= 2:
        rel = np.array([r['f2'] / np.sum(r['comparison_flux']) for r in usable])
        scatter = float(np.std(rel, ddof=1) / np.mean(rel))
        report.update(neighbour_scatter_per_frame=scatter,
                      frames_for_1pct_3sigma=int(np.ceil((3 * scatter / 0.01) ** 2)),
                      median_fwhm_arcsec=float(np.median([r['fwhm_arcsec'] for r in usable])),
                      ratio_median=float(np.median([r['ratio_measured'] for r in usable])))
        print(f"\nDispersión de la vecina entre tomas: {100 * scatter:.2f} % por toma")
        print(f"Tomas necesarias para ver una caída del 1 % a 3σ: ~{report['frames_for_1pct_3sigma']} "
              f"dentro de la ventana (y otras tantas fuera)")
    if len(usable) >= 6:
        report['injection'] = inject(usable)
        inj = report['injection']
        print(f"Inyección 1 %: recuperado {100 * inj['recovered']:.2f} ± {100 * inj['error']:.2f} %")
    if rows:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        r = rows[0]
        fig, axes = plt.subplots(1, 3, figsize=(11, 3.8))
        vmax = np.percentile(r['_cut'], 99.5)
        for ax, img, title in zip(axes, (r['_cut'], r['_model'], r['_cut'] - r['_model']),
                                  ('imagen', 'modelo (2 Moffat)', 'residuo')):
            ax.imshow(img, origin='lower', cmap='magma', vmin=np.percentile(img, 5) if title == 'residuo' else None,
                      vmax=None if title == 'residuo' else vmax)
            ax.set_title(title)
            ax.set_xticks([])
            ax.set_yticks([])
        fig.suptitle(f"{r['file']} · FWHM {r['fwhm_arcsec']:.2f}″ · vecina/principal {r['ratio_measured']:.4f}")
        fig.tight_layout()
        fig.savefig(OUT / 'diagnostico.png', dpi=120)
        plt.close(fig)
    (OUT / 'informe.json').write_text(json.dumps(report, indent=2, default=float), encoding='utf-8')
    print(f'\nResultados en {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
