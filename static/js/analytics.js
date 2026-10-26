// Grafik dashboard analitik (Nilai aset & Kinerja maintenance). Chart.js 4.
// Palet: satu seri = brand-500; dua seri = ramp brand terang/gelap (lolos validasi ordinal & kontras).
document.addEventListener('DOMContentLoaded', () => {
  const read = id => { const el = document.getElementById(id); return el ? JSON.parse(el.textContent) : null; };
  const css = getComputedStyle(document.documentElement);
  const c = name => css.getPropertyValue(name).trim();
  const BRAND = c('--brand-500'), DARK = '#2A31D8', LIGHT = '#8FA6FF';

  const empty = (id, text) => {
    const box = document.getElementById(id)?.closest('.chart-box');
    if (box) box.outerHTML = `<div class="empty chart-empty"><strong>Belum ada data</strong>${text}</div>`;
  };
  const total = arr => (arr || []).reduce((a, b) => a + (b || 0), 0);
  const rp = v => {
    const n = Math.abs(v);
    if (n >= 1e9) return 'Rp ' + (v / 1e9).toLocaleString('id-ID', { maximumFractionDigits: 2 }) + ' M';
    if (n >= 1e6) return 'Rp ' + (v / 1e6).toLocaleString('id-ID', { maximumFractionDigits: 1 }) + ' jt';
    if (n >= 1e3) return 'Rp ' + (v / 1e3).toLocaleString('id-ID', { maximumFractionDigits: 0 }) + ' rb';
    return 'Rp ' + Number(v).toLocaleString('id-ID');
  };
  const rpFull = v => 'Rp ' + Math.round(v).toLocaleString('id-ID');

  if (typeof Chart === 'undefined') return;
  Chart.defaults.font.family = c('--sans');
  Chart.defaults.font.size = 13;
  Chart.defaults.color = c('--ink-3');
  const tooltip = { backgroundColor: c('--ink'), cornerRadius: 8, padding: 10, boxPadding: 4, usePointStyle: true };
  const legend = { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: 'rectRounded', color: c('--ink-2') } };
  const grid = { color: c('--rule-soft') };
  const base = { responsive: true, maintainAspectRatio: false, animation: { duration: 400 } };
  // Batang: 4px membulat di ujung data, 2px celah warna permukaan antar segmen
  const bar = color => ({ backgroundColor: color, borderColor: c('--panel'), borderWidth: { top: 0, bottom: 0, left: 0, right: 0 }, borderRadius: 4, borderSkipped: 'start', maxBarThickness: 24 });

  // ── Nilai aset: nilai buku + akumulasi penyusutan per grup (batang horizontal bertumpuk) ──
  const groups = read('chart-groups');
  if (groups) {
    if (!total(groups.book) && !total(groups.accum)) empty('groupChart', 'Belum ada aset yang dinilai.');
    else new Chart(document.getElementById('groupChart'), {
      type: 'bar',
      data: { labels: groups.labels, datasets: [
        { label: 'Nilai buku', data: groups.book, ...bar(DARK), borderRadius: 0 },
        { label: 'Akumulasi penyusutan', data: groups.accum, ...bar(LIGHT), borderWidth: { left: 2 }, borderSkipped: false },
      ]},
      options: { ...base, indexAxis: 'y',
        plugins: { legend, tooltip: { ...tooltip, callbacks: { label: i => `${i.dataset.label}: ${rpFull(i.raw)}` } } },
        scales: { x: { stacked: true, grid, border: { display: false }, ticks: { callback: rp, maxTicksLimit: 6 } },
                  y: { stacked: true, grid: { display: false }, border: { display: false }, ticks: { color: c('--ink-2') } } } },
    });
  }

  // ── Nilai aset: perolehan per tahun beli (kolom, satu seri) ──
  const years = read('chart-years');
  if (years) {
    if (!total(years.cost)) empty('yearChart', 'Belum ada aset yang dinilai.');
    else new Chart(document.getElementById('yearChart'), {
      type: 'bar',
      data: { labels: years.labels, datasets: [{ label: 'Nilai perolehan', data: years.cost, ...bar(BRAND) }] },
      options: { ...base,
        plugins: { legend: { display: false }, tooltip: { ...tooltip, callbacks: { label: i => rpFull(i.raw) } } },
        scales: { x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
                  y: { beginAtZero: true, grid, border: { display: false }, ticks: { callback: rp, maxTicksLimit: 5 } } } },
    });
  }

  // ── Kinerja: biaya per bulan (perbaikan vs rutin, kolom bertumpuk) ──
  const cost = read('chart-cost');
  if (cost) {
    if (!total(cost.routine) && !total(cost.corrective)) empty('costChart', 'Tidak ada biaya maintenance dalam 12 bulan terakhir.');
    else new Chart(document.getElementById('costChart'), {
      type: 'bar',
      data: { labels: cost.labels, datasets: [
        { label: 'Perbaikan (corrective)', data: cost.corrective, ...bar(DARK), borderRadius: 0 },
        { label: 'Perawatan rutin & inspeksi', data: cost.routine, ...bar(LIGHT), borderWidth: { bottom: 2 }, borderSkipped: false },
      ]},
      options: { ...base, interaction: { mode: 'index', intersect: false },
        plugins: { legend, tooltip: { ...tooltip, callbacks: {
          label: i => `${i.dataset.label}: ${rpFull(i.raw)}`,
          footer: items => 'Total: ' + rpFull(items.reduce((a, i) => a + i.raw, 0)),
        } } },
        scales: { x: { stacked: true, grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
                  y: { stacked: true, beginAtZero: true, grid, border: { display: false }, ticks: { callback: rp, maxTicksLimit: 5 } } } },
    });
  }

  // ── Kinerja: kepatuhan per bulan (garis, satu seri, 0–100%) ──
  const comp = read('chart-compliance');
  if (comp) {
    if (!comp.some(v => v !== null)) empty('compChart', 'Belum ada jadwal rutin yang jatuh tempo.');
    else new Chart(document.getElementById('compChart'), {
      type: 'line',
      data: { labels: cost ? cost.labels : comp.map((_, i) => i + 1), datasets: [{
        label: 'Tepat waktu', data: comp, borderColor: BRAND, backgroundColor: 'rgba(70, 95, 255, .10)', fill: 'origin',
        borderWidth: 2, tension: .3, spanGaps: true, pointRadius: 4, pointHoverRadius: 6,
        pointBackgroundColor: BRAND, pointBorderColor: c('--panel'), pointBorderWidth: 2,
      }]},
      options: { ...base, interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: false }, tooltip: { ...tooltip, callbacks: { label: i => i.raw === null ? 'Tidak ada jadwal' : `${i.raw}% tepat waktu` } } },
        scales: { x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
                  y: { min: 0, max: 100, grid, border: { display: false }, ticks: { stepSize: 25, callback: v => v + '%' } } } },
    });
  }

  // ── Kinerja: biaya per kategori (batang horizontal, satu seri) ──
  const cats = read('chart-cats');
  if (cats) {
    if (!total(cats.cost)) empty('catChart', 'Tidak ada biaya maintenance dalam 12 bulan terakhir.');
    else new Chart(document.getElementById('catChart'), {
      type: 'bar',
      data: { labels: cats.labels, datasets: [{ label: 'Biaya', data: cats.cost, ...bar(BRAND) }] },
      options: { ...base, indexAxis: 'y',
        plugins: { legend: { display: false }, tooltip: { ...tooltip, callbacks: { label: i => rpFull(i.raw) } } },
        scales: { x: { beginAtZero: true, grid, border: { display: false }, ticks: { callback: rp, maxTicksLimit: 5 } },
                  y: { grid: { display: false }, border: { display: false }, ticks: { color: c('--ink-2') } } } },
    });
  }
});
