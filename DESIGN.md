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

## Halaman berkelas lama

Halaman yang belum dibangun ulang (form aset/maintenance/finansial, master data, BOM, import, movement, serah terima, laporan kerusakan, dashboard karyawan) masih memakai kosakata kelas lama di `static/css/pages/*.css`. `static/css/legacy.css` dimuat **setelah** CSS halaman dan menyelaraskan tampilannya (tombol, kartu, field, tabel, badge, tombol Bootstrap) ke sistem ini tanpa mengubah tata letak. Link CSS halaman harus berada di `{% block extra_head %}` agar urutan ini benar. Halaman yang sudah dibangun ulang mengosongkan `{% block legacy_css %}`.

## Do's and Don'ts

- **Do** memakai `_status.html` untuk setiap status; rambu dan teks selalu tampil bersama.
- **Do** memakai `brand-500` untuk aksi utama dan menu aktif.
- **Do** mengubah baris tabel menjadi kartu bertumpuk di layar sempit dari markup yang sama.
- **Don't** memakai warna status untuk dekorasi atau sebagai warna merek.
- **Don't** memakai `warning-fill` sebagai warna teks; teks waspada memakai `warning-ink`.
- **Don't** menghapus akses logout dari header atau sidebar.
