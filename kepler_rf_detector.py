"""CLI Windows/Python 3.11. Importar este módulo no lanza procesos."""
import argparse
import json
import logging
from pathlib import Path
from config import Config
from kepler_detector.utils import setup


def analysis_options(parser):
    parser.add_argument('--period-min', type=float, default=0.5, help='Días; modo search')
    parser.add_argument('--period-max', type=float, default=300.0, help='Días; limitado también por baseline')
    parser.add_argument('--durations', type=float, nargs='+', default=[0.05, 0.1, 0.2, 0.3, 0.45], help='Duraciones BLS en días')
    parser.add_argument('--flatten-window', type=float, default=3.0, help='Ventana de detrending en días')
    parser.add_argument('--max-duration-factor', type=float, default=2.5,
                        help='Duración máxima = factor x tránsito central solar (13 h a 1 año, escala P^(1/3))')
    parser.add_argument('--sigma-clip', type=float, default=7.0, help='Clipping superior, preserva caídas')
    parser.add_argument('--coarse-points', type=int, default=30000)
    parser.add_argument('--refine-peaks', type=int, default=12)
    parser.add_argument('--max-planets', type=int, default=5, help='Máximo de candidatos, no planetas confirmados')
    parser.add_argument('--min-snr', type=float, default=7.1)
    parser.add_argument('--min-transits', type=int, default=2)
    parser.add_argument('--match-tolerance', type=float, default=0.01, help='Diferencia fraccional de período, 0.01=1%%')
    parser.add_argument('--quarter', type=int, metavar='N',
                        help='Segmento único: quarter Kepler (0-17), campaign K2 o sector TESS; por defecto todos')
    parser.add_argument('--save-curves', action='store_true')
    parser.add_argument('--seed', type=int, default=42)


def parser():
    main = argparse.ArgumentParser(description='Candidatos de tránsito Kepler, K2 y TESS con BLS, RF y vetting; no confirma planetas.')
    commands = main.add_subparsers(dest='command', required=True)
    for name in ('catalog', 'stellar-catalog', 'discovery-targets', 'discovery-batches', 'discovery-replenish', 'build', 'train', 'scan', 'targets', 'scan-list', 'evaluate',
                 'cnn-build', 'cnn-train', 'cnn-evaluate'):
        sub = commands.add_parser(name)
        sub.add_argument('--root', type=Path, default=Path(__file__).resolve().parent, help='Directorio de datos/modelos/resultados')
        if name in ('catalog', 'stellar-catalog', 'discovery-targets', 'build', 'scan', 'targets', 'scan-list', 'cnn-build'):
            sub.add_argument('--force', action='store_true', help='Rehacer resultados; reutiliza FITS/curvas cacheadas')
        if name in ('build', 'scan', 'scan-list', 'cnn-build'):
            analysis_options(sub)
        if name == 'build':
            sub.add_argument('--per-class', type=int, default=500)
        if name in ('build', 'scan-list'):
            sub.add_argument('--retry-failed', action='store_true')
        if name == 'train':
            sub.add_argument('--seed', type=int, default=42)
            sub.add_argument('--test-size', type=float, default=0.25)
        if name in ('scan', 'scan-list'):
            sub.add_argument('--no-model', action='store_true', help='Diagnóstico explícito sin RF; probability=NaN')
        if name == 'scan':
            target = sub.add_mutually_exclusive_group(required=True)
            target.add_argument('--kic', type=int, help='Estrella Kepler')
            target.add_argument('--epic', type=int, help='Estrella K2 (EPIC)')
            target.add_argument('--tic', type=int, help='Estrella TESS (TIC); también --sector N')
            sub.add_argument('--sector', '--campaign', dest='segment', type=int,
                             help='Alias de --quarter para sectores TESS o campañas K2')
        if name == 'scan-list':
            sub.add_argument('--mission', choices=('kepler', 'k2', 'tess'), default='kepler',
                             help='Telescopio; columnas aceptadas: kepid / epic / tic o target_id')
        if name in ('targets', 'scan-list'):
            sub.add_argument('--limit', type=int, default=1000 if name == 'targets' else None)
        if name == 'discovery-targets':
            sub.add_argument('--limit', type=int, default=250)
        if name == 'discovery-batches':
            sub.add_argument('--batch-size', type=int, default=10)
        if name == 'discovery-replenish':
            sub.add_argument('--block-size', type=int, default=250)
            sub.add_argument('--batch-size', type=int, default=10)
        if name == 'scan-list':
            sub.add_argument('--input', type=Path, required=True)
            sub.add_argument('--workers', type=int, default=1)
        if name == 'cnn-build':
            sub.add_argument('--limit', type=int, help='Máximo de ejemplos nuevos; útil para smoke tests')
        if name == 'cnn-train':
            sub.add_argument('--seed', type=int, default=42)
            sub.add_argument('--test-size', type=float, default=0.25)
            sub.add_argument('--epochs', type=int, default=60)
            sub.add_argument('--batch-size', type=int, default=32)
    return main


def main(argv=None):
    args = parser().parse_args(argv)
    root = setup(args.root.resolve())
    try:
        if getattr(args, 'segment', None) is not None:
            if args.quarter is not None and args.quarter != args.segment:
                raise ValueError('Use sólo uno de --quarter / --sector / --campaign')
            args.quarter = args.segment
        mission, target = 'kepler', getattr(args, 'kic', None)
        if getattr(args, 'epic', None) is not None:
            mission, target = 'k2', args.epic
        elif getattr(args, 'tic', None) is not None:
            mission, target = 'tess', args.tic
        mission = getattr(args, 'mission', mission)
        if mission == 'kepler' and getattr(args, 'quarter', None) is not None and not 0 <= args.quarter <= 17:
            raise ValueError('Quarter Kepler debe estar entre 0 y 17')
        config = Config(**{name: getattr(args, name) for name in Config.__dataclass_fields__}) if args.command in ('build', 'scan', 'scan-list', 'cnn-build') else None
        if args.command == 'catalog':
            from kepler_detector.catalog import get_catalog
            print(f'{len(get_catalog(root, args.force))} KOI en {root / "data/koi_dr25.csv"}')
        elif args.command == 'stellar-catalog':
            from kepler_detector.catalog import get_stellar_catalog
            print(f'{len(get_stellar_catalog(root, args.force))} estrellas DR25 en {root / "data/kepler_stellar_dr25.csv"}')
        elif args.command == 'discovery-targets':
            from kepler_detector.catalog import discovery_targets
            table = discovery_targets(root, args.limit, args.force)
            print(f'{len(table)} objetivos inéditos en {root / "data/kepler_discovery_targets.csv"}')
        elif args.command == 'discovery-batches':
            from kepler_detector.catalog import discovery_batches
            manifest = discovery_batches(root, args.batch_size)
            print(f'{len(manifest)} lotes en {root / "data/discovery_batches"}')
        elif args.command == 'discovery-replenish':
            from kepler_detector.catalog import replenish_discovery_batches
            print(json.dumps(replenish_discovery_batches(root, args.block_size, args.batch_size), indent=2))
        elif args.command == 'targets':
            from kepler_detector.catalog import targets
            print(f'{len(targets(root, args.limit, args.force))} objetivos')
        elif args.command == 'build':
            from kepler_detector.builder import build
            print(build(root, config, args.per_class, args.force, args.retry_failed))
        elif args.command == 'train':
            from kepler_detector.ml import train
            print(json.dumps(train(root, args.seed, args.test_size), indent=2))
        elif args.command == 'evaluate':
            from kepler_detector.ml import evaluate
            print(json.dumps(evaluate(root), indent=2))
        elif args.command == 'cnn-build':
            from kepler_detector.cnn import build_dataset
            print(json.dumps(build_dataset(root, config, args.force, args.limit), indent=2))
        elif args.command == 'cnn-train':
            from kepler_detector.cnn import train
            print(json.dumps(train(root, args.seed, args.test_size, args.epochs, args.batch_size), indent=2))
        elif args.command == 'cnn-evaluate':
            from kepler_detector.cnn import evaluate
            print(json.dumps(evaluate(root), indent=2))
        elif args.command == 'scan':
            from kepler_detector.scanner import scan
            print(scan(root, target, config, args.force, args.no_model, mission=mission).to_string(index=False))
        elif args.command == 'scan-list':
            from kepler_detector.scanner import scan_list
            scan_list(root, args.input, config, args.limit, args.workers, args.force, args.no_model, args.retry_failed,
                      mission=mission)
        return 0
    except Exception as error:
        logging.getLogger('kepler').debug('Error de comando', exc_info=True)
        logging.getLogger('kepler').error('%s (detalles en logs/pipeline.log)', error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
