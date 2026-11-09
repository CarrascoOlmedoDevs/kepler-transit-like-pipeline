'use strict';
const $ = id => document.getElementById(id);
let token = '';
let reviewLimit = 12;
let lastOverview = null;

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

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = text;
  if (className) element.className = className;
  return element;
}

async function api(path, body) {
  const options = body ? {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Panel-Token': token},
    body: JSON.stringify(body)} : {};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Error');
  return data;
}

const num = value => { const n = Number(value); return Number.isFinite(n) && String(value) !== '' ? n : null; };
const fixed = (value, digits) => { const n = num(value); return n === null ? '—' : n.toFixed(digits); };
const percent = value => { const n = num(value); return n === null ? 'sin modelo' : Math.round(n * 100) + ' %'; };
const known = row => String(row.known_koi).toLowerCase() === 'true';

function kpi(value, label, hint) {
  const box = node('div', null, 'kpi');
  box.append(node('strong', String(value)), node('span', label));
  if (hint) box.append(node('small', hint));
  return box;
}

function openLink(job, kic) {
  const link = node('a', 'Abrir →', 'open');
  link.href = `/control?job=${encodeURIComponent(job)}&kic=${encodeURIComponent(kic)}`;
  return link;
}

function followButton(kic, watched) {
  const button = node('button', watched ? '★ Siguiendo' : '☆ Seguir', watched ? 'secondary follow on' : 'secondary follow');
  button.onclick = async () => {
    try {
      if (watched) await api('/api/watch', {kic, action: 'remove'});
      else await api('/api/watch', {kic, action: 'add', note: prompt(`Nota para ${starName(kic)} (opcional):`) || ''});
      await refresh();
    } catch (error) { msg(error.message); }
  };
  return button;
}

function card(row, watched) {
  const box = node('article', null, 'star ' + row.label);
  if (/^candidate_[0-9]+_folded\.png$/.test(row.plot_file || '')) {
    const link = document.createElement('a');
    link.href = `/files/${row.job}/${row.kic}/${row.plot_file}`;
    link.target = '_blank';
    link.rel = 'noopener';
    const img = document.createElement('img');
    img.src = link.href;
    img.loading = 'lazy';
    img.alt = `Curva plegada de ${starName(row.kic)}`;
    img.onerror = () => link.remove();
    link.append(img);
    box.append(link);
  }
  const head = node('div', null, 'star-head');
  head.append(node('h3', starName(row.kic)));
  const knownToi = String(row.known_toi).toLowerCase() === 'true';
  head.append(known(row)
    ? node('span', `Ya conocida · ${row.matched_koi_name || 'KOI'}${row.known_disposition ? ' · ' + row.known_disposition : ''}`, 'tag known')
    : knownToi
      ? node('span', `Ya conocida · ${row.matched_toi_name}${row.known_toi_disposition ? ' · ' + row.known_toi_disposition : ''}`, 'tag known')
      : node('span', 'Señal nueva', 'tag new'));
  box.append(head);
  const facts = node('dl', null, 'facts');
  for (const [label, value] of [['Periodo', fixed(row.period_days, 4) + ' d'], ['Profundidad', fixed(row.depth_ppm, 0) + ' ppm'],
      ['SNR', fixed(row.depth_snr, 1)], ['Tránsitos', fixed(row.number_of_transits, 0)],
      ['RF', percent(row.rf_probability)], ['CNN', percent(row.cnn_probability)], ['Prioridad', fixed(row.priority_score, 1)]]) {
    const item = node('div');
    item.append(node('dt', label), node('dd', value));
    facts.append(item);
  }
  box.append(facts);
  box.append(node('p', row.reason, 'reason small muted'));
  if (row.vetting_flags) box.append(node('p', 'Alertas: ' + String(row.vetting_flags).split('|').join(', '), 'flags small'));
  const meta = node('p', `Candidato ${row.rank} · analizada ${new Date(row.analyzed * 1000).toLocaleDateString()}${row.by ? ' por ' + row.by : ''}`, 'small muted');
  box.append(meta);
  const actions = node('div', null, 'actions');
  actions.append(followButton(row.kic, watched), openLink(row.job, row.kic));
  box.append(actions);
  return box;
}

function msg(text) { $('message').textContent = text || ''; }

function render(data) {
  lastOverview = data;
  const watched = new Set(data.watchlist.map(w => Number(w.kic)));
  const labels = data.labels || {};
  $('status-badge').textContent = data.helpers ? `${data.helpers_online} de ${data.helpers} equipos conectados` : 'Solo este PC';
  $('status-badge').classList.toggle('live', data.helpers_online > 0);
  const n = value => Number(value || 0).toLocaleString('es-ES');
  $('kpis').replaceChildren(
    kpi(n(data.stars), 'estrellas analizadas'),
    kpi(n(data.candidates), 'señales detectadas'),
    kpi(n(labels.prioritario), 'serias'),
    kpi(n(labels.revisar), 'para revisar'),
    kpi(data.model ? Number(data.model.roc_auc).toLocaleString('es-ES', {maximumFractionDigits: 2}) : '—',
        data.model ? (mission === 'tess' ? 'AUC del modelo (orientativo en TESS)' : 'AUC del modelo RF') : 'modelo sin entrenar'),
  );
  $('no-model').hidden = !data.without_model;
  $('no-model').textContent = data.without_model
    ? `${data.without_model} estrella(s) se analizaron sin modelo RF: no pueden salir como prioritarias. Si quieres, vuelve a analizarlas desde Control.`
    : '';

  const priority = data.follow.filter(r => r.label === 'prioritario');
  const review = data.follow.filter(r => r.label === 'revisar');
  $('priority-count').textContent = `${priority.length} señal(es)`;
  $('review-count').textContent = `${review.length} señal(es)`;
  $('priority').replaceChildren(...priority.map(row => card(row, watched.has(row.kic))));
  if (!priority.length) $('priority').append(node('p', mission === 'tess'
    ? 'En TESS no se marcan prioritarias automáticamente: revisa las señales de abajo.'
    : 'Todavía ninguna. Con el modelo entrenado, las nuevas estrellas analizadas pueden aparecer aquí.', 'muted empty'));
  $('review').replaceChildren(...review.slice(0, reviewLimit).map(row => card(row, watched.has(row.kic))));
  if (!review.length) $('review').append(node('p', 'Nada pendiente de revisar.', 'muted empty'));
  $('review-more').hidden = review.length <= reviewLimit;

  $('watchlist').replaceChildren();
  if (!data.watchlist.length) $('watchlist').append(node('p', 'Marca estrellas con ☆ Seguir para tenerlas siempre a mano.', 'muted empty'));
  for (const entry of data.watchlist) {
    const row = node('div', null, 'watch-row');
    const text = node('div');
    text.append(node('strong', starName(entry.kic)), node('span', entry.note || 'sin nota', 'muted small'));
    const actions = node('div', null, 'actions');
    if (entry.job) actions.append(openLink(entry.job, entry.kic));
    else actions.append(node('span', 'aún sin analizar', 'muted small'));
    const remove = node('button', 'Quitar', 'secondary');
    remove.onclick = async () => { await api('/api/watch', {kic: entry.kic, action: 'remove'}); refresh(); };
    actions.append(remove);
    row.append(text, actions);
    $('watchlist').append(row);
  }
}

async function refresh() {
  try {
    if (!token) token = (await api('/api/state')).token;
    render(await api('/api/overview?mission=' + mission));
    msg('');
  } catch (error) {
    msg('No se pudo actualizar: ' + error.message);
  }
}

$('watch-form').onsubmit = async event => {
  event.preventDefault();
  try {
    await api('/api/watch', {kic: Number($('watch-kic').value.trim()), action: 'add', note: $('watch-note').value});
    $('watch-kic').value = '';
    $('watch-note').value = '';
    await refresh();
  } catch (error) { msg(error.message); }
};
$('review-more').onclick = () => { reviewLimit += 12; render(lastOverview); };

loadMode().then(() => {
  $('home-eyebrow').textContent = mission === 'tess' ? 'Resumen del observatorio en TESS' : 'Resumen del observatorio en Kepler';
  $('watch-kic').placeholder = PREFIX[mission];
  if (mission === 'tess') document.querySelector('#priority').closest('.card').querySelector('p.muted').textContent =
    'En TESS el modelo es el de Kepler (no calibrado): ninguna señal sale como prioritaria sin revisión. Mira "A revisar".';
  refresh();
});
setInterval(refresh, 20000);
