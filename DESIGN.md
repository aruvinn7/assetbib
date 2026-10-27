---
name: AssetMS
description: Sistem manajemen aset PT Borneo Indobara. Tata letak dan komponen mengacu TailAdmin (references/), arti warna status mengikuti kode keselamatan K3.
colors:
  brand-50: "#ECF3FF"
  brand-200: "#C2D6FF"
  brand-500: "#465FFF"
  brand-600: "#3641F5"
  safe: "#039855"
  safe-tint: "#ECFDF3"
  safe-ink: "#027A48"
  mandatory: "#0086C9"
  mandatory-tint: "#F0F9FF"
  mandatory-ink: "#026AA2"
  warning-fill: "#F79009"
  warning-tint: "#FFFAEB"
  warning-ink: "#B54708"
  danger: "#D92D20"
  danger-tint: "#FEF3F2"
  danger-ink: "#B42318"
  off: "#667085"
  off-tint: "#F2F4F7"
  safe-line: "#A6F4C5"
  mandatory-line: "#B9E6FE"
  warning-line: "#FEDF89"
  danger-line: "#FECDCA"
  danger-line-strong: "#FDA29B"
  ink: "#101828"
  ink-2: "#344054"
  ink-3: "#667085"
  ink-4: "#98A2B3"
  ground: "#F9FAFB"
  panel: "#FFFFFF"
  rule: "#E4E7EC"
  rule-soft: "#F2F4F7"
  field: "#D0D5DD"
typography:
  display:
    fontFamily: "Outfit, Segoe UI, system-ui, sans-serif"
    fontSize: "30px"
    fontWeight: 700
    lineHeight: "38px"
  headline:
    fontFamily: "Outfit, Segoe UI, system-ui, sans-serif"
    fontSize: "24px"
    fontWeight: 600
    lineHeight: "32px"
  title:
    fontFamily: "Outfit, Segoe UI, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 600
  body:
    fontFamily: "Outfit, Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "'tnum'"
  label:
    fontFamily: "Outfit, Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 500
rounded:
  control: "8px"
  card: "16px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  2xl: "24px"
  gutter: "clamp(16px, 2.4vw, 24px)"
components:
  button-primary:
    backgroundColor: "{colors.brand-500}"
    textColor: "{colors.panel}"
    rounded: "{rounded.control}"
    height: "42px"
  button-primary-hover:
    backgroundColor: "{colors.brand-600}"
  button-outline:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.control}"
    height: "42px"
  panel:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.card}"
    padding: "20px"
  tag:
    rounded: "{rounded.pill}"
    typography: "{typography.label}"
  input:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.control}"
    height: "44px"
  sidebar:
    backgroundColor: "{colors.panel}"
    width: "272px"
  header:
    backgroundColor: "{colors.panel}"
    height: "68px"
---

# Design System: AssetMS

## Overview

Tata letak, bentuk, dan komponen mengacu **TailAdmin** (salinan di `references/tailadmin-free-tailwind-dashboard-template-main`): sidebar putih di kiri, header putih bergaris tipis, kartu putih membulat 16px di atas latar abu sangat terang, font Outfit, warna interaksi biru-ungu `brand-500`. Tujuannya ramah pengguna: lega, lembut, mudah dipindai.

Yang dipertahankan dari sistem lama adalah **arti warna status (kode K3/ISO 7010)**: setiap warna status punya tepat satu arti, dan piktogram rambu SVG ikut membawa arti itu sehingga status tetap terbaca tanpa warna.

- Hijau (safe) = aktif / normal / selesai
- Biru (mandatory) = sedang diproses / maintenance / segera
- Oranye (warning) = waspada / terlambat
- Merah (danger) = critical / kerusakan / hapus
- Abu (off) = write off / dilewati / netral
- Kotak merah berisi dokumen bertanda seru (`#sign-report`) = laporan kerusakan baru dari karyawan. Masih merah (bahaya), tapi bentuknya beda dari lingkaran bergaris (`#sign-danger`, aset critical) supaya dua hal ini tidak tertukar.

Istilah status aset selalu **Aktif / Maintenance / Write off** (lewat `components/_lifecycle.html`), tanpa singkatan atau sinonim ("Dalam perbaikan", "Maint.", "W/O"). "Sedang diperbaiki" hanya dipakai untuk status *laporan* kerusakan. Umur ditulis lengkap ("5 tahun 4 bulan") lewat filter `umur` / `umur_singkat` di `templatetags/waktu.py`.

Warna merek `brand-500` hanya untuk interaksi (tombol utama, menu aktif, tautan, fokus, grafik non-status). Jangan dipakai sebagai arti status.

## Layout

- **Kerangka:** sidebar 272px (ringkas 84px di desktop, diingat di localStorage; berubah jadi laci di bawah 1024px) + area konten dengan header sticky 68px.
- **Header:** tombol menu, pencarian aset global (tekan `/`), lonceng notifikasi, dan menu akun berisi tombol **Keluar**. Keluar juga ada di kaki sidebar, jadi selalu bisa dijangkau di semua ukuran layar.
- **Halaman:** lebar maksimum 1536px, gutter 16–24px. Kepala halaman berisi judul 24px/600 + sub-baris abu di kiri, aksi di kanan.
- Grid dua kolom dashboard/detail runtuh ke satu kolom lebih awal (1360–1440px) karena lebar sidebar.

## Components

- **Panel/kartu:** putih, tepi 1px `rule`, sudut 16px, tanpa bayangan. Kepala panel 16px 20px dengan garis `rule-soft`.
- **Panel hazard:** garis atas oranye 4px (inset) untuk panel yang memuat item terlambat/critical.
- **Kartu metrik (papan status):** ikon rambu di kotak tint 48px sudut 12px, label abu, angka 30px/700. Kartu adalah tautan ke daftar terfilter.
- **Tombol:** primary `brand-500` solid; outline putih bertepi `field`; danger putih bertepi merah muda, terisi merah saat hover. Sudut 8px, tinggi 42px (sm 34px).
- **Tag status:** pill, 12px/500, piktogram rambu 14px + teks, di atas tint warna statusnya. Selalu lewat partial `components/_status.html`.
- **Chip filter:** pill putih bertepi `rule`; aktif `brand-50` dengan teks `brand-600`; hitungan dalam pill abu kecil.
- **Tabel:** kepala 12px/500 abu tanpa latar, baris dipisah `rule-soft`, nama aset 500 tinta. Daftar aset menampilkan thumbnail foto utama 44px.
- **Input:** tinggi 44px, sudut 8px, tepi `field`, fokus tepi `brand-300` + cincin `rgba(70,95,255,.12)` 4px.
- **Dialog:** sudut 16px, bayangan xl, backdrop gelap 50% blur ringan.
- **Foto aset:** di detail aset, foto utama tampil besar di kartu kiri atas (klik untuk perbesar), thumbnail foto lain di bawahnya, galeri lengkap dengan aksi "Jadikan utama/Hapus" di kolom utama.

## Interaksi

Logika ada di `static/js/app.js` (global) dan `static/js/asset_list.js`. Semua tautan tetap berfungsi tanpa JS.

- **Palet perintah:** dibuka dengan `Ctrl+K`, `/`, mengklik kolom cari di header, atau tombol kaca pembesar di ponsel. Isinya: aset dari `/asset/cari/` (beberapa kata digabung dengan DAN), aksi sesuai peran, halaman dari menu sidebar, dan aset yang terakhir dibuka (disimpan di localStorage). Aksi "Catat maintenance" mengubah palet ke mode pilih aset.
- **Dialog catat maintenance:** setiap tautan `data-quick-maint` membuka `maintenance/_quick_form.html` di `dialog.sheet` (lembar bawah di ponsel). Dialog meminta konfirmasi sebelum menutup isian yang sudah diubah. Setelah berhasil, muncul toast dan bagian halaman bertanda `[data-refresh][id]` dimuat ulang; halaman tanpa bagian itu dimuat ulang penuh.
- **Daftar aset:** chip filter, kepala kolom (bisa diurutkan, `aria-sort`), paginasi, dan pencarian mengganti hasil tanpa muat ulang, dengan URL ikut diperbarui. Klik baris atau tombol "Ringkas" membuka `dialog.drawer` (laci kanan, lembar bawah di ponsel). Di laci, ↑/↓ berpindah aset.
- **Toast:** pojok kanan bawah. Sukses hilang setelah 5,5 detik, error setelah 9 detik.
- **Error validasi:** view memanggil `flash_form_errors()`; tiap error field ditandai `extra_tags="field:<name>|<label>"` dan `base.html` menempelkannya tepat di bawah isian ber-`name` sama (`aria-invalid`, fokus ke isian pertama, satu ringkasan di atas). Error tanpa isian di halaman tetap tampil sebagai flash. Error hilang begitu isiannya diubah.
- **Aksi permanen** (hapus aset, lewati jadwal, terima/selesaikan/tolak laporan) hanya menerima POST. Hapus aset tidak ada di baris daftar; tempatnya panel "Hapus permanen" di halaman edit, terpisah dari tombol Simpan.
- **Write off:** status tidak diubah dari form edit. GA memakai tombol "Write off" di detail aset (`asset/write_off.html`): alasan, tanggal, keterangan, konfirmasi, dan nilai buku pada tanggal itu dihitung langsung. `write_off_asset()` menyimpan `AssetWriteOff` (snapshot nilai), menghentikan jadwal, mengembalikan pemegang, menutup laporan aktif, dan mencatat movement. Nilai buku berhenti menyusut di tanggal write off. Pembatalan lewat banner di detail aset (`cancel_write_off()`).
- **Kesehatan aset:** rasio = biaya perbaikan (corrective) ÷ nilai perolehan. Preventive & inspeksi tidak dihitung, supaya aset yang rajin dirawat tidak terlihat makin "sakit".
- **Gerak:** satu ease-out eksponensial (`--ease-out`) untuk dialog, laci, palet, dan toast. Dimatikan oleh `prefers-reduced-motion`.

## Form & data master

Form tambah/edit aset, finansial, BOM, dan master data memakai komponen bersama di `base.css`:

- **`.form-layout`** membagi halaman jadi kolom form dan kartu samping 320px. Varian `.single` hanya berisi satu kolom form, lebar 760px.
- **`.fsec`** adalah bagian form, berisi kepala (judul + penjelasan singkat) dan **`.fgrid`** dua kolom (`.span-all` untuk isian lebar penuh).
- **`.form-bar`** adalah bilah aksi yang menempel di bawah layar: petunjuk di kiri, Batal + Simpan di kanan, Hapus (bila ada) paling kiri.
- **`.kv`** adalah pasangan label–nilai di kartu samping, dan **`.side-links`** adalah tautan terkait.
- **`.note`** (`.warn`, `.danger`) adalah catatan di dalam form. **`.check-row`** dipakai untuk kotak centang berlabel panjang.
- Isian yang sama dipakai bersama lewat partial: `asset/_form_fields.html`, `financial/_form.html`, `components/_form.html`, `master/_category_form.html`, `master/_location_form.html`.
- Master data punya tab bersama (`components/_master_tabs.html`). Daftar kecil disaring di sisi klien (`static/js/master.js`), dengan panel "tambah cepat" di samping.
- Semua penghapusan dilakukan lewat POST + konfirmasi yang menyebut dampaknya (jumlah aset, sub-lokasi, atau kategori yang ikut terdampak).

**Berita acara** (`views/berita_acara.py`, menu *Berita acara*):

- Setiap temuan kerusakan otomatis masuk **draf BA Kerusakan** per departemen pemilik aset: laporan kerusakan baru, inspeksi/preventive berhasil *Minor/Major issue*, atau ditambah GA. Laporan yang ditolak dikeluarkan dari draf.
- GA meninjau draf (keterangan + rekomendasi *Diperbaiki/Dihapuskan*) → **Terbitkan** (nomor `NNN/BIB/-HRGA/BA/<bulan romawi>/<tahun>`, urut per tahun, lembar pengesahan disalin saat terbit) → cetak & tanda tangan basah → **Tandai ditandatangani** (scan opsional) → **Proses penghapusan** untuk aset berekomendasi dihapus.
- Setiap write off (dari BA maupun dari detail aset) menerbitkan **BA Penghapusan** yang merujuk BA Kerusakan bila ada.
- Dokumen memakai `static/css/ba_dokumen.css` (Arial, mengikuti format resmi BA HRGA, bukan font aplikasi) dan `berita_acara/_dokumen.html` untuk pratinjau maupun cetak/PDF.

**Pelacakan:** halaman lokasi, pemegang aset, dan riwayat perpindahan berada di satu menu, dengan tab bersama (`components/_tracking_tabs.html`).

## Pola bersama halaman

Semua halaman memakai sistem yang sama; `legacy.css` dan kelas lama (`pg-*`, `db-card`, `mv-*` lama, eyebrow) sudah dihapus. CSS khusus halaman ada di `static/css/pages/*.css` dan dimuat lewat `{% block extra_head %}`.

- **`.table.stack`**: tabel yang di bawah 760px berubah jadi kartu bertumpuk. `td.lead` = judul kartu, `td.act` = aksi, `td[data-label]` = pasangan label–nilai. Dipakai Pelacakan, import, dan aset per lokasi.
- **`.pick-list`** (`.cols` untuk grid): daftar ringkas berisi satu aksi per baris (aset tanpa pemegang).
- **`data-filter-for="<id select>"`** pada `<input>` di atas `<select>` panjang: menyaring opsi sambil mengetik; bila tinggal satu, opsi itu langsung terpilih (`static/js/app.js`).
- **`data-palette-open="maint"`**: membuka palet langsung di mode pilih aset untuk dicatat maintenance-nya (menggantikan dialog daftar semua aset).
- **Riwayat perpindahan** memakai `<details>` per aset (bisa dipakai keyboard tanpa JS); satu `<template>` form dipasang di slot aset/baris yang dibuka. Setelah simpan, halaman kembali ke saringan yang sama dengan aset itu terbuka (`#mv-<id>`).
- **Aset per lokasi**: pohon lokasi dirender server dalam keadaan terbuka sampai lokasi terpilih.
- **Form maintenance**: isian dialog cepat dan halaman penuh sama-sama dari `maintenance/_fields.html`. Interval khusus (buat/ubah) memakai satu template `maintenance/config_form.html`.
- **Import Excel**: satu template `asset/import.html` untuk aset & finansial (isi kolom, langkah, dan aturan dari `IMPORT_PAGES` di `views/import_views.py`), hasilnya di `asset/import_result.html`.
- **Teks meta 13px** (sub-baris tabel, catatan, hint) adalah ukuran tetap di antara label 12px dan body 14px.
- Angka tidak dianimasikan (tanpa count-up); nilainya langsung benar saat halaman tampil.

## Do's and Don'ts

- **Do** memakai `_status.html` untuk setiap status; rambu dan teks selalu tampil bersama.
- **Do** memakai `brand-500` untuk aksi utama dan menu aktif.
- **Do** mengubah baris tabel menjadi kartu bertumpuk di layar sempit dari markup yang sama.
- **Don't** memakai warna status untuk dekorasi atau sebagai warna merek.
- **Don't** memakai `warning-fill` sebagai warna teks; teks waspada memakai `warning-ink`.
- **Don't** menghapus akses logout dari header atau sidebar.
