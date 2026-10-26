---
version: 1
slug: "bibasset-templates-base-html"
primary_target: "bibasset/templates/base.html"
related_targets: ["bibasset/templates/dashboard.html","bibasset/templates/asset/list.html","bibasset/templates/asset/detail.html","bibasset/templates/maintenance/list.html"]
---

# Surface brief: AssetMS app shell + halaman inti

## Scope
Mode: Operate. Fondasi visual seluruh aplikasi (base.html, base.css: token, navbar, tombol, tabel, tag status, form, flash) ditambah bangun ulang penuh empat halaman inti: Dashboard (dashboard.html), Daftar Aset (asset/list.html), Detail Aset (asset/detail.html), Maintenance (maintenance/list.html). Halaman lain mewarisi token baru tanpa dibangun ulang.

## Audience & task
Staf GA di kantor (desktop, ruangan terang) memantau aset, menindaklanjuti jadwal terlambat dan aset critical, lalu mencatat maintenance. Finance membaca nilai aset. Data harus tetap padat.

## Constraints
- Serius dan profesional ("jangan terlalu gaya"); ini bahan skripsi.
- Tanpa emoji sebagai ikon.
- Fungsi, data, dan URL tetap; hanya tampilan yang diganti.

## Direction contract
THESIS: Status aset dibaca seperti rambu keselamatan di site: setiap warna punya tepat satu arti (hijau aman, biru wajib/dalam proses, kuning waspada, merah bahaya), dan bentuk rambunya ikut membawa arti itu. Menolak dashboard kartu-pastel dengan ikon + angka besar + label kecil.
OWN-WORLD: Latar abu sangat terang #F4F4F1, panel putih bertepi hitam 2px, bar atas hitam #111. Archivo dengan sumbu lebar (sempit untuk label dan angka, normal untuk teks), angka tabular. Warna ISO 7010: hijau #1E7B3C, biru #005BAC, kuning #F2C200 (isi saja, teks kuning tidak pernah), merah #C8102E. Pita kuning-hitam hanya di header panel bahaya. Sudut 2px, tanpa bayangan dekoratif.
STORY: GA membuka aplikasi dan dalam satu pandangan tahu berapa aset dalam bahaya, apa yang terlambat, dan di mana; lalu langsung membuka aset itu dan mencatat tindakan.
FIRST VIEWPORT: Bar atas hitam dengan navigasi. Judul halaman dan aksi utama (tombol hitam solid) di kanan. Baris papan status bergaya rambu (piktogram berbentuk ISO di blok warna, angka besar sempit, label), lalu panel "Antrean kerja" berpita kuning-hitam berisi tabel jadwal terlambat dan laporan kerusakan aktif, di samping ringkasan lokasi.
FORM: Rambu K3/HSE site tambang (kode warna dan bentuk ISO 7010), kandidat #1 di daftar grounded (IMPECCABLE'S PICK, dipilih pengguna). seed:808c9616680e770b
SIGNATURE: Ikon status adalah bentuk rambu ISO (persegi = aman, lingkaran penuh = wajib/proses, segitiga = waspada, lingkaran bergaris = bahaya/terlarang), dipakai identik di tag, tabel, dan papan, sehingga status terbaca dari bentuk tanpa warna.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
