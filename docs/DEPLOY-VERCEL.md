# Deploy AssetMS ke Vercel + Neon (gratis)

Susunan gratis yang dipakai:

| Bagian | Layanan | Paket |
|---|---|---|
| Aplikasi Django | Vercel | Hobby (gratis, **hanya untuk pemakaian non-komersial**) |
| Database **dan** file unggahan (foto aset, foto kerusakan, scan & foto BA) | Neon PostgreSQL | Free: 1 GB per proyek, 100 CU-jam/bulan |

Neon tidak pernah menghentikan proyek karena lama tidak dipakai. Setelah 5 menit tanpa akses,
komputasinya "tidur", lalu bangun sendiri pada akses berikutnya. Akses pertama sesudahnya sedikit lebih lambat.

File unggahan disimpan di tabel `BerkasMedia` pada database yang sama, lalu disajikan lewat `/berkas/<nama>`.
Alamat ini **hanya bisa dibuka setelah login**. Di laptop tanpa env, file tetap disimpan di folder `media/`.
Perkiraan kapasitas: 1 GB cukup untuk data (±30–50 MB) ditambah sekitar 1.000–2.000 foto, karena foto
diperkecil di browser sebelum diunggah. Kalau suatu saat butuh lebih, set `MEDIA_BUCKET` dkk.
(lihat [`.env.example`](../.env.example)) supaya foto pindah ke bucket S3-compatible.

---

## 1. Buat database di Neon

1. Daftar di **neon.com** (bisa login dengan GitHub).
2. Buat project:
   - Name: `assetms`
   - Region: **AWS Asia Pacific (Singapore)**
   - Postgres: versi terbaru
3. Di Dashboard, klik **Connect** dan salin dua URL:
   - **Connection pooling ON**: host-nya mengandung `-pooler`. URL ini dipakai Vercel.
   - **Connection pooling OFF**: koneksi langsung. URL ini dipakai sekali dari laptop di langkah 2.

## 2. Pindahkan data & foto dari laptop

Buka **PowerShell baru** di folder proyek, lalu jalankan:

```powershell
pip install -r requirements.txt
$env:DATABASE_URL = "<URL Neon TANPA pooler>"
python manage.py pindah_ke_server --data --media
```

Hasil yang benar diakhiri dengan:

```
Selesai: 41286 baris disalin ke server.
Mengunggah media/ ke DatabaseStorage…
Selesai: 6 file diunggah, 0 sudah ada.
```

Cara kerja perintah ini:

- `--data` menjalankan `migrate`, lalu menyalin semua data dari `db.sqlite3` dalam **satu transaksi**.
  Kalau gagal di tengah, server tidak berubah. Tanggal asli ikut tersalin, dan penomoran id berlanjut.
  Sesi login dan log admin tidak ikut disalin.
- Perintah menolak jalan kalau server sudah berisi aset/user. Untuk mengganti seluruh data server
  dengan data laptop, tambahkan `--timpa`. Foto yang sudah ada di server tidak ikut terhapus.
- `--media` mengunggah isi folder `media/` ke database. File yang sudah ada dilewati, jadi aman diulang.
- Akun dan password login sama dengan di laptop.

Buat kunci rahasia Django, lalu simpan hasilnya untuk langkah 3:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

**Tutup jendela PowerShell ini setelah selesai.** Kalau Anda menjalankan `runserver` di jendela yang sama,
aplikasi lokal akan menulis ke database Neon, bukan ke `db.sqlite3`.

## 3. Deploy di Vercel

1. Daftar di **vercel.com** dengan **Continue with GitHub**, lalu pilih plan **Hobby**.
2. Klik **Add New → Project** dan **Import** repo `aruvinn7/assetbib`.
   Biarkan *Framework Preset* dan *Root Directory* apa adanya. Vercel mengenali Django dari `manage.py`.
3. Buka **Environment Variables** dan isi **dua** nilai berikut:

   | Key | Value |
   |---|---|
   | `DJANGO_SECRET_KEY` | hasil perintah `secrets` di langkah 2 |
   | `DATABASE_URL` | URL Neon **dengan** `-pooler` |

4. Klik **Deploy** dan tunggu ±1–3 menit. Buka `https://<nama-proyek>.vercel.app`, lalu login.

Yang sudah otomatis di Vercel:
- `DEBUG` mati dan domain `*.vercel.app` diizinkan.
- Cookie hanya dikirim lewat HTTPS.
- Sesi login berakhir setelah 60 menit tidak aktif dan saat browser ditutup. Untuk mengubah batasnya,
  tambahkan env `SESSION_IDLE_MENIT` (mis. `30` atau `120`), lalu Redeploy.
- Foto disimpan di database, dan fungsi berjalan di region Singapura (`vercel.json`).

Kalau env wajib belum diisi, build berhenti dengan pesan yang menyebut env mana yang kurang.

## 4. Cek setelah deploy

- Login sebagai GA, Finance, dan Karyawan. Buka Ringkasan, Daftar aset, dan detail aset yang berfoto.
- Unggah satu foto dari ponsel di halaman detail aset, lalu hapus lagi.
- Kalau ada error, buka Vercel → proyek → **Logs**. Error Django tampil di sana.

Masalah yang sering terjadi:
- `Set env DJANGO_SECRET_KEY ...` atau `Set env DATABASE_URL ...`: env belum diisi. Lengkapi, lalu buka **Deployments** dan klik **Redeploy**.
- `password authentication failed`: URL Neon salah salin. Salin ulang dari tombol **Connect**.
- Halaman pertama lambat sesudah lama tidak dipakai: Neon sedang bangun dari tidur (normal, kurang dari 1 detik).

## Domain sendiri (opsional)

Tambahkan domain di Vercel → *Domains*. Lalu set env
`DJANGO_ALLOWED_HOSTS=aset.contoh.co.id` dan `DJANGO_CSRF_TRUSTED_ORIGINS=https://aset.contoh.co.id`.

## Setelah tayang

- Setiap `git push` ke `master`, Vercel otomatis deploy ulang.
- Kalau ada migration baru, jalankan dari laptop **sebelum** atau bersamaan dengan push:
  ```powershell
  $env:DATABASE_URL = "<URL Neon TANPA pooler>"
  python manage.py migrate
  ```

## Batasan

- **Ukuran unggahan maks. 4,5 MB per permintaan** (batas Vercel). Foto JPEG/PNG/WebP di atas 1,5 MB
  otomatis diperkecil di browser (sisi terpanjang 2048 px) sebelum dikirim (`static/js/app.js`).
  Scan berita acara berformat **PDF tidak diperkecil**, jadi harus di bawah 4,5 MB.
- **Kuota Neon Free:** penyimpanan 1 GB per proyek. Kalau penuh, simpan dan ubah data akan gagal sampai
  ada ruang kosong. Pantau di Neon → *Monitoring*.
- `bangun_ulang_data` menghapus data operasional. Jangan jalankan dengan `DATABASE_URL` server kecuali memang perlu.
- Ikon Font Awesome adalah *subset*. Kalau menambah ikon baru, jalankan `python scripts/build_icons.py` sebelum commit.

## Sebelum dibuka untuk umum (keamanan)

Sudah ditangani: `SECRET_KEY`, `DEBUG`, dan `ALLOWED_HOSTS` dibaca dari env; cookie HTTPS; file unggahan hanya bisa dibuka setelah login.
Masih terbuka:
- Beberapa aksi yang mengubah data masih menerima GET (hapus master data, `maintenance_generate_schedule`).
- Tipe file unggahan belum diperiksa di server.
- Django 5.1 sudah EOL dan Pillow 10.0.1 sudah usang: naikkan ke Django 5.2 LTS dan Pillow terbaru.
