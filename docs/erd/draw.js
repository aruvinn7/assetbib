// Primitif gambar SVG untuk ERD.
const { C, TW, HEAD_H, ROW_H, tables, layout } = require('./data');

const esc = (s) => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const tableH = (t) => HEAD_H + tables[t].cols.length * ROW_H;

const rowY = (t, colName) => {
  const i = tables[t].cols.findIndex((c) => c[0] === colName);
  return layout[t].y + HEAD_H + i * ROW_H + ROW_H / 2;
};

const BADGE = {
  PK: { fg: '#b91c1c', bg: '#fee2e2' },
  FK: { fg: '#0369a1', bg: '#e0f2fe' },
  U: { fg: '#6d28d9', bg: '#ede9fe' },
};

function badge(x, cy, key) {
  const b = BADGE[key];
  const w = key === 'PK' ? 22 : 22;
  const h = 11;
  return `<g>
    <rect x="${x}" y="${cy - h / 2}" width="${w}" height="${h}" rx="3" fill="${b.bg}"/>
    <text x="${x + w / 2}" y="${cy + 2.9}" font-size="7" font-weight="700"
      text-anchor="middle" fill="${b.fg}" font-family="Arial">${key}</text>
  </g>`;
}

function tableSvg(name) {
  const t = tables[name];
  const { x, y } = layout[name];
  const w = TW;
  const h = tableH(name);
  const header = `M ${x} ${y + HEAD_H} L ${x} ${y + 7} Q ${x} ${y} ${x + 7} ${y}
    L ${x + w - 7} ${y} Q ${x + w} ${y} ${x + w} ${y + 7} L ${x + w} ${y + HEAD_H} Z`;

  const rows = t.cols.map(([cn, ty, key], i) => {
    const cy = y + HEAD_H + i * ROW_H + ROW_H / 2;
    const out = [];
    out.push(`<text x="${x + 9}" y="${cy + 3.2}" font-size="9" fill="${C.text}"
      font-family="Arial">${esc(cn)}</text>`);
    out.push(`<text x="${x + 132}" y="${cy + 3.2}" font-size="8.2" fill="${C.muted}"
      font-family="Arial">${esc(ty)}</text>`);
    if (key) out.push(badge(x + w - 30, cy, key));
    return out.join('');
  }).join('');

  return `<g>
    <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="7" fill="#ffffff"
      stroke="#b9c5d4" stroke-width="0.9"/>
    <path d="${header}" fill="${t.color}"/>
    <text x="${x + 9}" y="${y + 14.6}" font-size="9.6" font-weight="700"
      fill="#ffffff" font-family="Arial">${esc(name)}</text>
    <line x1="${x}" y1="${y + HEAD_H}" x2="${x + w}" y2="${y + HEAD_H}"
      stroke="${t.color}" stroke-width="1.2"/>
    ${rows}
  </g>`;
}

function panelSvg(p) {
  const bottom = Math.max(...p.members.map((m) => layout[m].y + tableH(m)));
  const h = bottom - p.y + 12;
  return `<g>
    <rect x="${p.x}" y="${p.y}" width="248" height="${h}" rx="10"
      fill="${C.panel}" stroke="#cbd5e1" stroke-width="0.9" stroke-dasharray="5 4"/>
    <text x="${p.x + 10}" y="${p.y + 15}" font-size="9" font-weight="700"
      letter-spacing="0.6" fill="#94a3b8" font-family="Arial">${esc(p.label)}</text>
  </g>`;
}

// polyline dengan sudut membulat
function roundedPath(pts, r = 6) {
  const p = pts.filter((pt, i) => i === 0
    || Math.abs(pt[0] - pts[i - 1][0]) > 0.01
    || Math.abs(pt[1] - pts[i - 1][1]) > 0.01);
  if (p.length < 2) return '';
  let d = `M ${p[0][0]} ${p[0][1]}`;
  for (let i = 1; i < p.length - 1; i++) {
    const a = p[i - 1], b = p[i], c = p[i + 1];
    const d1 = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const d2 = Math.hypot(c[0] - b[0], c[1] - b[1]);
    const rr = Math.min(r, d1 / 2, d2 / 2);
    const p1 = [b[0] + (a[0] - b[0]) / d1 * rr, b[1] + (a[1] - b[1]) / d1 * rr];
    const p2 = [b[0] + (c[0] - b[0]) / d2 * rr, b[1] + (c[1] - b[1]) / d2 * rr];
    d += ` L ${p1[0].toFixed(2)} ${p1[1].toFixed(2)} Q ${b[0]} ${b[1]} ${p2[0].toFixed(2)} ${p2[1].toFixed(2)}`;
  }
  const last = p[p.length - 1];
  d += ` L ${last[0]} ${last[1]}`;
  return d;
}

// kaki gagak (sisi many) di titik px pada tabel
function crowFoot(px, py, side) {
  const s = side === 'left' ? -1 : 1;
  const apex = px + s * 8;
  return `<path d="M ${apex} ${py} L ${px} ${py - 4} M ${apex} ${py} L ${px} ${py} M ${apex} ${py} L ${px} ${py + 4}"
    stroke="${C.line}" stroke-width="1" fill="none"/>`;
}

// garis penanda sisi "one"
function oneTick(px, py, side, n = 1) {
  const s = side === 'left' ? 1 : -1;
  let out = '';
  for (let i = 0; i < n; i++) {
    const bx = px + s * (5 + i * 4);
    out += `<line x1="${bx}" y1="${py - 4.5}" x2="${bx}" y2="${py + 4.5}" stroke="${C.line}" stroke-width="1"/>`;
  }
  return out;
}

function wrap(text, max) {
  const words = text.split(' ');
  const lines = [];
  let cur = '';
  for (const w of words) {
    if ((cur + ' ' + w).trim().length > max) {
      if (cur) lines.push(cur);
      cur = w;
    } else {
      cur = (cur + ' ' + w).trim();
    }
  }
  if (cur) lines.push(cur);
  return lines;
}

module.exports = { esc, tableH, rowY, tableSvg, panelSvg, roundedPath, crowFoot, oneTick, wrap, badge };
