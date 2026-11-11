const $ = id => document.getElementById(id);
let token = '', selected = null, busy = false, resultKey = '';
// Enlaces desde el resumen: /control?job=…&kic=…
const linkParams = new URLSearchParams(location.search);
let wantedKic = linkParams.get('kic');
selected = linkParams.get('job');
const labels = {
  running: 'En ejecución', paused: 'Pausado', stopped: 'Detenido',
  completed: 'Finalizado', error: 'Error', ready: 'Preparado', pending: 'Pendiente',
  complete: 'Completada', interrupted: 'Interrumpida', failed: 'Fallida', training: 'Entrenando'
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

let currentMission = 'kepler';
async function results(state) {
  currentMission = state.mission || 'kepler';
  const kic = $('stars').value;
  const key = `${state.id}/${kic}/${state.stars[kic]}`;
  if (key === resultKey) return;
  resultKey = key;
  $('results').replaceChildren();
  $('downloads').replaceChildren();
  $('curves').replaceChildren();
  $('curves-extra-imgs').replaceChildren();
  $('curves-card').hidden = true;
  if (state.task === 'build') {
    const signals = (state.build?.signals || {})[kic] || [];
    $('results').textContent = `Estrella de entrenamiento: ${signals.length} KOI (${signals.map(s => s[0]).join(', ')}). ${state.note || ''}`;
    return;
  }
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
        const url = `/files/${state.id}/${kic}/${row.plot_file}`;
        const thumb = document.createElement('a');
        thumb.href = url;
        thumb.target = '_blank';
        thumb.rel = 'noopener';
        thumb.className = 'curve-thumb';
        thumb.title = 'Abrir a tamaño completo';
        const img = document.createElement('img');
        img.src = url;
        img.loading = 'lazy';
        img.alt = `Curva plegada del candidato ${row.rank || ''}`;
        thumb.append(img);
        const link = node('a', 'Ver');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener';
        td.className = 'curve-cell';
        td.append(thumb, link);
      }
      tr.append(td);
      table.append(tr);
    }
    $('results').append(table);
    if (!rows.length) $('results').append(node('p', 'Sin detecciones aceptadas.'));
    renderCurves(state.id, kic, rows);
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

function figure(url, caption, className) {
  const box = document.createElement('figure');
  box.className = className || 'curve-figure';
  const link = document.createElement('a');
  link.href = url;
  link.target = '_blank';
  link.rel = 'noopener';
  link.title = 'Abrir a tamaño completo';
  const img = document.createElement('img');
  img.src = url;
  img.loading = 'lazy';
  img.alt = caption;
  img.onerror = () => box.remove();  // Resultados antiguos sin esa figura.
  link.append(img);
  box.append(link, node('figcaption', caption));
  return box;
}

function renderCurves(job, kic, rows) {
  const base = `/files/${job}/${kic}/`;
  $('curves-title').textContent = starName(kic, currentMission);
  const plotted = rows.filter(row => /^candidate_[0-9]+_folded\.png$/.test(row.plot_file || ''));
  for (const row of plotted) {
    const period = Number(row.period_days);
    const caption = `Candidato ${row.rank || ''} · periodo ${Number.isFinite(period) ? period.toFixed(5) : '—'} d · ${row.triage_label || ''}`;
    $('curves').append(figure(base + row.plot_file, caption));
  }
  if (!plotted.length) $('curves').append(node('p', 'Sin candidatos: abajo tienes la curva completa.'));
  $('curves-extra').open = !plotted.length;
  for (const [file, caption] of [['lightcurve.png', 'Curva completa (PDCSAP normalizada)'],
      ['detrended_lightcurve.png', 'Curva sin tendencia'], ['bls_periodogram.png', 'Periodograma BLS']]) {
    $('curves-extra-imgs').append(figure(base + file, caption, 'curve-figure small'));
  }
  $('curves-card').hidden = false;
}

let clusterEnabled = false;

// ---- Selector Kepler | TESS (compartido por las dos páginas) ----
let mission = 'kepler';
const PREFIX = {kepler: 'KIC', tess: 'TIC'};
const starName = (id, m) => `${PREFIX[m || mission] || 'KIC'} ${id}`;
async function loadMode() {
  const response = await fetch('/api/settings');
  mission = (await response.json()).mission || 'kepler';
  document.body.dataset.mission = mission;
  for (const button of document.querySelectorAll('.mode-switch button')) {
    button.classList.toggle('on', button.dataset.mission === mission);
    button.onclick = async () => {
      if (button.dataset.mission === mission) return;
      if (!token) token = (await (await fetch('/api/state')).json()).token;
      const result = await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json',
        'X-Panel-Token': token}, body: JSON.stringify({mission: button.dataset.mission})});
      if (result.ok) location.href = location.pathname;  // Recargar en el nuevo modo.
    };
  }
}
function renderCluster(cluster) {
  clusterEnabled = !!cluster.enabled;
  $('cluster-off').hidden = clusterEnabled;
  $('cluster-on').hidden = !clusterEnabled;
  $('distributed-label').hidden = !clusterEnabled;
  $('train-card').hidden = !clusterEnabled || !['kepler', 'tess'].includes(mission);
  $('train-title').textContent = mission === 'tess' ? 'Entrenar RF de TESS' : 'Entrenar RF repartido';
  $('train-hint').textContent = mission === 'tess'
    ? 'Los equipos miden TOI con veredicto TFOPWG (planetas confirmados o conocidos frente a falsos positivos) y este PC entrena un RF propio de TESS.'
    : 'Los equipos miden KOI conocidos (planetas candidatos y falsos positivos) y este PC entrena el modelo. Con 500 por clase son unas 800 estrellas.';
  $('mode-badge').textContent = clusterEnabled ? 'Red local activa' : 'Solo este PC';
  $('mode-badge').classList.toggle('live', clusterEnabled);
  if (!clusterEnabled) return;
  const addresses = (cluster.addresses.length ? cluster.addresses : ['IP-de-este-PC']).map(a => `${a}:${cluster.port}`);
  $('cluster-address').textContent = addresses.join('  ·  ');
  $('cluster-token').textContent = cluster.token;
  $('cluster-discovery').checked = cluster.distribute_discovery;
  $('cluster-helpers').replaceChildren();
  if (!cluster.helpers.length) $('cluster-helpers').append(node('p', 'Ningún equipo conectado todavía.'));
  for (const helper of cluster.helpers) {
    const row = node('div', '');
    row.className = 'helper';
    const name = node('span', `${helper.online ? '🟢' : '⚪'} ${helper.name}`);
    if (helper.host) name.title = helper.host;  // El nombre del equipo, al pasar el ratón
    if (!helper.online) name.className = 'off';
    const current = helper.current ? 'estrella ' + helper.current.split(':')[1] : (helper.online ? 'esperando' : 'desconectado');
    row.append(name, node('span', `${current} · ${helper.completed} hechas${helper.failed ? ` · ${helper.failed} fallos` : ''}`));
    $('cluster-helpers').append(row);
  }
}

function renderDiscovery(discovery, cnnRunning) {
  $('discovery-eyebrow').textContent = mission === 'tess' ? 'Descubrimiento TESS: estrellas de 2 min sin TOI' : 'Descubrimiento Kepler';
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
    renderCluster(await api('/api/cluster').catch(() => ({enabled: false})));
    // Los repartos no ocupan este PC: no bloquean un análisis local.
    const scanRunning = jobs.some(job => job.kind !== 'cluster' && ['running', 'pausing', 'stopping'].includes(job.status));
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
    $('start').disabled = ($('distributed').checked && clusterEnabled) ? false : (scanRunning || cnn.status === 'running');
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
    $('current').textContent = state.kind === 'cluster' && typeof state.current === 'number' && !state.ids.includes(state.current)
      ? `${state.current} a la vez` : (state.current || '—');
    $('serious').textContent = state.triage?.serious_count || 0;
    $('progress').max = state.ids.length;
    $('progress').value = done;
    $('note').textContent = state.error || state.note || (state.kind === 'cluster' ? 'Reparto entre equipos de la red · cada estrella la analiza un ayudante y el coordinador valida el resultado.' : state.kind === 'blind' ? 'Cohorte TEST congelada. El control KIC 10854555 se ejecutará aparte, al terminar.' : 'Una estrella cada vez · Caché de curvas reutilizada · Configuración científica sin cambios.');
    $('resume').disabled = state.kind === 'cluster'
      ? (!clusterEnabled || ['running', 'completed'].includes(state.status))
      : (scanRunning || cnn.status === 'running' || state.status === 'completed');
    $('pause').disabled = state.status !== 'running' || state.requested_action === 'pause';
    $('stop').disabled = state.status !== 'running';
    $('log').textContent = state.log || 'Sin actividad. No se ha iniciado ningún análisis desde el panel.';
    const old = $('stars').value;
    $('stars').replaceChildren();
    const ids = [...state.ids, ...(state.control ? [state.control] : [])];
    ids.sort((a, b) => (state.stars[b] === 'complete') - (state.stars[a] === 'complete'));
    const seriousKics = new Set((state.triage?.serious || []).map(item => Number(item.kic)));
    for (const kic of ids) {
      const who = (state.assigned || {})[kic] || (state.done_by || {})[kic];
      const option = node('option', `${seriousKics.has(Number(kic)) ? '✦ ' : ''}${starName(kic, state.mission || 'kepler')} · ${labels[state.stars[kic]] || state.stars[kic]}${who ? ' · ' + who : ''}${kic === state.control ? ' (control)' : ''}`);
      option.value = kic;
      $('stars').append(option);
    }
    if (wantedKic && ids.map(String).includes(String(wantedKic))) {
      $('stars').value = String(wantedKic);
      wantedKic = null;
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
    const distributed = clusterEnabled && $('distributed').checked;
    const job = await api('/api/jobs', {kics: $('kics').value, distributed});
    selected = job.id;
    msg(distributed ? 'Reparto creado: los equipos conectados empezarán a pedir estrellas.' : 'Análisis iniciado.');
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

$('distributed').onchange = () => refresh();
$('train-start').onclick = async () => {
  const perClass = Number($('train-per-class').value);
  const what = mission === 'tess' ? 'TOI' : 'KOI';
  if (!confirm(`¿Entrenar el RF de ${mission === 'tess' ? 'TESS' : 'Kepler'} con ${perClass} ejemplos por clase? Los equipos descargarán curvas de ${what} conocidos; puede tardar horas.`)) return;
  try {
    const job = await api('/api/train/start', {per_class: perClass, mission});
    selected = job.id;
    msg(`Entrenamiento repartido iniciado: los equipos empezarán a medir ${what}.`);
    await refresh();
  } catch (error) { msg(error.message); }
};
$('cluster-discovery').onchange = async () => {
  try {
    await api('/api/cluster/settings', {distribute_discovery: $('cluster-discovery').checked});
    msg($('cluster-discovery').checked ? 'Los próximos lotes de descubrimiento se repartirán entre los equipos.' : 'Los próximos lotes se analizarán en este PC.');
  } catch (error) { msg(error.message); }
};
$('cluster-newtoken').onclick = async () => {
  if (!confirm('¿Cambiar el token? Los ayudantes tendrán que reconfigurarse (AYUDAR.bat reconfigurar).')) return;
  try { await api('/api/cluster/token', {}); msg('Token cambiado.'); await refresh(); } catch (error) { msg(error.message); }
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

loadMode().then(() => {
  if (mission === 'tess') {
    $('kics-label').textContent = 'Identificadores TIC (TESS)';
    $('kics').placeholder = '25155310, 261136679';
    $('kics-hint').textContent = 'Uno o varios TIC. Se analizan todos los sectores con curvas SPOC de 2 min (o FFI si no hay). El modelo RF es de Kepler: orientativo.';
  }
  refresh();
});
setInterval(refresh, 3000);
