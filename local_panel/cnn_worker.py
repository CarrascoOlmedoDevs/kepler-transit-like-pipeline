"""Panel-owned wrapper for the optional CNN commands."""
import sys
import threading

from kepler_rf_detector import main
from local_panel.worker import watch_parent


if __name__ == '__main__':
    if len(sys.argv) < 3:
        raise SystemExit('Uso: cnn_worker <parent-pid> <cnn-command> [opciones]')
    threading.Thread(target=watch_parent, args=(int(sys.argv[1]),), daemon=True).start()
    raise SystemExit(main(sys.argv[2:]))
