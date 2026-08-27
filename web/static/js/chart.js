// 의존성 없는 간단한 SVG 라인차트
function drawChart(elId, points, opts) {
  opts = opts || {};
  const el = document.getElementById(elId);
  const W = el.clientWidth || 600, H = el.clientHeight || 150;
  const pad = { l: 40, r: 8, t: 8, b: 18 };
  const xs = points.map(p => p.t);
  const ys = points.map(p => p.v).filter(v => v !== null && v !== undefined && !isNaN(v));
  if (ys.length < 2) { el.innerHTML = '<svg><text x="8" y="20" class="lbl">데이터 없음</text></svg>'; return; }
  let ymin = Math.min.apply(null, ys), ymax = Math.max.apply(null, ys);
  if (opts.ymin !== undefined) ymin = Math.min(ymin, opts.ymin);
  if (opts.ymax !== undefined) ymax = Math.max(ymax, opts.ymax);
  if (ymin === ymax) { ymin -= 1; ymax += 1; }
  const xmin = Math.min.apply(null, xs), xmax = Math.max.apply(null, xs);
  const X = t => pad.l + (xmax === xmin ? 0 : (t - xmin) / (xmax - xmin)) * (W - pad.l - pad.r);
  const Y = v => pad.t + (1 - (v - ymin) / (ymax - ymin)) * (H - pad.t - pad.b);

  let g = '';
  for (let i = 0; i <= 3; i++) {
    const v = ymin + (ymax - ymin) * i / 3;
    const y = Y(v);
    g += `<line class="grid-line" x1="${pad.l}" y1="${y}" x2="${W - pad.r}" y2="${y}"/>`;
    g += `<text class="lbl" x="4" y="${y + 3}">${v.toFixed(opts.dec ?? 1)}</text>`;
  }
  const x0 = new Date(xmin * 1000), x1 = new Date(xmax * 1000);
  g += `<text class="lbl" x="${pad.l}" y="${H - 4}">${x0.getHours()}:${String(x0.getMinutes()).padStart(2,'0')}</text>`;
  g += `<text class="lbl" x="${W - pad.r - 28}" y="${H - 4}">${x1.getHours()}:${String(x1.getMinutes()).padStart(2,'0')}</text>`;

  let d = '', penUp = true;
  points.forEach(p => {
    if (p.v === null || p.v === undefined || isNaN(p.v)) { penUp = true; return; }
    d += (penUp ? 'M' : 'L') + X(p.t).toFixed(1) + ',' + Y(p.v).toFixed(1) + ' ';
    penUp = false;
  });
  const line = `<path class="line" d="${d.trim()}" ${opts.color ? `style="stroke:${opts.color}"` : ''}/>`;
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${g}${line}</svg>`;
}
