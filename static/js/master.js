// Master data: saring baris tabel/pohon di sisi klien (data master kecil, cukup tanpa server).
document.querySelectorAll('[data-filter]').forEach(input => {
  const count = input.closest('.panel')?.querySelector('[data-filter-count]');
  const run = () => {
    const q = input.value.trim().toLowerCase();
    const rows = [...document.querySelectorAll(input.dataset.filter)];
    let shown = 0;
    rows.forEach(r => {
      const hit = !q || (r.dataset.name || r.textContent.toLowerCase()).includes(q);
      r.hidden = !hit;
      if (hit) shown++;
    });
    if (count) count.textContent = q ? `${shown} dari ${rows.length} cocok` : '';
  };
  input.addEventListener('input', run);
});
