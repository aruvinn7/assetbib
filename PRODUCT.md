# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Aplikasi internal PT Borneo Indobara, dipakai di dua situasi: di kantor (desktop) dan di lapangan (site, mess, area operasional) lewat ponsel.

- **GA (General Affairs)** — pengguna utama. Mendaftarkan dan mengubah aset, mencatat maintenance, menyerahkan aset ke karyawan (assignment), mencatat perpindahan (movement), menangani laporan kerusakan, mengimpor data dari Excel, dan mengelola master data. Bekerja di kantor maupun langsung di lapangan.
- **Finance** — mengisi dan memantau data finansial aset (nilai awal, nilai residual, umur ekonomis) serta melihat nilai buku.
- **Karyawan (role User)** — melihat aset yang sedang mereka pegang dan melaporkan kerusakan, umumnya dari ponsel di lapangan.

## Product Purpose

Satu sumber kebenaran untuk aset fisik perusahaan: apa asetnya, di mana, siapa yang memegang, bagaimana kondisinya, dan berapa nilainya.

Ukuran sukses, sesuai prioritas yang dikonfirmasi:

1. **Data aset akurat** — lokasi, pemegang, dan status setiap aset selalu benar dan historinya bisa dilacak.
2. **Maintenance tepat waktu** — jadwal preventive tidak terlewat, dan kerusakan yang dilaporkan cepat ditangani sampai selesai.
3. **Keputusan writeoff** — status kesehatan aset (rasio biaya perbaikan terhadap nilai awal, frekuensi kerusakan) membantu memutuskan kapan aset perlu ditinjau untuk diganti atau di-writeoff.

Nilai finansial (depresiasi, nilai buku) tersedia untuk Finance, tetapi bukan ukuran sukses utama.

## Positioning

Menggantikan pencatatan terpisah (spreadsheet, catatan manual) dengan satu alur yang tersambung: laporan kerusakan dari karyawan otomatis mengubah status aset dan membuat jadwal corrective; maintenance yang selesai otomatis menjadwalkan maintenance berikutnya; dan biaya perbaikan yang terkumpul langsung tercermin di status kesehatan aset.

## Operating Context

- Status proyek: **proyek magang/skripsi** yang dibangun dan diuji di PT Borneo Indobara. Saat ini dijalankan sebagai server dev Django dan diakses lewat tunnel ngrok.
- Lokasi dan aset tersusun dalam hierarki site → area → ruang (contoh: "Angsana > Mess > Mess 1"). Site yang ada antara lain Angsana, Kusan, dan Bunati.
- Data awal sering datang dari file Excel, diimpor lewat template yang disediakan aplikasi (aset dan finansial).
- Alur kerja inti:
  - Aset didaftarkan → jadwal maintenance awal dibuat dari interval kategori (atau interval custom per aset).
  - Karyawan melaporkan kerusakan → aset berstatus maintenance → GA mengakui, lalu menyelesaikan atau menolak laporan.
  - GA menyerahkan aset ke karyawan; perpindahan lokasi dan pemegang dicatat sebagai histori movement.

## Capabilities and Constraints

- **Stack:** Django 5.1, SQLite, template server-side, JavaScript vanilla. Chart memakai Chart.js; Bootstrap 5 hanya dimuat di halaman laporan kerusakan.
- **Bahasa:** antarmuka berbahasa Indonesia. Sebagian istilah teknis tetap dalam bahasa Inggris: Active, Maintenance, Write Off, preventive, corrective, inspection, writeoff.
- **Role** ditentukan dari profile Employee: GAProfile, FinanceProfile, UserProfile.
- **Status aset:** active, maintenance, writeoff.
- **Status kesehatan:** normal, warning, waspada, critical, no_data. Ambang persen biaya perbaikan bertingkat menurut nilai awal aset: < 2 jt, < 10 jt, < 50 jt, dan di atasnya.
- **Status laporan kerusakan:** pending, acknowledged, in_progress, resolved, rejected. Severity: low, medium, high, critical.
- **Kebutuhan perangkat:** harus bisa dipakai di desktop maupun ponsel, karena GA dan karyawan bekerja di lapangan.
- **Belum diputuskan:** target deployment produksi, dan apakah aplikasi akan dipakai resmi setelah masa magang/skripsi.

## Brand Commitments

- Nama perusahaan **PT Borneo Indobara** tampil di aplikasi.
- Nama aplikasi yang dipakai di UI saat ini adalah **"AssetMS" / "Asset Management System"**.
- Belum ada logo resmi maupun pedoman brand yang dikonfirmasi.

## Evidence on Hand

- **Data uji:** database pengembangan berisi sekitar 300 aset, 27 lokasi, sekitar 1.100 catatan maintenance, dan beberapa histori movement. Ini data uji, bukan klaim penggunaan.
- **Foto aset nyata:** `media/asset_photos/`.
- **Template import Excel:** dibuat otomatis oleh aplikasi (`/asset/import/template/...`); versi statis ada di `static/templates/`.
- **Belum ada** testimoni, metrik dampak, atau hasil evaluasi pengguna. Jangan dikarang.

## Product Principles

1. **Kebenaran data di atas segalanya.** Setiap perubahan lokasi, pemegang, atau status harus tercatat dan bisa ditelusuri, bukan menimpa data diam-diam.
2. **Alur yang menyambung sendiri.** Satu aksi pengguna (lapor rusak, catat maintenance) harus menggerakkan status dan jadwal terkait secara otomatis, sehingga tidak ada pekerjaan ganda.
3. **Yang mendesak harus terlihat duluan.** Aset critical, jadwal overdue, dan laporan kerusakan aktif harus muncul lebih dulu dibanding data rutin.
4. **Lapangan sama pentingnya dengan kantor.** Tugas inti (melapor kerusakan, memeriksa aset, mencatat movement) harus nyaman dilakukan dari ponsel.
5. **Bantu keputusan, jangan memutuskan.** Status kesehatan dan rekomendasi writeoff adalah sinyal untuk ditinjau manusia, bukan keputusan otomatis.
