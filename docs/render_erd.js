/**
 * Renderer ERD AssetMS - domain Aset & Maintenance (model ringkas).
 * Layout ditentukan manual: tabel disusun dalam 3 kolom, garis relasi hanya
 * melewati koridor antar-kolom sehingga tidak menabrak tabel/teks.
 *
 * Pakai: node docs/render_erd.js docs
 * Output: erd_aset_maintenance.svg + erd_aset_maintenance.png
 */
const fs = require('fs');
const path = require('path');

let sharp;
try {
  sharp = require('sharp');
} catch (e) {
  sharp = require(process.env.TEMP + '\\dbmlcheck\\node_modules\\sharp');
}

const { C, TW, HEAD_H, ROW_H, tables, layout, panels, edges, colIndex, W, H } = require('./erd/data');
const D = require('./erd/draw');

const OUT = process.argv[2] || '.';
const SCALE = 2;

const box = (t) => ({ x: layout[t].x, y: layout[t].y, w: TW, h: D.tableH(t) });

// koridor vertikal tempat garis berjalan
const GAP = {
  outerL: { x1: 8, x2: 52 },
  gap1: { x1: 326, x2: 462 },
  gap2: { x1: 718, x2: 880 },
  outerR: { x1: 1134, x2: 1154 },
};

// ---------------------------------------------------------------- relasi
const list = edges.map((e, i) => {
  const [ft, fc] = e.f;
  const tt = e.t;
  const sameCol = colIndex(ft) === colIndex(tt);
  let sideSrc, sideDst, gap;

  if (sameCol) {
    const c = colIndex(ft);
    if (ft === tt) { sideSrc = 'left'; sideDst = 'left'; gap = 'outerL'; }
    else if (c === 2) { sideSrc = 'right'; sideDst = 'right'; gap = 'outerR'; }
    else if (c === 0) { sideSrc = 'left'; sideDst = 'left'; gap = 'outerL'; }
    else { sideSrc = 'left'; sideDst = 'left'; gap = 'gap1'; }
  } else {
    const dir = colIndex(ft) > colIndex(tt) ? 'left' : 'right';
    sideSrc = dir;
    sideDst = dir === 'left' ? 'right' : 'left';
    gap = colIndex(ft) === 2 ? 'gap2' : (colIndex(ft) === 1 ? 'gap1' : 'outerL');
  }

  return {
    ...e, id: i, ft, fc, tt, sameCol, sideSrc, sideDst, gap,
    sy: D.rowY(ft, fc),
    dy: 0,
    lane: 0,
  };
});

// titik masuk ke tabel induk disebar agar tidak menumpuk di satu baris
const entryGroups = {};
for (const e of list) {
  const k = e.tt + '|' + e.sideDst;
  (entryGroups[k] = entryGroups[k] || []).push(e);
}
// Semua relasi masuk pada baris primary key tabel induk; beberapa relasi
// yang menuju tabel yang sama disebar sedikit agar penanda tidak menumpuk.
for (const k of Object.keys(entryGroups)) {
  const g = entryGroups[k].sort((a, b) => a.sy - b.sy);
  const b = box(g[0].tt);
  const pkY = b.y + HEAD_H + ROW_H / 2;
  const n = g.length;
  g.forEach((e, i) => {
    e.dy = pkY + (n === 1 ? 0 : (i - (n - 1) / 2) * 9);
  });
}

// relasi diri masuk kembali ke baris primary key agar penanda "one" terbaca
for (const e of list) if (e.ft === e.tt) e.dy = layout[e.tt].y + HEAD_H + ROW_H / 2;

// alokasi lajur per koridor
const laneGroups = {};
for (const e of list) (laneGroups[e.gap] = laneGroups[e.gap] || []).push(e);
for (const g of Object.keys(laneGroups)) {
  const arr = laneGroups[g].sort((a, b) => (a.sameCol - b.sameCol) || (a.sy - b.sy));
  const { x1, x2 } = GAP[g];
  const step = (x2 - x1) / (arr.length + 1);
  arr.forEach((e, i) => { e.lane = x1 + step * (i + 1); });
}

function pathFor(e) {
  const a = box(e.ft);
  const b = box(e.tt);
  const sx = e.sideSrc === 'left' ? a.x : a.x + a.w;
  const dx = e.sideDst === 'left' ? b.x : b.x + b.w;
  return [[sx, e.sy], [e.lane, e.sy], [e.lane, e.dy], [dx, e.dy]];
}

function edgeSvg(e) {
  const pts = pathFor(e);
  const d = D.roundedPath(pts, 6);
  let style;
  if (e.k === 'a') style = `stroke="${C.audit}" stroke-width="1" stroke-dasharray="4 3"`;
  else if (e.k === '1') style = `stroke="${C.one}" stroke-width="1.2"`;
  else style = `stroke="${C.line}" stroke-width="1.1"`;

  let marks = '';
  if (e.k !== 'a') {
    marks += D.crowFoot(pts[0][0], e.sy, e.sideSrc);
    marks += D.oneTick(pts[pts.length - 1][0], e.dy, e.sideDst, e.k === '1' ? 2 : 1);
  }
  return `<path d="${d}" fill="none" ${style} stroke-linejoin="round"/>${marks}`;
}

// ---------------------------------------------------------------- legenda
function legendSvg() {
  const x = 68, y = 306, w = 248, h = 126;
  const rows = [];
  rows.push(`<text x="${x + 12}" y="${y + 20}" font-size="9" font-weight="700"
    letter-spacing="0.6" fill="#94a3b8" font-family="Arial">KETERANGAN</text>`);

  rows.push(D.badge(x + 12, y + 41, 'PK'));
  rows.push(`<text x="${x + 40}" y="${y + 44}" font-size="8.6" fill="${C.text}" font-family="Arial">Primary key</text>`);
  rows.push(D.badge(x + 126, y + 41, 'FK'));
  rows.push(`<text x="${x + 154}" y="${y + 44}" font-size="8.6" fill="${C.text}" font-family="Arial">Foreign key</text>`);

  rows.push(D.badge(x + 12, y + 61, 'U'));
  rows.push(`<text x="${x + 40}" y="${y + 64}" font-size="8.6" fill="${C.text}" font-family="Arial">Nilai unik (alternate key)</text>`);

  rows.push(`<line x1="${x + 12}" y1="${y + 86}" x2="${x + 36}" y2="${y + 86}" stroke="${C.line}" stroke-width="1.2"/>`);
  rows.push(`<text x="${x + 44}" y="${y + 89}" font-size="8.6" fill="${C.text}" font-family="Arial">Relasi many-to-one</text>`);
  rows.push(`<line x1="${x + 12}" y1="${y + 106}" x2="${x + 36}" y2="${y + 106}" stroke="${C.audit}" stroke-width="1.2" stroke-dasharray="4 3"/>`);
  rows.push(`<text x="${x + 44}" y="${y + 109}" font-size="8.6" fill="${C.text}" font-family="Arial">Jejak pelaku (audit)</text>`);

  return `<g><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10" fill="${C.panel}"
    stroke="#cbd5e1" stroke-dasharray="5 4" stroke-width="0.9"/>${rows.join('')}</g>`;
}

function noteSvg() {
  const x = 68, y = 450, w = 248, h = 112;
  const items = [
    'Preventif: id_laporan_kerusakan kosong. Korektif: terisi laporan.',
    'Status selesai diisi saat tanggal_pelaksanaan terisi.',
    'Satu aset hanya boleh punya satu laporan aktif.',
  ];
  const out = [`<text x="${x + 12}" y="${y + 20}" font-size="9" font-weight="700"
    letter-spacing="0.6" fill="#94a3b8" font-family="Arial">CATATAN</text>`];
  let ly = y + 40;
  for (const it of items) {
    D.wrap(it, 47).forEach((ln, i) => {
      out.push(`<text x="${x + 12 + (i === 0 ? 0 : 8)}" y="${ly}" font-size="8.2"
        fill="${C.muted}" font-family="Arial">${i === 0 ? '• ' : ''}${D.esc(ln)}</text>`);
      ly += 12;
    });
    ly += 2;
  }
  return `<g><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10" fill="${C.panel}"
    stroke="#cbd5e1" stroke-dasharray="5 4" stroke-width="0.9"/>${out.join('')}</g>`;
}

function titleSvg() {
  return `<text x="68" y="32" font-size="15" font-weight="700" fill="${C.text}"
      font-family="Arial">ERD Logis AssetMS — Domain Aset &amp; Maintenance</text>
    <text x="68" y="50" font-size="9.6" fill="${C.muted}" font-family="Arial">
      PT Borneo Indobara · 6 entitas · 11 relasi · data bagian aset dan jadwal maintenance dilebur ke entitasnya</text>`;
}

// ---------------------------------------------------------------- rakit
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W * SCALE}" height="${H * SCALE}" viewBox="0 0 ${W} ${H}">
  <rect width="${W}" height="${H}" fill="#ffffff"/>
  ${titleSvg()}
  ${panels.map(D.panelSvg).join('\n  ')}
  <g>${list.map(edgeSvg).join('')}</g>
  ${Object.keys(tables).map(D.tableSvg).join('\n  ')}
  ${legendSvg()}
  ${noteSvg()}
</svg>`;

fs.writeFileSync(path.join(OUT, 'erd_aset_maintenance.svg'), svg, 'utf8');

sharp(Buffer.from(svg))
  .png({ compressionLevel: 9 })
  .toFile(path.join(OUT, 'erd_aset_maintenance.png'))
  .then((info) => console.log(`ok ${info.width}x${info.height} -> ${path.join(OUT, 'erd_aset_maintenance.png')}`))
  .catch((e) => { console.error(e); process.exit(1); });
