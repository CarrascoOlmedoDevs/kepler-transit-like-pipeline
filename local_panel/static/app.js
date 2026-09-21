const $ = id => document.getElementById(id);
let token = '', selected = null, busy = false, resultKey = '';
const labels = {
  running: 'En ejecución', paused: 'Pausado', stopped: 'Detenido',
  completed: 'Finalizado', error: 'Error', ready: 'Preparado', pending: 'Pendiente',
  complete: 'Completada', interrupted: 'Interrumpida', failed: 'Fallida'
};

async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Panel-Token': token},
    body: JSON.stringify(data)
  });
  const value = await response.json();
  if (!response.ok) throw Error(value.error || 'Error del servidor');
  return value;
}

function msg(text) { $('message').textContent = text; }
function node(tag, text) { const element = document.createElement(tag); element.textContent = text; return element; }

async function results(state) {
  const kic = $('stars').value;
  const key = `${state.id}/${kic}/${state.stars[kic]}`;
  if (key === resultKey) return;
  resultKey = key;
  $('results').replaceChildren();
  $('downloads').replaceChildren();
  if (state.stars[kic] !== 'complete') {
    $('results').textContent = 'Esta estrella todavía no tiene resultados completos.';
    return;
  }
  try {
    const rows = await api(`/api/results/${state.id}/${kic}`);
    const table = document.createElement('table'), head = document.createElement('tr');
    ['Rank', 'Revisión', 'Periodo (d)', 'RF', 'CNN', 'Prioridad', 'Clasificación', 'Curva'].forEach(text => head.append(node('th', text)));
    table.append(head);
    for (const row of rows) {
      const tr = document.createElement('tr');
      tr.className = `triage-${row.triage_label || 'debil'}`;
      tr.title = row.triage_reason || '';
      tr.append(node('td', row.rank || '—'));
      const triage = node('span', row.triage_label || '—');
      triage.className = 'triage-badge';
      const triageCell = document.createElement('td');
      triageCell.append(triage);
      tr.append(triageCell);
      for (const field of ['period_days', 'rf_probability', 'cnn_probability', 'priority_score', 'classification']) {
        let value = row[field] ?? '—';
        if (value === '') value = '—';
        if (['period_days', 'rf_probability', 'cnn_probability', 'priority_score'].includes(field) && value !== '—' && Number.isFinite(Number(value))) {
          value = Number(value).toFixed(field === 'period_days' ? 6 : 3);
        }
        tr.append(node('td', value));
      }
      const td = document.createElement('td');
      if (/^candidate_[0-9]+_folded\.png$/.test(row.plot_file || '')) {
        const link = node('a', 'Ver');
        link.href = `/files/${state.id}/${kic}/${row.plot_file}`;
        link.target = '_blank';
        link.rel = 'noopener';
        td.append(link);
      }
      tr.append(td);
      table.append(tr);
    }
    $('results').append(table);
    if (!rows.length) $('results').append(node('p', 'Sin detecciones aceptadas.'));
    for (const name of ['candidates.csv', 'detections.csv', 'metadata.json']) {
      const link = node('a', name);
      link.href = `/files/${state.id}/${kic}/${name}`;
      link.download = name;
      $('downloads').append(link);
    }
  } catch (error) {
    resultKey = '';
    $('results').textContent = error.message;
  }
}

function renderDiscovery(discovery, cnnRunning) {
  const active = discovery.enabled;
  const next = discovery.next_batch || 1;
  const total = discovery.total_batches || 25;
  const prepared = discovery.prepared_targets || total * 10;
  $('discovery-note').textContent = discovery.note || (active
    ? `Automático activo · ${prepared} estrellas preparadas · siguiente lote ${String(next).padStart(2, '0')}/${total}. Pausar o detener una cola frena el avance.`
    : `Preparado · ${prepared} estrellas · siguiente lote ${String(next).padStart(2, '0')}/${total}.`);
  $('discovery-start').disabled = active || cnnRunning;
  $('discovery-add').disabled = cnnRunning;
  $('discovery-stop').disabled = !active;
}

function renderCnn(cnn, scanRunning) {
  const running = cnn.status === 'running';
  const artifacts = cnn.artifacts || {};
  $('cnn-status').textContent = `${labels[cnn.status] || cnn.status}${cnn.operation ? ` · ${cnn.operation}` : ''}`;
  $('cnn-artifacts').replaceChildren();
  const items = [
    ['Dataset escalar', artifacts.training_features, `${cnn.training_rows || 0} filas`],
    ['Vistas CNN', artifacts.view_index, `${cnn.view_rows || 0} filas`],
    ['TensorFlow', artifacts.tensorflow, artifacts.tensorflow ? 'instalado' : 'no instalado'],
    ['Modelo', artifacts.model, artifacts.model ? 'disponible' : 'pendiente']
  ];
  for (const [name, ok, detail] of items) {
    const item = node('span', `${ok ? '✓' : '○'} ${name} · ${detail}`);
    item.className = ok ? 'artifact ok' : 'artifact';
    $('cnn-artifacts').append(item);
  }
  const unavailable = running || scanRunning;
  $('cnn-build').disabled = unavailable || !artifacts.training_features;
  $('cnn-train').disabled = unavailable || !artifacts.tensorflow || !artifacts.view_index;
  $('cnn-evaluate').disabled = unavailable || !artifacts.tensorflow || !artifacts.model;
  $('cnn-stop').disabled = !running;
  $('cnn-log').textContent = cnn.log || 'Sin actividad.';
  $('cnn-metrics').replaceChildren();
  if (cnn.error) $('cnn-metrics').append(node('p', cnn.error));
  if (cnn.metrics) {
    const metricNames = {accuracy: 'Accuracy', precision: 'Precisión', recall: 'Recall', f1: 'F1', roc_auc: 'ROC-AUC', average_precision: 'Average precision'};
    for (const [key, title] of Object.entries(metricNames)) {
      if (Number.isFinite(Number(cnn.metrics[key]))) {
        const box = document.createElement('div');
        box.append(node('strong', Number(cnn.metrics[key]).toFixed(3)), node('span', title));
        $('cnn-metrics').append(box);
      }
    }
  }
}

async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const snapshot = await api('/api/state');
    token = snapshot.token;
    const jobs = snapshot.jobs;
    const cnn = await api('/api/cnn');
    const discovery = await api('/api/discovery');
    const scanRunning = jobs.some(job => ['running', 'pausing', 'stopping'].includes(job.status));
    renderCnn(cnn, scanRunning);
    renderDiscovery(discovery, cnn.status === 'running');
    if ((!selected || !jobs.some(job => job.id === selected)) && jobs.length) {
      selected = (jobs.find(job => ['running', 'pausing', 'stopping'].includes(job.status)) || jobs[jobs.length - 1]).id;
    }
    $('jobs').replaceChildren();
    for (const job of jobs) {
      const button = node('button', job.name);
      button.className = 'job' + (selected === job.id ? ' selected' : '');
      const done = job.ids.filter(kic => job.stars[kic] === 'complete').length;
      button.append(node('small', `${labels[job.status] || job.status} · ${done}/${job.ids.length}`));
      button.onclick = () => { selected = job.id; resultKey = ''; refresh(); };
      $('jobs').append(button);
    }
    $('start').disabled = scanRunning || cnn.status === 'running';
    if (!selected) {
      $('title').textContent = 'Crea tu primera cola';
      ['resume', 'pause', 'stop'].forEach(id => $(id).disabled = true);
      return;
    }
    const state = await api('/api/jobs/' + selected);
    const done = state.ids.filter(kic => state.stars[kic] === 'complete').length;
    $('title').textContent = state.name;
    $('status').textContent = state.requested_action === 'pause' && state.status === 'running' ? 'Pausa solicitada' : labels[state.status] || state.status;
    $('done').textContent = done;
    $('total').textContent = state.ids.length;
    $('current').textContent = state.current || '—';
    $('serious').textContent = state.triage?.serious_count || 0;
    $('progress').max = state.ids.length;
    $('progress').value = done;
    $('note').textContent = state.error || (state.kind === 'blind' ? 'Cohorte TEST congelada. El control KIC 10854555 se ejecutará aparte, al terminar.' : 'Una estrella cada vez · Caché de curvas reutilizada · Configuración científica sin cambios.');
    $('resume').disabled = scanRunning || cnn.status === 'running' || state.status === 'completed';
    $('pause').disabled = state.status !== 'running' || state.requested_action === 'pause';
    $('stop').disabled = state.status !== 'running';
    $('log').textContent = state.log || 'Sin actividad. No se ha iniciado ningún análisis desde el panel.';
    const old = $('stars').value;
    $('stars').replaceChildren();
    const ids = [...state.ids, ...(state.control ? [state.control] : [])];
    ids.sort((a, b) => (state.stars[b] === 'complete') - (state.stars[a] === 'complete'));
    const seriousKics = new Set((state.triage?.serious || []).map(item => Number(item.kic)));
    for (const kic of ids) {
      const option = node('option', `${seriousKics.has(Number(kic)) ? '✦ ' : ''}KIC ${kic} · ${labels[state.stars[kic]] || state.stars[kic]}${kic === state.control ? ' (control)' : ''}`);
      option.value = kic;
      $('stars').append(option);
    }
    if (ids.includes(Number(old))) $('stars').value = old;
    $('stars').onchange = () => results(state);
    await results(state);
  } catch (error) {
    msg('No se pudo actualizar: ' + error.message);
  } finally {
    busy = false;
  }
}

$('create').onsubmit = async event => {
  event.preventDefault();
  try {
    const job = await api('/api/jobs', {kics: $('kics').value});
    selected = job.id;
    msg('Análisis iniciado.');
    await refresh();
  } catch (error) { msg(error.message); }
};

$('discovery-start').onclick = async () => {
  try {
    const state = await api('/api/discovery/start', {});
    msg(`Automatización activada. Siguiente lote: ${state.next_batch}/${state.total_batches}.`);
    await refresh();
  } catch (error) { msg(error.message); }
};

$('discovery-stop').onclick = async () => {
  try {
    await api('/api/discovery/stop', {});
    msg('Secuencia automática desactivada; el lote actual continuará.');
    await refresh();
  } catch (error) { msg(error.message); }
};

$('discovery-add').onclick = async () => {
  $('discovery-add').disabled = true;
  try {
    const result = await api('/api/discovery/replenish', {});
    msg(`Preparadas ${result.total_targets} estrellas en ${result.total_batches} lotes, sin duplicados.`);
    await refresh();
  } catch (error) {
    msg(error.message);
  } finally {
    $('discovery-add').disabled = false;
  }
};

for (const action of ['resume', 'pause', 'stop']) $('' + action).onclick = async () => {
  if (action === 'stop' && !confirm('¿Detener la estrella en curso? Sus archivos parciales se conservarán; al continuar se repetirá solo ese intento.')) return;
  try {
    await api(`/api/jobs/${selected}/${action}`, {});
    msg(action === 'pause' ? 'Pausa solicitada: se terminará primero la estrella actual.' : action === 'stop' ? 'Ejecución detenida. Resultados conservados.' : 'Continuación solicitada.');
    await refresh();
  } catch (error) { msg(error.message); }
};

async function startCnn(operation) {
  const options = operation === 'build' ? {
    limit: $('cnn-limit').value ? Number($('cnn-limit').value) : undefined
  } : operation === 'train' ? {
    epochs: Number($('cnn-epochs').value),
    batch_size: Number($('cnn-batch').value),
    test_size: Number($('cnn-test-size').value),
    seed: 42
  } : {};
  try {
    await api('/api/cnn/start', {operation, options});
    msg(`ExoMiner-lite: ${operation} iniciado.`);
    await refresh();
  } catch (error) { msg(error.message); }
}

$('cnn-build').onclick = () => startCnn('build');
$('cnn-train').onclick = () => startCnn('train');
$('cnn-evaluate').onclick = () => startCnn('evaluate');
$('cnn-stop').onclick = async () => {
  if (!confirm('¿Detener el proceso ExoMiner-lite en curso? Los artefactos ya completados se conservarán.')) return;
  try { await api('/api/cnn/stop', {}); msg('ExoMiner-lite detenido.'); await refresh(); }
  catch (error) { msg(error.message); }
};

if (document.modelContext?.registerTool) {
  try {
    Promise.resolve(document.modelContext.registerTool({
      name: 'read_kepler_jobs',
      description: 'Consultar el estado de las colas locales sin iniciar ni modificar análisis.',
      inputSchema: {type: 'object', properties: {}, additionalProperties: false},
      annotations: {readOnlyHint: true},
      execute: async () => { await refresh(); const state = await api('/api/state'); return {jobs: state.jobs}; }
    })).catch(() => {});
  } catch {}
}

refresh();
setInterval(refresh, 3000);
