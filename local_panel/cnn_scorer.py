"""Puntuación CNN en el coordinador para resultados de ayudantes sin TensorFlow.

Los ayudantes (p. ej. con GPU AMD) guardan las entradas de la CNN en cnn_inputs.npz; este PC,
que sí tiene TensorFlow, las puntúa en segundo plano y completa candidates.csv."""
import csv
import io
import os
import queue
import threading
from pathlib import Path


class CNNScorer:
    def __init__(self, root, log=None, done=None):
        self.root = Path(root)
        self.log = log or (lambda job, text: None)
        self.done = done or (lambda job, kic, folder, mission: None)
        self.queue = queue.Queue()
        self.predictor = None
        self.unavailable = None
        self.thread = None
        self.lock = threading.Lock()

    def submit(self, job, kic, folder, mission='kepler'):
        self.queue.put((job, int(kic), Path(folder), mission))
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, daemon=True, name='cnn-scorer')
                self.thread.start()

    def backlog(self, folders):
        """Resultados que quedaron sin puntuar (p. ej. panel cerrado a medias)."""
        for job, kic, folder, mission in folders:
            if (Path(folder) / 'cnn_inputs.npz').exists():
                self.submit(job, kic, folder, mission)

    def _load(self):
        if self.predictor is None and self.unavailable is None:
            try:
                from kepler_detector.cnn import load_predictor
                self.predictor = load_predictor(self.root)
                if self.predictor is None:
                    self.unavailable = 'no hay modelo CNN entrenado'
            except (RuntimeError, ValueError) as error:
                self.unavailable = str(error)
        return self.predictor

    def _run(self):
        while True:
            try:
                job, kic, folder, mission = self.queue.get(timeout=5)
            except queue.Empty:
                return
            try:
                scored = self.score(folder)
                if scored is not None:
                    self.log(job, f'KIC {kic}: CNN calculada en este PC para {scored} candidato(s)')
            except Exception as error:  # noqa: BLE001 - un resultado malo no para la cola
                self.log(job, f'AVISO CNN KIC {kic}: {error}')
            finally:
                self.done(job, kic, folder, mission)

    def score(self, folder):
        """Completa cnn_probability en candidates.csv. None si este PC tampoco puede puntuar."""
        from kepler_detector.cnn import load_pending_inputs, predict_inputs
        folder = Path(folder)
        inputs_path = folder / 'cnn_inputs.npz'
        if not inputs_path.exists():
            return 0
        predictor = self._load()
        if predictor is None:
            return None
        pending = load_pending_inputs(inputs_path)
        path = folder / 'candidates.csv'
        text = path.read_text(encoding='utf-8')
        rows = list(csv.DictReader(io.StringIO(text)))
        fields = list(rows[0].keys()) if rows else []
        scored = 0
        for row in rows:
            try:
                order = int(float(row.get('detection_order', 0)))
            except ValueError:
                continue
            if order in pending and str(row.get('cnn_probability', '')).strip() in ('', 'nan'):
                row['cnn_probability'] = repr(predict_inputs(predictor, pending[order]))
                row['cnn_available'] = 'True'
                scored += 1
        if rows:
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
            temporary = path.with_name(path.name + '.tmp')
            temporary.write_text(buffer.getvalue(), encoding='utf-8')
            os.replace(temporary, path)
        inputs_path.unlink()
        return scored
