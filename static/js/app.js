// AssetMS — interaksi global: toast, palet perintah (Ctrl+K), dan dialog "Catat maintenance".
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const FETCH = { 'X-Requested-With': 'fetch' };
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ── Toast ──────────────────────────────────────────────
  const SIGN = { success: 'safe', error: 'danger', info: 'mandatory' };
  function toast(msg, kind = 'success') {
    const host = $('#toasts');
    if (!host) return;
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.innerHTML = `<svg class="sign" aria-hidden="true"><use href="#sign-${SIGN[kind] || 'mandatory'}"/></svg><span>${esc(msg)}</span>` +
      '<button type="button" class="toast-close" aria-label="Tutup notifikasi"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button>';
    const dismiss = () => { el.classList.add('is-leaving'); setTimeout(() => el.remove(), 180); };
    el.querySelector('button').addEventListener('click', dismiss);
    host.append(el);
    setTimeout(dismiss, kind === 'error' ? 9000 : 5500);
  }
  // Toast yang dititipkan sebelum muat ulang halaman
  try {
    const pending = sessionStorage.getItem('pendingToast');
    if (pending) { sessionStorage.removeItem('pendingToast'); const p = JSON.parse(pending); toast(p.msg, p.kind); }
  } catch (e) {}

  // ── Segarkan bagian halaman bertanda [data-refresh][id]; tanpa itu muat ulang ──
  async function refresh(msg) {
    const regions = [...document.querySelectorAll('[data-refresh][id]')];
    if (!regions.length) {
      try { if (msg) sessionStorage.setItem('pendingToast', JSON.stringify({ msg, kind: 'success' })); } catch (e) {}
      location.reload();
      return;
    }
    if (msg) toast(msg);
    try {
      const res = await fetch(location.href);
      const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
      regions.forEach(r => { const fresh = doc.getElementById(r.id); if (fresh) r.replaceWith(fresh); });
      document.dispatchEvent(new CustomEvent('app:refreshed'));
    } catch (e) { location.reload(); }
  }

  // ── Aset yang terakhir dibuka (untuk palet) ──
  function rememberAsset(a) {
    if (!a || !a.id || !a.url) return;
    try {
      const list = JSON.parse(localStorage.getItem('recentAssets') || '[]').filter(x => String(x.id) !== String(a.id));
      list.unshift({ id: a.id, name: a.name, number: a.number || '', url: a.url });
      localStorage.setItem('recentAssets', JSON.stringify(list.slice(0, 5)));
    } catch (e) {}
  }
  const seen = $('#recentAsset');
  if (seen) rememberAsset({ ...seen.dataset });

  window.App = { toast, refresh, rememberAsset };

  // ── Dialog "Catat maintenance" ─────────────────────────
  const qm = $('#quickMaint'), qmBody = $('#quickMaintBody');
  let dirty = false;

  async function openQuickMaint(url) {
    if (!qm) { location.href = url; return; }
    dirty = false;
    qmBody.innerHTML = '<div class="dlg-loading" role="status"><span class="spinner"></span>Memuat form…</div>';
    if (!qm.open) qm.showModal();
    try {
      const res = await fetch(url, { headers: FETCH });
      const html = await res.text();
      if (!res.ok || res.redirected || /<html/i.test(html)) throw new Error('form');
      qmBody.innerHTML = html;
      bindQuickForm();
    } catch (e) { location.href = url; }
  }

  function bindQuickForm() {
    const form = $('form', qmBody);
    if (!form) return;
    // Fokus ke isian yang salah; kalau tipe sudah terpilih (dari jadwal) langsung ke deskripsi
    const typeChosen = form.querySelector('input[name=maintenance_type]:checked');
    (form.querySelector('[aria-invalid="true"]') || (typeChosen ? form.elements.deskripsi_pekerjaan : form.querySelector('input[name=maintenance_type]')))?.focus();
    form.addEventListener('input', () => { dirty = true; });
    form.addEventListener('click', e => {
      const b = e.target.closest('[data-fill]');
      if (!b) return;
      const ta = form.elements.deskripsi_pekerjaan;
      ta.value = b.dataset.fill;
      ta.focus();
      dirty = true;
    });
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const btn = form.querySelector('[type=submit]');
      btn.disabled = true; btn.classList.add('is-loading');
      try {
        const res = await fetch(form.action, { method: 'POST', body: new FormData(form), headers: FETCH });
        if ((res.headers.get('content-type') || '').includes('application/json')) {
          const data = await res.json();
          if (data.ok) { dirty = false; qm.close(); refresh(data.message); return; }
        } else if (res.status === 422) {
          qmBody.innerHTML = await res.text();
          bindQuickForm();
          dirty = true;
          return;
        }
        throw new Error('save');
      } catch (err) {
        toast('Gagal menyimpan. Periksa koneksi lalu coba lagi.', 'error');
        btn.disabled = false; btn.classList.remove('is-loading');
      }
    });
  }

  qm?.addEventListener('click', e => {
    if (e.target.closest('[data-dialog-close]') && (!dirty || confirm('Buang isian maintenance ini?'))) qm.close();
  });
  qm?.addEventListener('cancel', e => { if (dirty && !confirm('Buang isian maintenance ini?')) e.preventDefault(); });

  document.addEventListener('click', e => {
    const a = e.target.closest('a[data-quick-maint]');
    if (!a || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    // Dialog pemilih aset ditutup dulu; panel ringkas aset tetap terbuka di belakang
    const host = a.closest('dialog');
    if (host && !host.classList.contains('drawer')) host.close();
    openQuickMaint(a.href);
  });

  // ── Palet perintah ─────────────────────────────────────
  const pal = $('#palette');
  if (!pal) return;
  const input = $('#palInput'), list = $('#palList'), modeTag = $('#palMode');
  const cfg = JSON.parse($('#paletteData').textContent);
  const pages = [...document.querySelectorAll('.sidebar-nav a')].map(a => ({
    kind: 'page', label: (a.querySelector('.mi-text') || a).textContent.trim(), url: a.href,
    icon: a.querySelector('.mi-icon')?.className.replace('mi-icon', '').trim() || 'fa-solid fa-database',
  }));
  let items = [], active = 0, mode = null, timer, seq = 0, remote = { results: [], total: 0 };

  const norm = s => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  const match = (label, q) => !q || norm(label).includes(norm(q));

  function render() {
    const q = input.value.trim();
    items = [];
    const groups = [];
    const push = (title, arr) => { if (arr.length) { groups.push([title, arr]); items.push(...arr); } };

    if (mode === 'maint') {
      push('Pilih aset', remote.results.filter(r => r.maint_url).map(r => ({ ...r, kind: 'asset-maint' })));
    } else {
      if (!q) {
        let recent = [];
        try { recent = JSON.parse(localStorage.getItem('recentAssets') || '[]'); } catch (e) {}
        push('Terakhir dibuka', recent.map(r => ({ kind: 'asset', name: r.name, number: r.number, url: r.url, meta: '' })));
      }
      if (q && cfg.search) {
        push('Aset', remote.results.map(r => ({ ...r, kind: 'asset' })));
        if (remote.total > remote.results.length) items.push({ kind: 'all', label: `Lihat semua ${remote.total} hasil untuk “${q}”`, url: remote.all_url });
      }
      push('Aksi', cfg.actions.filter(a => match(a.label, q)).map(a => ({ ...a, kind: a.mode ? 'mode' : 'action' })));
      push('Halaman', pages.filter(p => match(p.label, q)));
    }

    if (!items.length) {
      list.innerHTML = `<li class="pal-empty" role="presentation">${mode === 'maint'
        ? (q ? 'Tidak ada aset aktif yang cocok.' : 'Ketik nama, nomor aset, atau lokasi.')
        : (q ? 'Tidak ada yang cocok. Coba kata kunci lain.' : 'Ketik untuk mencari aset atau halaman.')}</li>`;
      input.removeAttribute('aria-activedescendant');
      return;
    }
    active = Math.min(active, items.length - 1);
    let i = 0, html = '';
    const row = it => {
      const id = `pal-opt-${i}`, sel = i === active;
      const idx = i++;
      if (it.kind === 'asset' || it.kind === 'asset-maint') {
        const thumb = it.photo ? `<img src="${esc(it.photo)}" alt="">` : '<i class="fa-solid fa-box" aria-hidden="true"></i>';
        const tag = it.status ? `<span class="tag ${{ active: 'safe', maintenance: 'mandatory', writeoff: 'off' }[it.status]}"><svg class="sign" aria-hidden="true"><use href="#sign-${{ active: 'safe', maintenance: 'mandatory', writeoff: 'off' }[it.status]}"/></svg>${esc(it.status_label)}</span>` : '';
        return `<li id="${id}" role="option" aria-selected="${sel}" data-i="${idx}" class="pal-item asset"><span class="pal-thumb">${thumb}</span><span class="pal-text"><strong>${esc(it.name)}</strong><small>${esc([it.number, it.meta].filter(Boolean).join(' · '))}</small></span>${it.kind === 'asset-maint' ? '<span class="pal-hint">Catat</span>' : tag}</li>`;
      }
      const icon = it.kind === 'all' ? 'fa-solid fa-list' : (it.icon || 'fa-solid fa-arrow-right');
      return `<li id="${id}" role="option" aria-selected="${sel}" data-i="${idx}" class="pal-item"><span class="pal-icon"><i class="${esc(icon)}" aria-hidden="true"></i></span><span class="pal-text"><strong>${esc(it.label)}</strong>${it.hint ? `<small>${esc(it.hint)}</small>` : ''}</span>${it.kind === 'mode' ? '<i class="fa-solid fa-chevron-right pal-go" aria-hidden="true"></i>' : ''}</li>`;
    };
    for (const [title, arr] of groups) {
      html += `<li class="pal-group" role="presentation">${esc(title)}</li>` + arr.map(row).join('');
      if (title === 'Aset' && items.some(x => x.kind === 'all')) html += row(items.find(x => x.kind === 'all'));
    }
    list.innerHTML = html;
    input.setAttribute('aria-activedescendant', `pal-opt-${active}`);
    $(`#pal-opt-${active}`)?.scrollIntoView({ block: 'nearest' });
  }

  function search() {
    const q = input.value.trim();
    clearTimeout(timer);
    if (!cfg.search || !q) { remote = { results: [], total: 0 }; render(); return; }
    list.setAttribute('aria-busy', 'true');
    timer = setTimeout(async () => {
      const my = ++seq;
      try {
        const res = await fetch(`${cfg.search}?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        if (my !== seq || input.value.trim() !== q) return;   // sudah ada ketikan/mode yang lebih baru
        remote = data;
      } catch (e) { remote = { results: [], total: 0 }; }
      list.removeAttribute('aria-busy');
      render();
    }, 160);
    render();
  }

  function setMode(m) {
    clearTimeout(timer);
    seq++;
    list.removeAttribute('aria-busy');
    mode = m;
    modeTag.hidden = !m;
    input.placeholder = m === 'maint' ? 'Cari aset yang akan dicatat maintenance-nya…' : cfg.placeholder;
    input.value = '';
    active = 0;
    remote = { results: [], total: 0 };
    render();
    input.focus();
  }

  function choose(it, newTab) {
    if (!it) return;
    if (it.kind === 'mode') { setMode(it.mode); return; }
    if (it.kind === 'asset-maint') { pal.close(); openQuickMaint(it.maint_url); return; }
    if (it.kind === 'asset') rememberAsset({ id: it.id || it.url, name: it.name, number: it.number, url: it.url });
    if (newTab) window.open(it.url, '_blank'); else location.href = it.url;
  }

  function open(prefill = '') {
    if (pal.open) { input.select(); return; }
    setMode(null);
    input.value = prefill;
    pal.showModal();
    input.focus();
    search();
  }

  input.addEventListener('input', () => { active = 0; search(); });
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); active = (active + 1) % Math.max(items.length, 1); render(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); active = (active - 1 + items.length) % Math.max(items.length, 1); render(); }
    else if (e.key === 'Enter') { e.preventDefault(); choose(items[active], e.ctrlKey || e.metaKey); }
    else if (e.key === 'Backspace' && !input.value && mode) { e.preventDefault(); setMode(null); }
  });
  list.addEventListener('mousemove', e => {
    const li = e.target.closest('[data-i]');
    if (li && Number(li.dataset.i) !== active) { active = Number(li.dataset.i); list.querySelectorAll('[aria-selected=true]').forEach(x => x.setAttribute('aria-selected', 'false')); li.setAttribute('aria-selected', 'true'); input.setAttribute('aria-activedescendant', li.id); }
  });
  list.addEventListener('click', e => { const li = e.target.closest('[data-i]'); if (li) choose(items[Number(li.dataset.i)], e.ctrlKey || e.metaKey); });
  pal.addEventListener('click', e => { if (e.target === pal) pal.close(); });
  $('#palModeClear')?.addEventListener('click', () => setMode(null));

  // Pemicu: Ctrl/Cmd+K, "/", kolom cari di header, tombol cari di ponsel
  document.addEventListener('keydown', e => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
    if ((e.key === 'k' || e.key === 'K') && (e.ctrlKey || e.metaKey)) { e.preventDefault(); open(); }
    else if (e.key === '/' && !typing && !document.querySelector('dialog[open]')) { e.preventDefault(); open(); }
  });
  document.querySelectorAll('[data-palette-open]').forEach(el => {
    el.addEventListener('click', e => { e.preventDefault(); open(); });
  });
  const gs = $('#globalSearch');
  gs?.addEventListener('focus', () => { const v = gs.value; gs.blur(); open(v); });
})();
