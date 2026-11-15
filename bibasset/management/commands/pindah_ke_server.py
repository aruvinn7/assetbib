"""Pindahkan isi aplikasi dari laptop ke server: data SQLite ke PostgreSQL, folder media/ ke bucket.

Jalankan di laptop dengan env server terpasang (lihat docs/DEPLOY-VERCEL.md):
    python manage.py pindah_ke_server --data     # salin semua data ke DATABASE_URL
    python manage.py pindah_ke_server --media    # unggah foto & scan ke MEDIA_BUCKET

Data:  dibaca dari db.sqlite3 (atau --sqlite PATH), ditulis ke database `default` (PostgreSQL dari
       DATABASE_URL) dalam SATU transaksi: gagal di tengah → database tujuan tidak berubah.
       Server belum boleh berisi aset/user (pakai --timpa untuk mengganti seluruhnya dengan data laptop).
       Sesi login, log admin, dan izin bawaan Django tidak disalin (dibuat ulang oleh migrate).
Media: setiap file di MEDIA_ROOT diunggah dengan nama yang sama ke penyimpanan server (database
       PostgreSQL bila DATABASE_URL diset, atau bucket bila MEDIA_BUCKET diset); yang sudah ada dilewati.
"""
from contextlib import contextmanager
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connections, transaction

SOURCE = 'sqlite_lokal'
# Dibuat ulang oleh `migrate` atau tidak perlu dipindah. Isi file (BerkasMedia) diurus --media,
# jadi --data/--timpa tidak pernah menghapus foto yang sudah ada di server.
SKIP = {'contenttypes.contenttype', 'auth.permission', 'admin.logentry', 'sessions.session',
        'bibasset.berkasmedia'}


@contextmanager
def keep_timestamps(models):
    """Matikan auto_now/auto_now_add sementara: tanggal asli ikut tersalin, bukan diganti waktu sekarang."""
    saved = []
    for m in models:
        for f in m._meta.concrete_fields:
            if getattr(f, 'auto_now', False) or getattr(f, 'auto_now_add', False):
                saved.append((f, f.auto_now, f.auto_now_add))
                f.auto_now = f.auto_now_add = False
    try:
        yield
    finally:
        for f, now, now_add in saved:
            f.auto_now, f.auto_now_add = now, now_add


class Command(BaseCommand):
    help = 'Salin data SQLite lokal ke PostgreSQL (--data) dan folder media/ ke server (--media).'

    def add_arguments(self, parser):
        parser.add_argument('--data', action='store_true', help='salin data ke DATABASE_URL')
        parser.add_argument('--media', action='store_true', help='unggah folder media/ ke MEDIA_BUCKET')
        parser.add_argument('--sqlite', default=str(settings.BASE_DIR / 'db.sqlite3'), help='file SQLite sumber')
        parser.add_argument('--timpa', action='store_true', help='kosongkan data di tujuan sebelum menyalin')

    def handle(self, *args, **opts):
        if not (opts['data'] or opts['media']):
            raise CommandError('Pilih --data, --media, atau keduanya.')
        if opts['data']:
            self.copy_data(Path(opts['sqlite']), opts['timpa'])
        if opts['media']:
            self.upload_media()

    # ── Data ────────────────────────────────────────────────────────────
    def models(self):
        """Semua tabel yang disalin, termasuk tabel penghubung many-to-many."""
        out = []
        for m in apps.get_models(include_auto_created=True):
            if m._meta.label_lower in SKIP or m._meta.proxy or not m._meta.managed:
                continue
            out.append(m)
        return out

    def copy_data(self, sqlite_path, wipe):
        target = connections['default']
        if target.vendor != 'postgresql':
            raise CommandError('Database tujuan bukan PostgreSQL. Set env DATABASE_URL ke database server dulu.')
        if not sqlite_path.is_file():
            raise CommandError(f'File SQLite tidak ditemukan: {sqlite_path}')

        # Daftarkan SQLite sebagai koneksi kedua, khusus untuk perintah ini
        cfg = {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(sqlite_path)}
        connections.settings[SOURCE] = connections.configure_settings(
            {'default': settings.DATABASES['default'], SOURCE: cfg})[SOURCE]

        self.stdout.write('Menyiapkan tabel di server (migrate)…')
        call_command('migrate', interactive=False, verbosity=0)

        models = self.models()
        # Tabel penghubung yang menunjuk ke Permission memakai id yang berbeda di server → harus kosong
        for m in models:
            if m._meta.auto_created and any(f.related_model and f.related_model._meta.label_lower == 'auth.permission'
                                            for f in m._meta.fields):
                if m.objects.using(SOURCE).exists():
                    raise CommandError(f'{m._meta.db_table} berisi data; izin per user tidak didukung perintah ini.')

        # Migrasi sendiri mengisi beberapa tabel (mis. penanda tangan BA bawaan) → itu boleh ditimpa.
        # Yang dijaga: aset & akun user, tanda server sudah dipakai sungguhan.
        used = [label for label in ('bibasset.Aset', 'auth.User')
                if apps.get_model(label).objects.using('default').exists()]
        if used and not wipe:
            raise CommandError('Database server sudah berisi data (' + ', '.join(used) +
                               '). Pakai --timpa bila memang ingin mengganti seluruhnya dengan data laptop.')

        total = 0
        # Constraint FK di PostgreSQL buatan Django bersifat DEFERRED: urutan tabel bebas,
        # semuanya dicek saat commit di akhir transaksi.
        with keep_timestamps(models), transaction.atomic(using='default'):
            with target.cursor() as c:
                tables = ', '.join(target.ops.quote_name(m._meta.db_table) for m in models)
                c.execute(f'TRUNCATE {tables} RESTART IDENTITY CASCADE')
            for m in models:
                rows = list(m.objects.using(SOURCE).all().order_by('pk'))
                for i in range(0, len(rows), 1000):
                    m.objects.using('default').bulk_create(rows[i:i + 1000])
                total += len(rows)
                if rows:
                    self.stdout.write(f'  {m._meta.label:40} {len(rows):>6}')
            # Lanjutkan penomoran id dari nilai terbesar, supaya data baru tidak bentrok
            with target.cursor() as c:
                for sql in target.ops.sequence_reset_sql(no_style(), models):
                    c.execute(sql)
        self.stdout.write(self.style.SUCCESS(f'Selesai: {total} baris disalin ke server.'))

    # ── Media ───────────────────────────────────────────────────────────
    def upload_media(self):
        backend = settings.STORAGES['default']['BACKEND']
        if backend.endswith('FileSystemStorage'):
            raise CommandError('Penyimpanan tujuan masih folder media/ lokal. Set env DATABASE_URL '
                               '(simpan di database server) atau MEDIA_BUCKET dulu.')
        self.stdout.write(f'Mengunggah media/ ke {backend.rsplit(".", 1)[-1]}…')
        if backend.endswith('DatabaseStorage'):
            call_command('migrate', interactive=False, verbosity=0)  # tabel BerkasMedia harus ada
        root = Path(settings.MEDIA_ROOT)
        files = [p for p in root.rglob('*') if p.is_file()]
        sent = skipped = 0
        for p in files:
            name = p.relative_to(root).as_posix()
            if default_storage.exists(name):
                skipped += 1
                continue
            with p.open('rb') as fh:
                saved = default_storage.save(name, File(fh, name=p.name))
            if saved != name:
                raise CommandError(f'Bucket menyimpan {name} sebagai {saved}; periksa izin bucket.')
            sent += 1
            self.stdout.write(f'  + {name}')
        self.stdout.write(self.style.SUCCESS(f'Selesai: {sent} file diunggah, {skipped} sudah ada.'))
