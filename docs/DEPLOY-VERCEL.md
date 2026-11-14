# Deploy AssetMS ke Vercel

Vercel menjalankan Django sebagai *serverless function*. Akibatnya, file di server tidak permanen:
`db.sqlite3` dan folder `media/` **tidak bisa** dipakai di sana. Di server, aplikasi memakai:

| Kebutuhan | Di laptop (tanpa env) | Di Vercel |
|---|---|---|
| Database | `db.sqlite3` | PostgreSQL (`DATABASE_URL`) |
| Foto aset, foto kerusakan, scan & foto BA | folder `media/` | bucket S3-compatible privat (`MEDIA_BUCKET` dkk.) |
| CSS/JS/font | runserver | CDN Vercel (`collectstatic` dijalankan Vercel otomatis saat build) |

Semua pengaturan dibaca dari *environment variable* (daftar lengkap: [`.env.example`](../.env.example)).
Tanpa env apa pun, aplikasi di laptop tetap jalan seperti biasa.

Rekomendasi paling sederhana: **Supabase**. Satu akun sudah menyediakan PostgreSQL dan Storage (S3-compatible).
Pilih region **Southeast Asia (Singapore)** supaya dekat dengan fungsi Vercel, yang di [`vercel.json`](../vercel.json)
juga sudah diset ke Singapura (`sin1`). Alternatif lain: Neon (Postgres) + Cloudflare R2 (foto).

---

## 1. Siapkan Supabase

1. Buat proyek di supabase.com dengan region **Southeast Asia (Singapore)**. Simpan password database-nya.
2. **Database → Connect**, salin dua URL:
   - **Transaction pooler** (port `6543`) → dipakai Vercel sebagai `DATABASE_URL`.
   - **Session pooler** (port `5432`) → dipakai sekali dari laptop untuk memindahkan data (langkah 4).
3. **Storage → New bucket**: nama `assetms-media`, **Public bucket: OFF** (privat).
4. **Storage → Settings → S3 Connection**: aktifkan, lalu catat *Endpoint* dan *Region*. Buat **access key** dan
   catat *Access key ID* serta *Secret access key*.

## 2. Buat proyek di Vercel

1. Push kode ke GitHub (`origin` repo ini), lalu di vercel.com pilih **Add New → Project** dan impor repo ini.
   Vercel mengenali Django dari `manage.py` dan `requirements.txt`. Python 3.12 dipilih dari `.python-version`.
2. Sebelum klik **Deploy**, buka **Environment Variables** dan isi:

   | Nama | Nilai |
   |---|---|
   | `DJANGO_SECRET_KEY` | hasil `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
   | `DATABASE_URL` | URL *Transaction pooler* dari langkah 1.2 |
   | `MEDIA_BUCKET` | `assetms-media` |
   | `MEDIA_ENDPOINT_URL` | Endpoint S3 Supabase, mis. `https://xxxx.supabase.co/storage/v1/s3` |
   | `MEDIA_REGION` | Region S3 Supabase, mis. `ap-southeast-1` |
   | `MEDIA_ADDRESSING_STYLE` | `path` |
   | `MEDIA_ACCESS_KEY_ID` / `MEDIA_SECRET_ACCESS_KEY` | access key dari langkah 1.4 |

   `DEBUG` otomatis mati di Vercel, dan domain `*.vercel.app` otomatis diizinkan. Kalau env wajib belum diisi,
   build berhenti dengan pesan yang menyebut env mana yang kurang.
3. Klik **Deploy**. Database masih kosong pada tahap ini; datanya diisi di langkah berikut.

## 3. (Opsional) Domain sendiri

Tambahkan domain di Vercel → *Domains*, lalu set env:
`DJANGO_ALLOWED_HOSTS=aset.contoh.co.id` dan `DJANGO_CSRF_TRUSTED_ORIGINS=https://aset.contoh.co.id`.

## 4. Pindahkan data & foto dari laptop

Jalankan sekali dari folder proyek di laptop (PowerShell). Perintah ini membaca `db.sqlite3` dan folder `media/`,
lalu menulis ke Postgres dan bucket server. Database lokal tidak diubah.

```powershell
pip install -r requirements.txt
$env:DATABASE_URL = "<URL Session pooler, port 5432>"
$env:MEDIA_BUCKET = "assetms-media"
$env:MEDIA_ENDPOINT_URL = "https://xxxx.supabase.co/storage/v1/s3"
$env:MEDIA_REGION = "ap-southeast-1"
$env:MEDIA_ADDRESSING_STYLE = "path"
$env:MEDIA_ACCESS_KEY_ID = "<access key id>"
$env:MEDIA_SECRET_ACCESS_KEY = "<secret access key>"
python manage.py pindah_ke_server --data --media
```

- `--data` membuat tabel di server (`migrate`), lalu menyalin semua data dalam satu transaksi. Kalau gagal di tengah,
  server tidak berubah. Tanggal asli (dibuat/diperbarui) ikut tersalin apa adanya, dan penomoran id berlanjut.
- Perintah menolak jalan kalau server sudah berisi aset/user. Untuk mengganti seluruh isi server dengan data laptop,
  tambahkan `--timpa`.
- `--media` mengunggah isi `media/`. File yang sudah ada di bucket dilewati, jadi aman diulang.
- Pakai **akun yang sama** untuk login. Password tetap sama karena tabel user ikut disalin.

Tutup jendela PowerShell setelah selesai supaya env server tidak terbawa ke `runserver` lokal.

## 5. Cek setelah deploy

- Login sebagai GA, Finance, dan Karyawan. Buka Ringkasan, Daftar aset, dan detail aset yang berfoto.
- Unggah satu foto dari ponsel di halaman detail aset, lalu hapus lagi.
- Vercel → *Logs*: error Django tampil di sini (logging ke console sudah aktif).

## Perubahan skema berikutnya

Setiap ada migration baru, jalankan dari laptop dengan URL *Session pooler*:

```powershell
$env:DATABASE_URL = "<URL Session pooler>"
python manage.py migrate
```

Jalankan migrate **sebelum** atau **bersamaan** dengan deploy kode yang membutuhkannya.

## Batasan yang perlu diketahui

- **Ukuran unggahan maks. 4,5 MB per permintaan** (batas Vercel). Foto JPEG/PNG/WebP di atas 1,5 MB otomatis
  diperkecil di browser (sisi terpanjang 2048 px) sebelum dikirim (`static/js/app.js`). Scan berita acara
  berformat **PDF tidak diperkecil**, jadi harus < 4,5 MB.
- **Foto bersifat privat.** Tautan foto ditandatangani dan berlaku 1 jam, sehingga tautan yang disalin keluar akan kedaluwarsa.
- **Data simulasi** (`bangun_ulang_data`) bisa dijalankan ke server dengan `DATABASE_URL` yang sama,
  tetapi itu menghapus data operasional. Jalankan hanya kalau memang perlu.
- Ikon Font Awesome adalah *subset*. Kalau menambah ikon baru, jalankan `python scripts/build_icons.py` sebelum commit.

## Sebelum dibuka untuk umum (keamanan)

Pengaturan dasar sudah dari env (`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, cookie HTTPS, foto privat). Masih terbuka:

- Beberapa aksi berubah data menerima GET (hapus master data, `maintenance_generate_schedule`).
- Upload belum memeriksa tipe file di server.
- Django 5.1 sudah EOL dan Pillow 10.0.1 sudah usang: naikkan ke Django 5.2 LTS dan Pillow terbaru.
