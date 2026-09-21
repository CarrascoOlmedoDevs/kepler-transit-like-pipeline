import hashlib
import json
import logging
import os
import time
from pathlib import Path
import numpy as np
import pandas as pd


def setup(root):
    root = Path(root)
    for folder in ('data', 'cache/lightcurves', 'models', 'results', 'logs'):
        (root / folder).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('kepler')
    expected = str((root / 'logs/pipeline.log').resolve())
    existing = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    if existing and existing[0].baseFilename != expected:
        close_logging()
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        disk = logging.FileHandler(root / 'logs/pipeline.log', encoding='utf-8')
        disk.setLevel(logging.DEBUG)
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        for handler in (disk, console):
            handler.setFormatter(formatter)
            logger.addHandler(handler)
    return root


def close_logging():
    logger = logging.getLogger('kepler')
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


def retry(operation, attempts=4):
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as error:
            if attempt == attempts - 1:
                raise
            logging.getLogger('kepler').warning('Intento %d/%d: %s', attempt + 1, attempts, error)
            time.sleep(min(30, 2 ** (attempt + 1)))


def atomic_csv(frame, path):
    path = Path(path)
    temp = path.with_suffix('.csv.tmp')
    frame.to_csv(temp, index=False)
    _replace_with_retry(temp, path)


def atomic_json(value, path):
    path = Path(path)
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    _replace_with_retry(temp, path)


def _replace_with_retry(temp, path, attempts=40):
    """Replace an artifact despite brief Windows reader/antivirus locks."""
    for attempt in range(attempts):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(min(0.5, 0.025 * (attempt + 1)))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def robust_std(x):
    x = np.asarray(x)
    x = x[np.isfinite(x)]
    return float(max(1e-8, 1.4826 * np.median(np.abs(x - np.median(x))))) if len(x) else float('nan')


def read_frame(path):
    return pd.read_csv(path) if Path(path).exists() else pd.DataFrame()
