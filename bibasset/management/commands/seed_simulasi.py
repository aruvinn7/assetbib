"""
python manage.py seed_simulasi [--dry-run]

Menambah data simulasi kondisi lapangan di atas data dummy yang sudah ada:

- Karyawan baru (akun login role User, plus satu teknisi GA) di departemen
  pemilik aset dan penghuni mess dari departemen lapangan.
- Serah terima aset ke karyawan (laptop, PC kantor site, kursi kerja,
  perabot kamar mess) lengkap dengan rotasi: resign, roster, pengganti.
- Histori movement yang cocok dengan setiap serah terima/pengembalian, plus
  riwayat relokasi aset umum (TV, proyektor, printer, dll.).
- Laporan kerusakan dari pemegang aset pada masanya: sebagian besar selesai
  (tercatat sebagai maintenance corrective + biayanya), beberapa ditolak,
  dan beberapa masih aktif hari ini.

Aturan yang dijaga:
- Master data yang sudah ada tidak diubah; karyawan hanya DITAMBAH.
- Lokasi aset saat ini tidak berubah (relokasi disisipkan sebelum posisi terakhir).
- Aset 1.1.2.15 dan 1.3.2 (berfoto asli) tidak disentuh.
- Biaya perbaikan dibatasi supaya status kesehatan tidak melonjak tak wajar.
- Acak dengan seed tetap; perintah menolak jalan dua kali.

Semua akun simulasi memakai password SIM_PASSWORD di bawah (khusus data uji).
"""
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Sum

from bibasset.models import (
    Asset, AssetAssignment, AssetMovement, DamageReport, Department, Employee,
    GAProfile, Location, MaintenanceRecord, MaintenanceSchedule, UserProfile,
    get_repair_threshold,
)

SIM_PASSWORD = 'Simulasi#2026'
MARKER_USERNAME = 'fajar.nugroho'
PROTECTED_ASSETS = {'1.1.2.15', '1.3.2'}
TZ = ZoneInfo('Asia/Jakarta')
TODAY = date.today()


def at(d, hour=9, minute=0):
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TZ)


def d(s):
    return date.fromisoformat(s)


# ── Karyawan baru ────────────────────────────────────────────────────────────
# key, nama, jabatan, departemen, role, resign (tanggal akun dinonaktifkan)
EMPLOYEES = [
    ('fajar',   'Fajar Nugroho',        'Teknisi GA',                    'HRGA', 'ga', None),
    ('yusuf',   'Yusuf Maulana',        'Admin HRGA Site Kusan',         'HRGA', 'user', None),
    ('rina',    'Rina Marlina',         'Admin HRGA Site Bunati',        'HRGA', 'user', None),
    ('nur',     'Nur Aisyah',           'Staff Accounting',              'Finance', 'user', None),
    ('bayu',    'Bayu Setiawan',        'Staff Treasury',                'Finance', 'user', None),
    ('agus',    'Agus Salim',           'IT Support',                    'IT', 'user', '2025-01-31'),
    ('dimas',   'Dimas Prasetyo',       'Network Engineer',              'IT', 'user', None),
    ('rahmat',  'Rahmat Hidayat',       'Data Analyst',                  'Digital & Technology Solutions Division', 'user', None),
    ('putri',   'Putri Ananda',         'Software Engineer',             'Digital & Technology Solutions Division', 'user', None),
    ('hendraw', 'Hendra Wijaya',        'Contract Analyst',              'Business Control & Contract Management', 'user', None),
    ('maya',    'Maya Sari',            'Business Controller',           'Business Control & Contract Management', 'user', None),
    ('arif',    'Arif Rahman',          'Mine Planner',                  'Strategic Long Term Planning & Optimization', 'user', '2025-08-15'),
    ('lukman',  'Lukman Hakim',         'Mine Planner',                  'Strategic Long Term Planning & Optimization', 'user', None),
    ('wahyu',   'Wahyu Kurniawan',      'Short Term Planner',            'Short Term Planning', 'user', None),
    ('fauzi',   'Ahmad Fauzi',          'Surveyor',                      'Survey, Geology & Exploration', 'user', None),
    ('gusti',   'Gusti Rizal',          'Geologist',                     'Survey, Geology & Exploration', 'user', '2024-09-30'),
    ('ilham',   'Muhammad Ilham',       'Junior Geologist',              'Survey, Geology & Exploration', 'user', None),
    ('sri',     'Sri Wahyuni',          'HSE Training Officer',          'HSE System, Training & Compliance', 'user', None),
    ('taufik',  'Taufik Hidayat',       'Environmental Officer',         'Environment & Reclamation', 'user', None),
    ('nadia',   'Nadia Paramita',       'CSR Officer',                   'CSR & Empowerment', 'user', None),
    ('reza',    'Reza Firmansyah',      'Continuous Improvement Specialist', 'Continuous Improvement', 'user', None),
    ('andika',  'Andika Saputra',       'Coal Chain Planner',            'Coal Chain Coordinator', 'user', None),
    ('ramadhan', 'Ilham Ramadhan',      'Admin Logistik',                'Coal Chain Coordinator', 'user', None),
    ('bambang', 'Bambang Sutrisno',     'Site Manager',                  'Project', 'user', None),
    # Penghuni mess (departemen lapangan)
    ('rudi',    'Rudi Hartono',         'Operator Hauling',              'Coal Rom & Hauling Dept', 'user', None),
    ('joko',    'Joko Susilo',          'Foreman Hauling',               'Coal Rom & Hauling Dept', 'user', None),
    ('dedi',    'Dedi Kurniawan',       'Operator Hauling',              'Coal Rom & Hauling Dept', 'user', None),
    ('eko',     'Eko Prasetyo',         'Mekanik Road Maintenance',      'Road Maintenance', 'user', None),
    ('fikri',   'Fikri Ramadhan',       'Supervisor Barging',            'Port Coal Barging & Shipment', 'user', None),
    ('hasan',   'Hasan Basri',          'Civil Engineer',                'Infrastructure Project & Maintenance', 'user', None),
    ('samsul',  'Samsul Arifin',        'HSE Officer',                   'HSE Operation', 'user', None),
    ('aditya',  'Aditya Pratama',       'Safety Officer',                'HSE Operation', 'user', None),
    ('yoga',    'Yoga Pratama',         'Staff HSE Operation',           'HSE Operation', 'user', None),
]

# ── Serah terima per aset (berurutan) ───────────────────────────────────────
# (pemegang, mulai, selesai, alasan selesai, catatan serah terima)
# Pemegang bisa key karyawan baru atau nama karyawan lama.
NEW_HIRE = 'Fasilitas kerja karyawan baru'
ASSIGNMENTS = {
    'IT-PC-002':  [('yusuf', '2024-08-05', None, None, 'PC admin kantor site Kusan')],
    'IT-PC-003':  [('rina', '2023-05-02', None, None, 'PC admin kantor site Bunati')],
    'IT-PC-004':  [('ramadhan', '2025-06-28', None, None, 'PC admin logistik')],
    'IT-LP-004':  [('nur', '2024-12-02', None, None, 'Laptop eks. Radja, sudah diinstal ulang IT')],
    'FRN-KK-003': [('nur', '2024-12-02', None, None, NEW_HIRE)],
    'FRN-KK-004': [('Radja', '2024-07-22', None, None, 'Penggantian kursi lama')],
    'FRN-KK-005': [('bayu', '2024-07-22', None, None, NEW_HIRE)],
    'IT-LP-006':  [('dimas', '2025-02-17', None, None, NEW_HIRE)],
    'FRN-KK-008': [('agus', '2023-06-12', '2025-01-31', 'resign', NEW_HIRE),
                   ('dimas', '2025-02-17', None, None, 'Kursi eks. Agus Salim')],
    'FRN-KK-007': [('Rizky Pratama', '2023-06-12', None, None, 'Kursi kerja tim IT')],
    'IT-LP-007':  [('rahmat', '2025-06-30', None, None, NEW_HIRE)],
    'IT-LP-008':  [('putri', '2025-06-30', None, None, NEW_HIRE)],
    'FRN-KK-009': [('rahmat', '2025-05-26', None, None, NEW_HIRE)],
    'FRN-KK-010': [('putri', '2025-05-26', None, None, NEW_HIRE)],
    'IT-LP-011':  [('hendraw', '2024-10-03', None, None, NEW_HIRE)],
    'IT-LP-012':  [('maya', '2024-10-03', None, None, NEW_HIRE)],
    'FRN-KK-011': [('maya', '2024-09-02', None, None, NEW_HIRE)],
    'IT-LP-013':  [('arif', '2024-07-25', '2025-08-15', 'resign', NEW_HIRE),
                   ('lukman', '2025-08-25', None, None, 'Laptop eks. Arif Rahman, data sudah dibackup')],
    'FRN-KK-012': [('arif', '2024-08-26', '2025-08-15', 'resign', NEW_HIRE),
                   ('lukman', '2025-08-25', None, None, 'Kursi eks. Arif Rahman')],
    'IT-LP-014':  [('wahyu', '2025-05-12', None, None, NEW_HIRE)],
    'IT-LP-015':  [('fauzi', '2023-06-20', None, None, 'Laptop lapangan tim survey')],
    'IT-LP-016':  [('gusti', '2023-06-20', '2024-09-30', 'resign', 'Laptop lapangan tim geologi'),
                   ('ilham', '2024-10-14', None, None, 'Laptop eks. Gusti Rizal')],
    'IT-LP-017':  [('sri', '2024-08-22', None, None, NEW_HIRE)],
    'IT-LP-018':  [('taufik', '2025-03-17', None, None, NEW_HIRE)],
    'IT-LP-019':  [('nadia', '2024-11-18', None, None, NEW_HIRE)],
    'IT-LP-020':  [('reza', '2026-04-15', None, None, NEW_HIRE)],
    'IT-LP-022':  [('andika', '2026-10-05', None, None, NEW_HIRE)],
    'IT-LP-001':  [('bambang', '2026-05-11', None, None, 'Laptop pimpinan site')],
    'FRN-KK-006': [('bambang', '2025-03-24', None, None, 'Kursi ruang BOD untuk Site Manager')],
    # Kamar Mess 1: roster Dedi → Rudi
    'FRN-TT-001': [('dedi', '2023-06-05', '2025-03-31', 'rotasi roster', 'Fasilitas kamar mess'),
                   ('rudi', '2025-04-07', None, None, 'Fasilitas kamar mess')],
    'FRN-LP-001': [('dedi', '2023-06-05', '2025-03-31', 'rotasi roster', 'Fasilitas kamar mess'),
                   ('rudi', '2025-04-07', None, None, 'Fasilitas kamar mess')],
    'FRN-KS-001': [('dedi', '2024-06-12', '2025-03-31', 'rotasi roster', 'Kasur baru kamar mess'),
                   ('rudi', '2025-04-07', None, None, 'Fasilitas kamar mess')],
    # Kamar Mess 2
    'FRN-TT-002': [('joko', '2023-06-05', None, None, 'Fasilitas kamar mess')],
    'FRN-LP-002': [('joko', '2023-06-05', None, None, 'Fasilitas kamar mess')],
    'FRN-KS-002': [('joko', '2024-06-12', None, None, 'Kasur baru kamar mess')],
    # Kasur mess lama
    'BIB-HRGA-2502': [('eko', '2022-06-20', None, None, 'Fasilitas kamar mess')],
    'BIB-HRGA-2503': [('fikri', '2023-01-10', None, None, 'Fasilitas kamar mess')],
    'BIB-HRGA-2504': [('hasan', '2024-02-12', None, None, 'Fasilitas kamar mess')],
    'BIB-HRGA-2505': [('samsul', '2022-06-20', '2024-01-31', 'pindah ke kamar dinas', 'Fasilitas kamar mess'),
                      ('aditya', '2024-02-05', None, None, 'Fasilitas kamar mess')],
}
END_NOTES = {
    'resign':                'Karyawan resign, aset dikembalikan ke GA',
    'rotasi roster':         'Rotasi roster ke site lain, fasilitas kamar dikembalikan',
    'pindah ke kamar dinas': 'Pindah ke kamar dinas, kasur dikembalikan',
}

# ── Template kerusakan per kategori ─────────────────────────────────────────
# (judul, deskripsi, severity, tindakan, biaya, hasil, pesan GA, bisa ditolak?)
T = {
    'Laptop': [
        ('Laptop sering mati sendiri', 'Laptop mati mendadak saat dipakai, terutama kalau buka banyak aplikasi. Bagian bawah terasa sangat panas.', 'medium',
         'Bersihkan kipas & heatsink, ganti thermal paste', 250_000, 'ok', 'Kipas sudah dibersihkan, suhu normal lagi. Hindari pakai di atas kasur/bantal.', False),
        ('Baterai laptop cepat habis', 'Baterai penuh hanya tahan sekitar 30 menit, kadang langsung mati saat charger dicabut.', 'low',
         'Ganti baterai original', 950_000, 'replaced_component', 'Baterai sudah diganti yang baru, garansi 6 bulan dari vendor.', False),
        ('Beberapa tombol keyboard tidak berfungsi', 'Tombol E, R, dan spasi harus ditekan keras baru keluar huruf. Tidak ada riwayat tumpahan air.', 'medium',
         'Ganti unit keyboard', 650_000, 'replaced_component', 'Keyboard sudah diganti unit baru.', False),
        ('Layar laptop berkedip dan muncul garis', 'Layar berkedip saat engsel digerakkan, kadang muncul garis hijau vertikal.', 'high',
         'Ganti kabel fleksibel LCD', 450_000, 'replaced_component', 'Kabel fleksibel LCD diganti, layar sudah normal.', False),
        ('Charger laptop panas dan berbau hangus', 'Adaptor charger sangat panas dan tercium bau hangus, sempat ada percikan di colokan.', 'critical',
         'Ganti adaptor charger original, cek stop kontak meja', 550_000, 'replaced_component', 'Charger diganti original. Stop kontak meja juga sudah dicek, aman. Jangan pakai charger non-original.', False),
        ('Laptop tidak bisa terhubung ke WiFi', 'Laptop tidak bisa connect ke WiFi kantor sejak pagi, HP bisa connect normal.', 'low',
         'Reset network adapter & update driver', 0, 'ok', 'Driver WiFi sudah diperbarui, koneksi normal.', True),
        ('Engsel layar patah sebelah', 'Engsel kiri patah, layar tidak bisa berdiri tegak dan bodi bawah mulai retak.', 'medium',
         'Ganti engsel kiri-kanan dan casing bawah', 400_000, 'replaced_component', 'Engsel & casing sudah diganti. Buka-tutup layar dari tengah ya.', False),
        ('Laptop sangat lambat dan sering hang', 'Booting lebih dari 5 menit, aplikasi Excel dan email sering not responding.', 'low',
         'Instal ulang OS dan upgrade SSD', 700_000, 'replaced_component', 'SSD sudah di-upgrade dan OS diinstal ulang. Data sudah dipindahkan.', True),
    ],
    'Desktop / PC': [
        ('PC tidak mau menyala', 'Tombol power ditekan tidak ada respons sama sekali, lampu indikator mati.', 'high',
         'Ganti power supply', 450_000, 'replaced_component', 'Power supply rusak dan sudah diganti.', False),
        ('PC sering restart sendiri', 'PC restart sendiri 3-4 kali sehari, kadang muncul layar biru.', 'medium',
         'Bersihkan debu, ganti RAM yang bermasalah', 350_000, 'replaced_component', 'Satu keping RAM rusak sudah diganti.', False),
        ('Mouse dan keyboard PC tidak terdeteksi', 'Mouse dan keyboard USB tidak terbaca, sudah coba colok ke port lain.', 'low',
         'Ganti set keyboard-mouse', 150_000, 'replaced_component', 'Keyboard & mouse diganti set baru.', True),
    ],
    'Kursi Kerja': [
        ('Hidrolik kursi turun sendiri', 'Kursi turun pelan-pelan sampai paling bawah setiap kali diduduki.', 'low',
         'Ganti tabung hidrolik', 275_000, 'replaced_component', 'Tabung hidrolik sudah diganti.', False),
        ('Roda kursi patah', 'Satu roda patah, kursi miring dan susah digeser.', 'low',
         'Ganti 5 roda kursi', 150_000, 'replaced_component', 'Roda sudah diganti satu set.', False),
        ('Sandaran kursi goyang', 'Sandaran goyang ke kiri-kanan, ada bunyi saat bersandar.', 'low',
         'Kencangkan dan ganti baut mekanisme sandaran', 0, 'ok', 'Baut mekanisme sudah dikencangkan dan diganti.', False),
        ('Busa dudukan kursi sobek', 'Cover dudukan sobek dan busa mulai keluar.', 'low',
         'Ganti cover dan busa dudukan', 350_000, 'replaced_component', 'Cover & busa dudukan sudah diganti.', False),
    ],
    'Kasur': [
        ('Per kasur menonjol', 'Ada per yang menonjol di bagian tengah kasur, sakit saat dipakai tidur.', 'medium',
         'Perbaikan per dan lapisan busa oleh vendor', 400_000, 'ok', 'Kasur sudah diperbaiki vendor, per sudah diratakan.', False),
        ('Kasur lembap dan berjamur', 'Bagian bawah kasur lembap dan muncul bercak jamur, kamar jadi bau apek.', 'low',
         'Jemur, semprot anti jamur, pasang alas kasur', 0, 'ok', 'Kasur sudah dijemur & disemprot anti jamur. Sudah dipasang alas.', False),
    ],
    'Lemari Pakaian': [
        ('Pintu lemari lepas dari engsel', 'Pintu kanan lemari lepas, engsel atas patah.', 'low',
         'Ganti engsel pintu', 60_000, 'replaced_component', 'Engsel sudah diganti.', False),
        ('Kunci lemari rusak', 'Kunci lemari macet, tidak bisa dibuka maupun dikunci.', 'low',
         'Ganti silinder kunci', 85_000, 'replaced_component', 'Kunci sudah diganti, kunci cadangan disimpan GA.', False),
    ],
    'Tempat Tidur': [
        ('Rangka tempat tidur berderit dan goyang', 'Rangka besi berderit keras dan goyang saat dipakai.', 'low',
         'Las ulang sambungan rangka', 150_000, 'ok', 'Sambungan rangka sudah dilas ulang.', False),
        ('Papan alas tempat tidur patah', 'Dua bilah papan alas patah, kasur melesak ke bawah.', 'medium',
         'Ganti papan alas', 250_000, 'replaced_component', 'Papan alas sudah diganti.', False),
    ],
}
# Aset umum di kantor site, dilaporkan admin HRGA site
T_COMMON = {
    'AC': [('AC tidak dingin', 'AC menyala tapi hanya keluar angin, ruangan tetap panas.', 'medium',
            'Cuci AC dan tambah freon', 350_000, 'ok', 'AC sudah dicuci dan freon ditambah.', False),
           ('AC bocor, air menetes', 'Air menetes dari unit indoor ke meja di bawahnya.', 'low',
            'Bersihkan saluran pembuangan', 150_000, 'ok', 'Saluran pembuangan tersumbat, sudah dibersihkan.', False)],
    'Printer': [('Printer sering paper jam', 'Kertas sering tersangkut di tengah, harus ditarik manual.', 'low',
                 'Bersihkan roller penarik kertas', 0, 'ok', 'Roller sudah dibersihkan.', False),
                ('Hasil cetak bergaris', 'Ada garis hitam memanjang di setiap hasil cetak.', 'low',
                 'Ganti drum unit', 450_000, 'replaced_component', 'Drum unit sudah diganti.', False)],
    'Dispenser': [('Dispenser air dingin tidak dingin', 'Air dari keran dingin keluar dengan suhu normal.', 'low',
                   'Perbaikan kompresor kecil dispenser', 200_000, 'ok', 'Sudah diperbaiki teknisi.', False)],
    'Access Point': [('WiFi kantor sering putus', 'Koneksi WiFi putus-sambung setiap beberapa menit.', 'medium',
                      'Ganti adaptor PoE access point', 250_000, 'replaced_component', 'Adaptor PoE diganti, koneksi stabil.', False)],
    'Mesin Fotokopi': [('Mesin fotokopi muncul kode error', 'Muncul kode error SC542 dan mesin tidak bisa dipakai.', 'medium',
                        'Service vendor, ganti thermistor fuser', 750_000, 'replaced_component', 'Sudah diservice vendor.', False)],
    'TV / Monitor Display': [('TV tidak ada gambar', 'Ada suara tapi layar gelap.', 'medium',
                              'Ganti backlight LED', 400_000, 'replaced_component', 'Backlight sudah diganti.', False)],
}
REJECT_NOTES = [
    'Setelah dicek di lokasi perangkat berfungsi normal. Masalah ada di jaringan kantor dan sudah ditangani tim IT.',
    'Laporan ganda, sudah ditangani di laporan sebelumnya.',
    'Bukan kerusakan perangkat, hanya perlu pengaturan ulang. Sudah dijelaskan ke pelapor.',
]
RESOLVE_DAYS = {'critical': (0, 0), 'high': (0, 2), 'medium': (1, 4), 'low': (1, 6)}
RATE_PER_YEAR = {'Laptop': 1.3, 'Desktop / PC': 1.0, 'Kursi Kerja': 0.7, 'Kasur': 0.6,
                 'Lemari Pakaian': 0.5, 'Tempat Tidur': 0.5}

# Laporan yang masih berjalan hari ini: (aset, pelapor, template judul, umur jam, status, catatan GA)
ACTIVE_NOW = [
    ('IT-LP-008', 'putri', 'Beberapa tombol keyboard tidak berfungsi', 5, 'pending', ''),
    ('FRN-KS-001', 'rudi', 'Per kasur menonjol', 26, 'pending', ''),
    ('FRN-KK-011', 'maya', 'Hidrolik kursi turun sendiri', 52, 'acknowledged',
     'Sparepart hidrolik sudah dipesan, estimasi datang Kamis. Sementara pakai kursi cadangan di ruang meeting.'),
    ('IT-PC-002', 'yusuf', 'PC tidak mau menyala', 75, 'in_progress',
     'Teknisi sudah cek: power supply rusak. Unit pengganti dikirim dari Angsana.'),
]

RELOCATE_CATEGORIES = ['TV / Monitor Display', 'Proyektor', 'Printer', 'Dispenser', 'Microwave', 'Kursi Meeting',
                       'Whiteboard', 'Speaker / Audio System', 'Rak Dokumen', 'Sofa', 'Water Heater', 'Meja Meeting',
                       'Scanner', 'Paper Shredder']
RELOCATE_REASONS = [
    'Dipindah untuk kebutuhan ruang rapat',
    'Penataan ulang ruangan setelah renovasi',
    'Dipindah karena penambahan personel di ruangan ini',
    'Relokasi atas permintaan departemen pemilik',
    'Dipindah dari gudang GA setelah diperbaiki',
]


class Command(BaseCommand):
    help = 'Tambah data simulasi lapangan: karyawan, serah terima, movement, laporan kerusakan.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Jalankan lalu batalkan (rollback), tampilkan ringkasan.')

    def handle(self, *args, **opts):
        if User.objects.filter(username=MARKER_USERNAME).exists():
            raise CommandError('Data simulasi sudah pernah ditambahkan (akun fajar.nugroho sudah ada).')
        self.rng = random.Random(20261027)
        self.stats = defaultdict(int)
        try:
            with transaction.atomic():
                self.run()
                self.report_summary()
                if opts['dry_run']:
                    raise _Rollback()
        except _Rollback:
            self.stdout.write(self.style.WARNING('Dry run: semua perubahan dibatalkan.'))
            return
        self.stdout.write(self.style.SUCCESS('Data simulasi ditambahkan.'))

    # ── Langkah utama ───────────────────────────────────────────────────────
    def run(self):
        self.ga_staff = {e.nama: e for e in Employee.objects.filter(ga_profile__isnull=False)}
        self.emp = self.create_employees()
        self.ga_staff['Fajar Nugroho'] = self.emp['fajar']
        self.assets = {a.asset_number: a for a in Asset.objects.select_related(
            'asset_category', 'lokasi_asset', 'owned_by_department', 'financial')}
        self.budget = self.repair_budget()
        windows = self.create_assignments()
        self.create_active_reports()
        self.create_history_reports(windows)
        self.create_common_reports()
        self.create_relocations()

    def holder(self, key):
        return self.emp.get(key) or Employee.objects.get(nama=key)

    def create_employees(self):
        depts = {dep.name: dep for dep in Department.objects.all()}
        out = {}
        for i, (key, nama, jabatan, dept, role, resign) in enumerate(EMPLOYEES, start=1):
            first, *rest = nama.split()
            username = '.'.join(nama.lower().split()[:2])
            if User.objects.filter(username=username).exists():
                username = f'{username}{i}'
            user = User.objects.create_user(username, password=SIM_PASSWORD, first_name=first,
                                            last_name=' '.join(rest), is_active=resign is None)
            emp = Employee.objects.create(user=user, employee_id=f'BIB-{1000 + i * 7}', nama=nama,
                                          department=depts[dept], jabatan=jabatan)
            (GAProfile if role == 'ga' else UserProfile).objects.create(employee=emp)
            out[key] = emp
            self.stats['karyawan'] += 1
        return out

    def repair_budget(self):
        """Sisa ruang biaya & jumlah corrective per aset supaya kesehatan tetap wajar."""
        agg = {r['asset_id']: r for r in MaintenanceRecord.objects.filter(maintenance_type='corrective')
               .values('asset_id').annotate(total=Sum('biaya'), n=Count('id'))}
        budget = {}
        for a in self.assets.values():
            fin = getattr(a, 'financial', None)
            spent = float(agg.get(a.pk, {}).get('total') or 0)
            count = agg.get(a.pk, {}).get('n') or 0
            if fin:
                cap = float(fin.nilai_awal_aset) * get_repair_threshold(fin.nilai_awal_aset)['warning'] / 100 * 0.85
            else:
                cap = 1_500_000
            budget[a.pk] = {'money': cap - spent, 'count': 4 - count}
        return budget

    # ── Serah terima + movement ─────────────────────────────────────────────
    def create_assignments(self):
        windows = []  # (aset, pemegang, mulai, selesai)
        for number, plan in ASSIGNMENTS.items():
            asset = self.assets[number]
            current = asset.movements.filter(is_current=True).first()
            floor = (current.moved_in.astimezone(TZ).date() + timedelta(days=2)) if current else asset.tanggal_pembelian
            events = []
            shift = timedelta(0)
            for idx, (who, start, end, reason, note) in enumerate(plan):
                emp = self.holder(who)
                start = d(start) + shift
                if idx == 0 and start < floor:
                    shift = floor - start
                    start = floor
                end = (d(end) + shift) if end else None
                if end and end >= TODAY:
                    end = None
                by = self.recorder(asset)
                status = 'active'
                if end:
                    nxt = plan[idx + 1] if idx + 1 < len(plan) else None
                    status = 'transferred' if nxt and d(nxt[1]) + shift <= end else 'returned'
                AssetAssignment.objects.filter(asset=asset, status='active').update(status='transferred', returned_date=start)
                a = AssetAssignment.objects.create(asset=asset, assigned_to=emp, assigned_by=by, assigned_date=start,
                                                   returned_date=end, status=status, notes=note)
                AssetAssignment.objects.filter(pk=a.pk).update(created_at=at(start, 10))
                self.stats['serah terima'] += 1
                events.append((at(start, 10, self.rng.randint(0, 50)), emp, emp.department or asset.owned_by_department,
                               f'Serah terima ke {emp.nama}', by))
                if end:
                    events.append((at(end, 15, self.rng.randint(0, 50)), None, asset.owned_by_department,
                                   END_NOTES.get(reason, 'Aset dikembalikan ke GA'), by))
                windows.append((asset, emp, start, end or TODAY))
            self.append_movements(asset, current, events)
        return windows

    def recorder(self, asset):
        site = (asset.lokasi_asset.full_path or '') if asset.lokasi_asset else ''
        if site.startswith(('Kusan', 'Bunati')):
            return self.ga_staff['Fajar Nugroho']
        return self.rng.choice([g for n, g in self.ga_staff.items() if n != 'Fajar Nugroho'])

    def append_movements(self, asset, current, events):
        """Tutup posisi terakhir lalu susun movement baru berurutan; lokasi aset tidak berubah."""
        events.sort(key=lambda e: e[0])
        if current:
            if events[0][0] <= current.moved_in:
                raise CommandError(f'{asset.asset_number}: serah terima {events[0][0]:%Y-%m-%d} sebelum posisi terakhir.')
            AssetMovement.objects.filter(pk=current.pk).update(is_current=False, moved_out=events[0][0])
        location = asset.lokasi_asset
        for i, (when, held, dept, notes, by) in enumerate(events):
            last = i == len(events) - 1
            m = AssetMovement.objects.create(asset=asset, held_by=held, department=dept, location=location,
                                             moved_in=when, moved_out=None if last else events[i + 1][0],
                                             is_current=last, notes=notes, recorded_by=by)
            AssetMovement.objects.filter(pk=m.pk).update(created_at=when)
            self.stats['movement'] += 1

    # ── Laporan kerusakan ───────────────────────────────────────────────────
    def handler_for(self, asset):
        return self.recorder(asset)

    def pick_template(self, asset, used):
        pool = T.get(asset.asset_category.name if asset.asset_category else '', [])
        fresh = [t for t in pool if t[0] not in used] or pool
        return self.rng.choice(fresh) if fresh else None

    def fits_budget(self, asset, cost):
        b = self.budget[asset.pk]
        return b['count'] >= 1 and b['money'] >= cost

    def spend(self, asset, cost):
        self.budget[asset.pk]['count'] -= 1
        self.budget[asset.pk]['money'] -= cost

    def create_history_reports(self, windows):
        busy = defaultdict(list)  # aset → tanggal laporan (jaga jarak antarlaporan)
        for number, *_ in ACTIVE_NOW:
            busy[number].append(TODAY)
        used = defaultdict(set)
        for asset, emp, start, end in windows:
            if asset.asset_number in PROTECTED_ASSETS:
                continue
            span = (end - start).days - 30
            if span < 30:
                continue
            rate = RATE_PER_YEAR.get(asset.asset_category.name if asset.asset_category else '', 0.3)
            expected = span / 365 * rate
            n = int(expected) + (1 if self.rng.random() < expected - int(expected) else 0)
            for _ in range(n):
                when = start + timedelta(days=20 + self.rng.randint(0, span))
                if when >= TODAY - timedelta(days=7) or any(abs((when - x).days) < 45 for x in busy[asset.asset_number]):
                    continue
                tpl = self.pick_template(asset, used[asset.pk])
                if not tpl:
                    continue
                cost = self.cost(tpl[4])
                if not self.fits_budget(asset, cost):
                    continue
                busy[asset.asset_number].append(when)
                used[asset.pk].add(tpl[0])
                self.make_closed_report(asset, emp, when, tpl, cost)

    def create_common_reports(self):
        sites = [('yusuf', 'Kusan'), ('rina', 'Bunati')]
        for key, site in sites:
            emp = self.emp[key]
            since = AssetAssignment.objects.filter(assigned_to=emp).order_by('assigned_date').first().assigned_date
            pool = [a for a in self.assets.values()
                    if a.lifecycle_status == 'active' and a.lokasi_asset and (a.lokasi_asset.full_path or '').startswith(site)
                    and a.asset_number not in PROTECTED_ASSETS and self.common_templates(a)]
            self.rng.shuffle(pool)
            for asset in pool[:9]:
                tpl = self.rng.choice(self.common_templates(asset))
                cost = self.cost(tpl[4])
                if not self.fits_budget(asset, cost):
                    continue
                when = since + timedelta(days=self.rng.randint(30, max(31, (TODAY - since).days - 20)))
                self.make_closed_report(asset, emp, when, tpl, cost)

    def common_templates(self, asset):
        cat = asset.asset_category.name if asset.asset_category else ''
        for key, tpls in T_COMMON.items():
            if cat == key or cat.startswith(key) or (key == 'AC' and cat.startswith('AC')):
                return tpls
        return None

    def cost(self, base):
        if not base:
            return 0
        return int(round(base * self.rng.uniform(0.85, 1.2) / 5000) * 5000)

    def make_closed_report(self, asset, emp, when, tpl, cost):
        title, desc, sev, action, _, hasil, note, can_reject = tpl
        reported = at(when, self.rng.randint(7, 16), self.rng.choice([0, 10, 20, 30, 40, 50]))
        handler = self.handler_for(asset)
        rejected = can_reject and self.rng.random() < 0.5
        rep = DamageReport(asset=asset, reported_by=emp, title=title, description=desc, severity=sev,
                           handled_by=handler)
        DamageReport.objects.bulk_create([rep])   # tanpa save(): laporan lama tidak mengubah status/jadwal aset
        if rejected:
            DamageReport.objects.filter(pk=rep.pk).update(
                report_status='rejected', ga_notes=self.rng.choice(REJECT_NOTES),
                reported_at=reported, updated_at=reported + timedelta(hours=self.rng.randint(2, 30)))
            self.stats['laporan ditolak'] += 1
            return
        lo, hi = RESOLVE_DAYS[sev]
        days = self.rng.randint(lo, hi)
        if days == 0:   # kritis/parah: dikerjakan hari itu juga
            resolved = reported + timedelta(hours=self.rng.randint(2, 6), minutes=self.rng.choice([0, 15, 30, 45]))
        else:
            resolved = at(when + timedelta(days=days), self.rng.randint(9, 16), self.rng.choice([0, 15, 30, 45]))
        rec = MaintenanceRecord(
            asset=asset, maintenance_type='corrective', tanggal_pelaksanaan=resolved.date(),
            deskripsi_pekerjaan=(f"[Dari Laporan Kerusakan #{rep.pk}]\nJudul: {title}\n"
                                 f"Deskripsi awal: {desc}\n\nTindakan perbaikan: {action}"),
            hasil=hasil, biaya=Decimal(cost), performed_by=handler,
            catatan_tambahan='Dikerjakan vendor' if cost >= 600_000 else '',
        )
        MaintenanceRecord.objects.bulk_create([rec])   # tanpa save(): tidak membuat jadwal preventive baru
        MaintenanceRecord.objects.filter(pk=rec.pk).update(created_at=resolved)
        sch = MaintenanceSchedule(
            asset=asset, scheduled_date=when, status='done', damage_report=rep, maintenance_record=rec,
            notes=(f"[CORRECTIVE - Laporan Kerusakan #{rep.pk}] {title} "
                   f"(Dilaporkan oleh: {emp.nama}, Severity: {dict(DamageReport.SEVERITY)[sev]})"))
        MaintenanceSchedule.objects.bulk_create([sch])
        MaintenanceSchedule.objects.filter(pk=sch.pk).update(created_at=reported)
        DamageReport.objects.filter(pk=rep.pk).update(
            report_status='resolved', ga_notes=note, resolved_maintenance=rec,
            reported_at=reported, resolved_at=resolved, updated_at=resolved)
        self.spend(asset, cost)
        self.stats['laporan selesai'] += 1
        self.stats['biaya perbaikan'] += cost

    def create_active_reports(self):
        templates = {t[0]: t for tpls in T.values() for t in tpls}
        for number, key, title, hours, status, ga_note in ACTIVE_NOW:
            asset = Asset.objects.get(asset_number=number)
            if asset.lifecycle_status != 'active' or asset.damage_reports.filter(
                    report_status__in=DamageReport.ACTIVE_STATUS).exists():
                continue
            tpl = templates[title]
            reported = datetime.now(TZ) - timedelta(hours=hours)
            rep = DamageReport(asset=asset, reported_by=self.emp[key], title=tpl[0], description=tpl[1], severity=tpl[2])
            rep.save()   # alur asli: aset → Maintenance, jadwal corrective dibuat
            fields = {'reported_at': reported, 'updated_at': reported}
            if status != 'pending':
                fields.update(report_status=status, handled_by=self.handler_for(asset), ga_notes=ga_note,
                              updated_at=reported + timedelta(hours=3))
            DamageReport.objects.filter(pk=rep.pk).update(**fields)
            rep.corrective_schedules.update(scheduled_date=reported.date())
            self.stats['laporan aktif'] += 1

    # ── Relokasi aset umum ──────────────────────────────────────────────────
    def create_relocations(self):
        single = set(AssetMovement.objects.values('asset_id').annotate(n=Count('id')).filter(n=1).values_list('asset_id', flat=True))
        assigned = set(AssetAssignment.objects.values_list('asset_id', flat=True))
        pool = [a for a in self.assets.values()
                if a.pk in single and a.pk not in assigned and a.lifecycle_status == 'active'
                and a.asset_number not in PROTECTED_ASSETS and a.lokasi_asset
                and a.asset_category and a.asset_category.name in RELOCATE_CATEGORIES]
        self.rng.shuffle(pool)
        locations = list(Location.objects.all())
        moved = 0
        for asset in pool:
            if moved >= 24:
                break
            cur = asset.movements.get(is_current=True)
            root = (asset.lokasi_asset.full_path or asset.lokasi_asset.name).split(' > ')[0]
            options = [l for l in locations if l.pk != asset.lokasi_asset_id and (l.full_path or '').startswith(root + ' > ')
                       and (l.full_path or '').count(' > ') >= 1]
            start = cur.moved_in.astimezone(TZ).date()
            span = (TODAY - start).days
            if not options or span < 120:
                continue
            when = start + timedelta(days=self.rng.randint(60, span - 30))
            switch = at(when, 13, self.rng.choice([0, 15, 30, 45]))
            old = AssetMovement.objects.create(asset=asset, held_by=None, department=cur.department,
                                               location=self.rng.choice(options), moved_in=cur.moved_in,
                                               moved_out=switch, is_current=False, notes=cur.notes,
                                               recorded_by=cur.recorded_by)
            AssetMovement.objects.filter(pk=old.pk).update(created_at=cur.moved_in)
            AssetMovement.objects.filter(pk=cur.pk).update(moved_in=switch, notes=self.rng.choice(RELOCATE_REASONS),
                                                           recorded_by=self.recorder(asset), created_at=switch)
            moved += 1
            self.stats['relokasi'] += 1
            self.stats['movement'] += 1

    def report_summary(self):
        for k, v in self.stats.items():
            val = f'Rp {v:,.0f}'.replace(',', '.') if k == 'biaya perbaikan' else v
            self.stdout.write(f'  {k:18} {val}')


class _Rollback(Exception):
    pass
