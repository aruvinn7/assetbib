// Pemilih aset lewat pencarian (lapor kerusakan, lapor pindah).
// <input data-asset-search="URL" aria-controls="idWadahHasil" data-empty="teks bila kosong">
// Hasil dirender sebagai radio name="asset_id". Respons JSON: {results: [{id, name, number, category,
// location, blocked, tag, ...data}]}; kunci di `data` ikut jadi data-* di radio (mis. data-dept).
(() => {
  const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  document.querySelectorAll('[data-asset-search]').forEach(search => {
    const results = document.getElementById(search.getAttribute('aria-controls'));
    const box = search.closest('.rp-search');
    let timer, seq = 0;

    search.addEventListener('keydown', e => { if (e.key === 'Enter') e.preventDefault(); });
    search.addEventListener('input', () => {
      clearTimeout(timer);
      const q = search.value.trim();
      if (q.length < 2) { results.innerHTML = ''; box?.classList.remove('is-busy'); return; }
      timer = setTimeout(async () => {
        const my = ++seq;
        box?.classList.add('is-busy');
        try {
          const res = await fetch(`${search.dataset.assetSearch}?q=${encodeURIComponent(q)}`, { headers: { 'X-Requested-With': 'fetch' } });
          const found = (await res.json()).results;
          if (my !== seq) return;
          // Pilihan yang sedang dicentang dari hasil sebelumnya tetap dipertahankan
          const keep = results.querySelector('input:checked')?.closest('.ap-opt');
          results.innerHTML = found.length ? '' : `<p class="ap-empty">Tidak ada aset yang cocok dengan “${esc(q)}”. ${esc(search.dataset.empty || '')}</p>`;
          if (keep) results.append(keep);
          found.forEach(a => {
            if (keep && keep.querySelector('input').value === String(a.id)) return;
            const meta = [a.number, a.category, a.location].filter(Boolean).map(esc).join(' · ');
            const tag = a.blocked ? `<em class="ap-block">${esc(a.blocked)}</em>` : (a.tag ? `<em class="ap-mine">${esc(a.tag)}</em>` : '');
            const data = Object.entries(a.data || {}).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('');
            results.insertAdjacentHTML('beforeend',
              `<label class="ap-opt${a.blocked ? ' is-blocked' : ''}"><input type="radio" name="asset_id" value="${a.id}"${data}${a.blocked ? ' disabled' : ''}>` +
              `<span><strong>${esc(a.name)}</strong><small>${meta}</small>${tag}</span></label>`);
          });
        } catch (err) {
          if (my === seq) results.innerHTML = '<p class="ap-empty">Pencarian gagal. Periksa koneksi lalu coba lagi.</p>';
        } finally {
          if (my === seq) box?.classList.remove('is-busy');
        }
      }, 250);
    });
  });
})();
