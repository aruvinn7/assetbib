// Daftar aset: filter/urut/halaman tanpa muat ulang + panel ringkas per aset.
// Tanpa JS semua tautan tetap berfungsi sebagai navigasi biasa.
(() => {
  const results = () => document.getElementById('assetResults');
  const searchForm = document.getElementById('searchForm');
  const searchInput = document.getElementById('searchInput');
  const drawer = document.getElementById('qvDrawer');
  const drawerBody = document.getElementById('qvBody');
  let ctrl = null;

  // ── Muat hasil untuk URL baru, ganti hanya bagian yang berubah ──
  async function load(url, { push = true } = {}) {
    ctrl?.abort();
    ctrl = new AbortController();
    const box = results();
    box?.setAttribute('aria-busy', 'true');
    searchForm.classList.add('is-busy');
    try {
      const res = await fetch(url, { signal: ctrl.signal });
      if (!res.ok || res.redirected) throw new Error('nav');
      const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
      for (const id of ['assetResults', 'assetFilters', 'assetCount']) {
        const fresh = doc.getElementById(id);
        if (fresh) document.getElementById(id)?.replaceWith(fresh);
      }
      // Sinkronkan field tersembunyi pencarian dengan filter yang berlaku
      const freshForm = doc.getElementById('searchForm');
      if (freshForm) {
        searchForm.querySelectorAll('input[type=hidden]').forEach(i => i.remove());
        freshForm.querySelectorAll('input[type=hidden]').forEach(i => searchInput.before(i));
      }
      if (push) history.pushState({ assets: true }, '', url);
      document.title = doc.title;
    } catch (e) {
      if (e.name !== 'AbortError') location.href = url;
    } finally {
      results()?.removeAttribute('aria-busy');
      searchForm.classList.remove('is-busy');
    }
  }

  const sameList = a => a && a.origin === location.origin && a.pathname === location.pathname;

  document.addEventListener('click', e => {
    if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest('#assetFilters a, #assetResults .sort-link, #assetResults .pagination-wrap a, #assetResults .scope-note a, #assetResults .empty a');
    if (a && sameList(a)) {
      e.preventDefault();
      load(a.href);
      if (a.closest('.pagination-wrap')) document.querySelector('.toolbar')?.scrollIntoView({ block: 'start' });
    }
  });

  let t;
  searchInput?.addEventListener('input', () => {
    clearTimeout(t);
    t = setTimeout(() => {
      const params = new URLSearchParams(new FormData(searchForm));
      if (!searchInput.value.trim()) params.delete('q');
      params.delete('page');
      load(`${location.pathname}?${params}`, { push: false });
      history.replaceState({ assets: true }, '', `${location.pathname}?${params}`);
    }, 300);
  });
  searchForm?.addEventListener('submit', e => { e.preventDefault(); clearTimeout(t); searchInput.dispatchEvent(new Event('input')); });

  window.addEventListener('popstate', () => load(location.href, { push: false }));

  // ── Panel ringkas ──
  let current = null;
  const rows = () => [...document.querySelectorAll('#assetResults tr[data-qv]')];

  async function openQuick(row) {
    if (!row || !drawer) return;
    rows().forEach(r => r.classList.toggle('is-current', r === row));
    current = row;
    if (!drawer.open) {
      drawerBody.innerHTML = '<div class="dlg-loading" role="status"><span class="spinner"></span>Memuat ringkasan…</div>';
      drawer.showModal();
    } else {
      drawerBody.setAttribute('aria-busy', 'true');
    }
    try {
      const res = await fetch(row.dataset.qv);
      if (!res.ok || res.redirected) throw new Error('qv');
      drawerBody.innerHTML = await res.text();
      const list = rows(), i = list.indexOf(row);
      const pos = drawerBody.querySelector('[data-qv-pos]');
      if (pos) pos.textContent = `${i + 1} dari ${list.length}`;
      drawerBody.querySelector('[data-qv-step="-1"]').disabled = i <= 0;
      drawerBody.querySelector('[data-qv-step="1"]').disabled = i >= list.length - 1;
      try { window.App?.rememberAsset({ id: row.dataset.qv.match(/\d+/)[0], name: drawerBody.querySelector('#qvTitle')?.textContent.trim(), url: drawerBody.querySelector('.qv-foot .btn')?.getAttribute('href') }); } catch (e) {}
    } catch (e) {
      drawerBody.innerHTML = '<div class="dlg-loading" role="alert">Ringkasan gagal dimuat. <a href="' + row.querySelector('.row-link').href + '">Buka detail aset</a></div>';
    } finally {
      drawerBody.removeAttribute('aria-busy');
    }
  }

  function step(dir) {
    const list = rows(), i = list.indexOf(current);
    const next = list[i + dir];
    if (next) { openQuick(next); next.scrollIntoView({ block: 'nearest' }); }
  }

  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-qv-open]');
    const row = e.target.closest('#assetResults tr[data-qv]');
    if (btn) { e.preventDefault(); openQuick(btn.closest('tr')); return; }
    // Klik di area kosong baris (bukan tautan/tombol) membuka panel ringkas
    if (row && !e.target.closest('a, button, input, label') && !window.getSelection().toString()) openQuick(row);
  });

  drawer?.addEventListener('click', e => {
    if (e.target === drawer || e.target.closest('[data-dialog-close]')) drawer.close();
    const s = e.target.closest('[data-qv-step]');
    if (s && !s.disabled) step(Number(s.dataset.qvStep));
  });
  drawer?.addEventListener('keydown', e => {
    if (e.target.closest('input, textarea, select')) return;
    if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); step(1); }
    if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); step(-1); }
  });
  drawer?.addEventListener('close', () => {
    rows().forEach(r => r.classList.remove('is-current'));
    current?.querySelector('[data-qv-open]')?.focus({ preventScroll: true });
  });

  // Setelah maintenance dicatat dari panel, isi panel ikut diperbarui
  document.addEventListener('app:refreshed', () => {
    if (!drawer?.open || !current) return;
    const same = rows().find(r => r.dataset.qv === current.dataset.qv);
    if (same) openQuick(same);
  });
})();
