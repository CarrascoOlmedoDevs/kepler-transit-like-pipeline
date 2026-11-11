"""Análisis de seguimiento de un candidato: tránsitos uno a uno, centroides y forma del tránsito.

Uso: python analisis_candidato.py [KIC] [periodo_d] [epoca_bkjd] [duracion_h]
Por defecto analiza KIC 6881353 (P=233.94 d). Resultados en results/seguimiento/KIC_<kic>/analisis/.
Nada de esto confirma un planeta: sólo busca motivos para descartarlo."""
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.optimize import brentq, least_squares

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from config import Config  # noqa: E402
from kepler_detector.utils import robust_std  # noqa: E402

DEFAULT = (6881353, 233.9407736958617, 349.9347535910972, 12.636)
STAR = {6881353: dict(radius=1.46, mass=0.97, teff=6181)}  # KIC/Berger: R☉, M☉, K
PIXEL_ARCSEC = 3.98
G, RSUN, MSUN = 6.674e-11, 6.957e8, 1.989e30


# ---------------------------------------------------------------- datos
def load(kic):
    from kepler_detector.mast import download
    from kepler_detector.preprocessing import preprocess
    curve = download(ROOT, kic)
    return curve, preprocess(curve, Config())


def centroid_series(kic):
    """(tiempo BKJD, columna, fila, quarter) de los FITS PDCSAP ya en caché (MOM_CENTR)."""
    import lightkurve as lk
    files = sorted((ROOT / 'cache/mast').rglob(f'kplr{kic:09d}-*_llc.fits'))
    out = []
    for path in files:
        try:
            lc = lk.read(str(path), quality_bitmask='default')
        except Exception:  # noqa: BLE001 - un FITS corrupto no debe parar el análisis
            continue
        t = np.asarray(lc.time.value, dtype=float)
        col = np.asarray(lc.centroid_col.value, dtype=float)
        row = np.asarray(lc.centroid_row.value, dtype=float)
        ok = np.isfinite(t) & np.isfinite(col) & np.isfinite(row)
        out.append((t[ok], col[ok], row[ok], int(lc.meta.get('QUARTER', -1))))
    return out


# ---------------------------------------------------------------- 1. tránsitos uno a uno
def window_stats(t, y, epoch, dur):
    dt = t - epoch
    inside = np.abs(dt) < dur / 2
    outside = (np.abs(dt) > 0.75 * dur) & (np.abs(dt) < 3 * dur)
    expected = dur / np.median(np.diff(t))
    result = dict(epoca_bkjd=round(float(epoch), 3), puntos_dentro=int(inside.sum()),
                  cobertura=round(float(inside.sum() / expected), 2))
    if inside.sum() < 5 or outside.sum() < 10:
        return result
    sigma = robust_std(y[outside])
    depth = (np.median(y[outside]) - np.mean(y[inside])) * 1e6
    error = sigma / np.sqrt(inside.sum()) * 1e6
    # Hueco de datos junto al tránsito: el detrending puede haber deformado el borde.
    near = t[np.abs(dt) < 2]
    gap = float(np.max(np.diff(near))) * 24 if len(near) > 2 else float('nan')
    result.update(profundidad_ppm=round(depth, 1), error_ppm=round(error, 1), snr=round(depth / error, 2),
                  rms_local_ppm=round(sigma * 1e6, 1), mayor_hueco_h=round(gap, 1))
    return result


def individual(flat, period, epoch, dur, out):
    t, y = flat.time, flat.flux
    epochs = [epoch + k * period for k in range(-10, 20)
              if t.min() - dur < epoch + k * period < t.max() + dur]
    rows = [window_stats(t, y, e, dur) for e in epochs]
    fig, axes = plt.subplots(1, len(epochs), figsize=(3.3 * len(epochs), 3.4), sharey=True, squeeze=False)
    for ax, e, r in zip(axes[0], epochs, rows):
        sel = np.abs(t - e) < 1.5
        hours = (t[sel] - e) * 24
        ax.plot(hours, (y[sel] - 1) * 1e6, '.', ms=2, color='0.65')
        bins = np.arange(-36, 36.1, 1.5)
        idx = np.digitize(hours, bins)
        centers = [(bins[i - 1] + bins[i]) / 2 for i in range(1, len(bins)) if (idx == i).sum() > 3]
        means = [np.mean((y[sel][idx == i] - 1) * 1e6) for i in range(1, len(bins)) if (idx == i).sum() > 3]
        ax.plot(centers, means, 'o-', ms=3, color='#1f5fbf')
        ax.axvspan(-dur * 12, dur * 12, color='#e8a33d', alpha=0.15)
        label = f"{r.get('profundidad_ppm', '—')} ppm · SNR {r.get('snr', '—')}"
        ax.set_title(f"BKJD {e:.1f}\n{label}", fontsize=9)
        ax.set_xlabel('horas')
        ax.set_ylim(-300, 200)
    axes[0][0].set_ylabel('flujo relativo (ppm)')
    fig.tight_layout()
    fig.savefig(out / '1_transitos_individuales.png', dpi=130)
    plt.close(fig)
    valid = [r for r in rows if 'snr' in r]
    depths = np.array([r['profundidad_ppm'] for r in valid])
    errors = np.array([r['error_ppm'] for r in valid])
    mean = float(np.average(depths, weights=1 / errors ** 2)) if len(valid) else float('nan')
    chi2 = float(np.sum(((depths - mean) / errors) ** 2)) if len(valid) else float('nan')
    return dict(transitos=rows, profundidad_media_ppm=round(mean, 1),
                chi2=round(chi2, 2), grados_libertad=max(len(valid) - 1, 0))


# ---------------------------------------------------------------- 2. centroides
def centroid_test(series, period, epoch, dur, out):
    """Desplazamiento medio del centroide dentro del tránsito frente a fuera, por evento."""
    shifts = []
    for t, col, row, quarter in series:
        for k in range(int((t.min() - epoch) / period) - 1, int((t.max() - epoch) / period) + 2):
            e = epoch + k * period
            dt = t - e
            inside = np.abs(dt) < dur / 2
            outside = (np.abs(dt) > 0.75 * dur) & (np.abs(dt) < 3 * dur)
            if inside.sum() < 5 or outside.sum() < 10:
                continue
            item = dict(quarter=quarter, epoca_bkjd=round(e, 3))
            for name, values in (('col', col), ('row', row)):
                # Tendencia lineal fuera del tránsito: la deriva de apuntado no es señal.
                fit = np.polyfit(dt[outside], values[outside], 1)
                resid = values - np.polyval(fit, dt)
                sigma = robust_std(resid[outside])
                item[f'd{name}_mpix'] = float(np.mean(resid[inside]) * 1e3)
                item[f'e{name}_mpix'] = float(sigma / np.sqrt(inside.sum()) * 1e3)
            shifts.append(item)
    if not shifts:
        return dict(disponible=False, nota='No hay FITS con centroides en cache/mast')
    result = dict(disponible=True, eventos=shifts)
    for name in ('col', 'row'):
        d = np.array([s[f'd{name}_mpix'] for s in shifts])
        e = np.array([s[f'e{name}_mpix'] for s in shifts])
        w = 1 / e ** 2
        result[f'{name}_mpix'] = round(float((d * w).sum() / w.sum()), 3)
        result[f'{name}_error_mpix'] = round(float(1 / np.sqrt(w.sum())), 3)
    total = np.hypot(result['col_mpix'], result['row_mpix'])
    error = np.hypot(result['col_error_mpix'], result['row_error_mpix'])
    result['desplazamiento_mpix'] = round(float(total), 3)
    result['significancia_sigma'] = round(float(total / error), 2)
    result['desplazamiento_mas'] = round(float(total * PIXEL_ARCSEC), 2)
    fig, ax = plt.subplots(figsize=(4.5, 4.5))
    for s in shifts:
        ax.errorbar(s['dcol_mpix'], s['drow_mpix'], xerr=s['ecol_mpix'], yerr=s['erow_mpix'],
                    fmt='o', ms=4, color='0.5', alpha=0.7)
    ax.errorbar(result['col_mpix'], result['row_mpix'], xerr=result['col_error_mpix'],
                yerr=result['row_error_mpix'], fmt='s', ms=7, color='#c0392b', label='media')
    ax.axhline(0, color='k', lw=0.5)
    ax.axvline(0, color='k', lw=0.5)
    ax.set_xlabel('Δ columna (milipíxeles)')
    ax.set_ylabel('Δ fila (milipíxeles)')
    ax.set_title(f"Centroide en tránsito: {result['significancia_sigma']} σ")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / '2_centroides.png', dpi=130)
    plt.close(fig)
    return result


# ---------------------------------------------------------------- 3. forma del tránsito
def trapezoid(x, depth, t14, t12, shift):
    """Modelo trapezoidal en días desde el centro."""
    x = np.abs(x - shift)
    t12 = np.clip(t12, 1e-4, t14 / 2)
    flux = np.zeros_like(x)
    full = x <= t14 / 2 - t12
    ramp = (x > t14 / 2 - t12) & (x < t14 / 2)
    flux[full] = depth
    flux[ramp] = depth * (t14 / 2 - x[ramp]) / t12
    return -flux


def shape_fit(flat, period, epoch, dur, kic, out):
    t, y = flat.time, flat.flux
    phase = (t - epoch + period / 2) % period - period / 2
    sel = np.abs(phase) < 2.5 * dur
    x, f = phase[sel], (y[sel] - 1)
    sigma = robust_std(f[np.abs(x) > dur])

    def residual(p):
        return (f - trapezoid(x, *p)) / sigma
    depth0 = max(1e-5, -np.mean(f[np.abs(x) < dur / 2]))
    fit = least_squares(residual, [depth0, dur, dur / 6, 0.0],
                        bounds=([0, dur / 4, 1e-4, -dur / 2], [1e-3, 2.5 * dur, dur, dur / 2]))
    depth, t14, t12, shift = fit.x
    try:
        cov = np.linalg.inv(fit.jac.T @ fit.jac)
        errors = np.sqrt(np.diag(cov))
    except np.linalg.LinAlgError:
        errors = np.full(4, np.nan)
    result = dict(profundidad_ppm=round(depth * 1e6, 1), error_profundidad_ppm=round(errors[0] * 1e6, 1),
                  t14_h=round(t14 * 24, 2), error_t14_h=round(errors[1] * 24, 2),
                  ingreso_h=round(t12 * 24, 2), error_ingreso_h=round(errors[2] * 24, 2),
                  desfase_centro_h=round(shift * 24, 2), chi2_reducido=round(float(np.sum(fit.fun ** 2) / (len(x) - 4)), 3))
    star = STAR.get(kic)
    if star:
        k = np.sqrt(depth)
        rho = star['mass'] * MSUN / (4 / 3 * np.pi * (star['radius'] * RSUN) ** 3)
        a_rs = (G * rho * (period * 86400) ** 2 / (3 * np.pi)) ** (1 / 3)

        def duration(b):
            arg = np.sqrt(max((1 + k) ** 2 - b ** 2, 0)) / a_rs
            return period / np.pi * np.arcsin(min(arg, 1.0))
        central = duration(0.0)
        result.update(radio_planeta_rtierra=round(k * star['radius'] * 109.1, 2),
                      densidad_estelar_solar=round(rho / 1408, 3), a_sobre_rs=round(a_rs, 1),
                      t14_central_esperada_h=round(central * 24, 2))
        if t14 <= central:
            b = brentq(lambda value: duration(value) - t14, 0, 1 + k - 1e-6)
            result['parametro_impacto_b'] = round(float(b), 2)
            result['forma_compatible'] = True
        else:
            result['parametro_impacto_b'] = None
            # Más largo que un tránsito central: estrella más grande o menos densa de lo catalogado,
            # órbita excéntrica, o la señal no es un tránsito de esta estrella.
            result['forma_compatible'] = bool(t14 <= central * 1.3)
    # Gráfica plegada con el modelo
    order = np.argsort(x)
    bins = np.arange(-2.5 * dur, 2.5 * dur, dur / 8)
    idx = np.digitize(x, bins)
    bx = [x[idx == i].mean() for i in range(1, len(bins)) if (idx == i).sum() > 5]
    by = [f[idx == i].mean() for i in range(1, len(bins)) if (idx == i).sum() > 5]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(x * 24, f * 1e6, '.', ms=1.5, color='0.75')
    ax.plot(np.array(bx) * 24, np.array(by) * 1e6, 'o', ms=4, color='#1f5fbf', label='promedio')
    ax.plot(x[order] * 24, trapezoid(x[order], *fit.x) * 1e6, color='#c0392b', lw=2, label='trapecio ajustado')
    ax.set_ylim(-250, 150)
    ax.set_xlabel('horas desde el centro')
    ax.set_ylabel('flujo relativo (ppm)')
    ax.set_title(f"P = {period:.4f} d · {result['profundidad_ppm']} ppm · T14 = {result['t14_h']} h")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / '3_forma_transito.png', dpi=130)
    plt.close(fig)
    return result


# ---------------------------------------------------------------- informe
def verdict(single, centroid, shape):
    notes = []
    weak = [r for r in single['transitos'] if 'snr' in r and r['snr'] < 2]
    if weak:
        notes.append(f"{len(weak)} tránsito(s) casi sin señal: " +
                     ', '.join(f"BKJD {r['epoca_bkjd']} (SNR {r['snr']}, cobertura {r['cobertura']}, "
                               f"hueco {r.get('mayor_hueco_h')} h)" for r in weak))
    if single['grados_libertad'] and single['chi2'] / single['grados_libertad'] > 3:
        notes.append('profundidades poco compatibles entre tránsitos')
    if centroid.get('disponible'):
        if centroid['significancia_sigma'] >= 3:
            notes.append(f"CENTROIDE DESPLAZADO {centroid['significancia_sigma']} σ: posible binaria de fondo")
        else:
            notes.append(f"centroide sin desplazamiento significativo ({centroid['significancia_sigma']} σ)")
    else:
        notes.append('centroides no disponibles')
    if shape.get('forma_compatible') is False:
        notes.append('duración demasiado larga para esta estrella')
    elif shape.get('forma_compatible'):
        notes.append(f"duración compatible con la estrella (b ≈ {shape.get('parametro_impacto_b')})")
    return notes


def main():
    args = sys.argv[1:]
    kic = int(args[0]) if args else DEFAULT[0]
    period, epoch, hours = (float(a) for a in args[1:4]) if len(args) >= 4 else DEFAULT[1:]
    dur = hours / 24
    out = ROOT / f'results/seguimiento/KIC_{kic}/analisis'
    out.mkdir(parents=True, exist_ok=True)
    print(f'KIC {kic}: P={period} d, época {epoch}, duración {hours} h')
    print('Cargando curva (usa la caché si ya se descargó)...', flush=True)
    _, flat = load(kic)
    print('1/3 Tránsitos uno a uno...', flush=True)
    single = individual(flat, period, epoch, dur, out)
    print('2/3 Centroides...', flush=True)
    centroid = centroid_test(centroid_series(kic), period, epoch, dur, out)
    print('3/3 Forma del tránsito...', flush=True)
    shape = shape_fit(flat, period, epoch, dur, kic, out)
    report = dict(kic=kic, periodo_d=period, epoca_bkjd=epoch, duracion_h=hours,
                  tránsitos=single, centroides=centroid, forma=shape, resumen=verdict(single, centroid, shape))
    (out / 'informe.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, default=float), encoding='utf-8')
    print('\n=== Tránsitos ===')
    for r in single['transitos']:
        print(f"  BKJD {r['epoca_bkjd']:>9}: {r.get('profundidad_ppm', '—'):>6} ppm  SNR {r.get('snr', '—'):>5}  "
              f"cobertura {r['cobertura']}  hueco máx {r.get('mayor_hueco_h', '—')} h")
    if centroid.get('disponible'):
        print(f"\n=== Centroides === desplazamiento {centroid['desplazamiento_mpix']} mpx "
              f"({centroid['desplazamiento_mas']} mas) · {centroid['significancia_sigma']} σ")
    print(f"\n=== Forma === {shape['profundidad_ppm']}±{shape['error_profundidad_ppm']} ppm, "
          f"T14 {shape['t14_h']}±{shape['error_t14_h']} h, ingreso {shape['ingreso_h']} h")
    if 'radio_planeta_rtierra' in shape:
        print(f"  radio ≈ {shape['radio_planeta_rtierra']} R⊕ · T14 central esperada {shape['t14_central_esperada_h']} h"
              f" · b ≈ {shape['parametro_impacto_b']}")
    print('\n=== Resumen ===')
    for note in report['resumen']:
        print('  - ' + note)
    print(f'\nGráficas e informe en: {out}')


if __name__ == '__main__':
    main()
