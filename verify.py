"""Verificación offline reproducible con informe de cada proceso y códigos de salida."""
import compileall
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    logs = root / 'logs'
    logs.mkdir(exist_ok=True)
    compiled = all(compileall.compile_file(str(path), quiet=1) for path in
                   [*sorted(root.glob('*.py')), *sorted((root / 'kepler_detector').glob('*.py')),
                    *sorted((root / 'tests').glob('*.py'))])
    outcomes = {'compilation': compiled, 'python': sys.version}
    for name, command in [('tests', ['-m', 'unittest', 'discover', '-s', 'tests', '-v']),
                           ('help', ['kepler_rf_detector.py', '--help']),
                           ('smoke', ['smoke_test.py']), ('dependencies', ['-m', 'pip', 'check'])]:
        process = subprocess.run([sys.executable, *command], cwd=root, capture_output=True,
                                 text=True, encoding='utf-8', errors='replace',
                                 env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        (logs / f'{name}.txt').write_text(process.stdout + process.stderr, encoding='utf-8')
        outcomes[name] = {'exit_code': process.returncode}
        print(name, 'PASS' if process.returncode == 0 else 'FAIL')
        if process.returncode:
            print(process.stderr)
    (logs / 'verification.json').write_text(json.dumps(outcomes, indent=2), encoding='utf-8')
    return 0 if compiled and all(v['exit_code'] == 0 for v in outcomes.values() if isinstance(v, dict)) else 1


if __name__ == '__main__':
    raise SystemExit(main())
