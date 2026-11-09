'use strict';
/* Cielo de fondo (estático) y tránsito en directo de la portada. Sin red, sin bucles innecesarios. */
(() => {
  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  // Colores de cuerpo negro aproximados: de estrellas O/B (azul) a M (rojo).
  const TINTS = ['#9bb0ff', '#aabfff', '#cad7ff', '#f8f7ff', '#fff4ea', '#ffe4c4', '#ffd2a1', '#ffbd6f'];
  const WEIGHTS = [2, 4, 10, 22, 26, 18, 12, 6];

  function seeded(seed) {
    return () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
  }
  function pick(rand) {
    let r = rand() * WEIGHTS.reduce((a, b) => a + b, 0);
    for (let i = 0; i < TINTS.length; i++) { r -= WEIGHTS[i]; if (r <= 0) return TINTS[i]; }
    return TINTS[3];
  }

  function drawSky() {
    let canvas = document.getElementById('sky');
    if (!canvas) {
      canvas = document.createElement('canvas');
      canvas.id = 'sky';
      canvas.setAttribute('aria-hidden', 'true');
      document.body.prepend(canvas);
    }
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const w = window.innerWidth, h = window.innerHeight;
    canvas.width = w * ratio;
    canvas.height = h * ratio;
    const ctx = canvas.getContext('2d');
    ctx.scale(ratio, ratio);
    const rand = seeded(20261109);
    // Banda tenue tipo Vía Láctea en diagonal.
    const band = ctx.createLinearGradient(0, h, w, 0);
    band.addColorStop(0.2, 'rgba(120,110,220,0)');
    band.addColorStop(0.5, 'rgba(120,130,230,0.07)');
    band.addColorStop(0.8, 'rgba(120,110,220,0)');
    ctx.fillStyle = band;
    ctx.fillRect(0, 0, w, h);
    const count = Math.round(w * h / 2600);
    for (let i = 0; i < count; i++) {
      const x = rand() * w, y = rand() * h;
      // Más densidad cerca de la banda diagonal.
      const near = Math.abs((x / w) + (y / h) - 1);
      if (near > 0.35 && rand() < 0.45) continue;
      const big = rand() < 0.035;
      const r = big ? 1.1 + rand() * 1.1 : 0.35 + rand() * 0.6;
      ctx.globalAlpha = big ? 0.95 : 0.25 + rand() * 0.55;
      ctx.fillStyle = pick(rand);
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
      if (big) {  // Halo suave en las estrellas brillantes.
        const glow = ctx.createRadialGradient(x, y, 0, x, y, r * 6);
        glow.addColorStop(0, 'rgba(255,255,255,0.18)');
        glow.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.fillStyle = glow;
        ctx.fillRect(x - r * 6, y - r * 6, r * 12, r * 12);
      }
    }
    ctx.globalAlpha = 1;
  }

  /* Tránsito: el planeta cruza el disco (con oscurecimiento del limbo) y la curva de luz se dibuja a la vez. */
  function transit(canvas) {
    const ctx = canvas.getContext('2d');
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    let w, h, cx, cy, R, top, plotH;
    const PERIOD = 14000;                 // ms por tránsito completo
    const color = () => getComputedStyle(document.body).getPropertyValue('--accent').trim() || '#ffc86b';
    function size() {
      const box = canvas.getBoundingClientRect();
      w = box.width; h = box.height;
      canvas.width = w * ratio; canvas.height = h * ratio;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      R = Math.min(w * 0.27, h * 0.3);
      cx = w / 2; cy = h * 0.4; top = h * 0.86; plotH = h * 0.11;
    }
    const planet = () => R * 0.2;
    // Flujo relativo según la fracción del planeta sobre el disco (con limbo oscurecido).
    function flux(px) {
      const r = planet();
      const d = Math.abs(px - cx);
      if (d >= R + r) return 1;
      const overlap = d <= R - r ? 1 : (R + r - d) / (2 * r);
      const mu = Math.sqrt(Math.max(0, 1 - Math.min(1, d / R) ** 2));
      const limb = 1 - 0.6 * (1 - mu);
      return 1 - overlap * (r * r) / (R * R) * limb * 1.35;
    }
    const xAt = t => cx - (R + planet() * 1.8) + t * 2 * (R + planet() * 1.8);
    function frame(now) {
      const t = still ? 0.5 : (now % PERIOD) / PERIOD;
      ctx.clearRect(0, 0, w, h);
      const accent = color();
      // Disco estelar con oscurecimiento del limbo.
      const reach = Math.min(R * 1.75, cx - 2, cy - 2);  // El halo nunca toca el borde del lienzo.
      const glow = ctx.createRadialGradient(cx, cy, R * 0.95, cx, cy, reach);
      glow.addColorStop(0, accent + '50');
      glow.addColorStop(1, accent + '00');
      ctx.fillStyle = glow;
      ctx.beginPath(); ctx.arc(cx, cy, reach, 0, Math.PI * 2); ctx.fill();
      const disc = ctx.createRadialGradient(cx - R * 0.15, cy - R * 0.15, R * 0.05, cx, cy, R);
      disc.addColorStop(0, '#fffaf0');
      disc.addColorStop(0.55, accent);
      disc.addColorStop(1, accent + 'aa');
      ctx.fillStyle = disc;
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();
      // Planeta.
      const px = xAt(t);
      ctx.fillStyle = '#05081a';
      ctx.beginPath(); ctx.arc(px, cy + R * 0.12, planet(), 0, Math.PI * 2); ctx.fill();
      // Curva de luz hasta el instante actual.
      const left = w * 0.08, right = w * 0.92;
      ctx.strokeStyle = 'rgba(154,166,207,.25)';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(left, top); ctx.lineTo(right, top); ctx.stroke();
      ctx.strokeStyle = accent;
      ctx.lineWidth = 2;
      ctx.beginPath();
      const steps = 220;
      for (let i = 0; i <= steps * t; i++) {
        const s = i / steps;
        const y = top + (1 - flux(xAt(s))) * plotH * 9;
        const x = left + s * (right - left);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();
      const s = t, y = top + (1 - flux(xAt(s))) * plotH * 9, x = left + s * (right - left);
      ctx.fillStyle = accent;
      ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.fill();
      if (!still && !document.hidden) requestAnimationFrame(frame);
    }
    size();
    window.addEventListener('resize', () => { size(); if (still) frame(0); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden && !still) requestAnimationFrame(frame); });
    requestAnimationFrame(frame);
  }

  drawSky();
  let pending;
  window.addEventListener('resize', () => { clearTimeout(pending); pending = setTimeout(drawSky, 150); });
  const hero = document.getElementById('transit');
  if (hero) transit(hero);
})();
