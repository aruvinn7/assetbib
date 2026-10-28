"""
python manage.py bangun_ulang_data --yes [--seed 2026]

Membangun ulang SELURUH data aset PT Borneo Indobara seperti sistem yang sudah berjalan
sejak 2019: lokasi tiga site, grup & kategori, ±1.100 aset penunjang operasional dan
tambang per departemen, lalu riwayatnya sampai hari ini — serah terima (pribadi &
departemen), perpindahan & laporan pindah karyawan, laporan kerusakan, maintenance
preventive/inspeksi/corrective, penggantian komponen, Berita Acara Kerusakan &
Penghapusan, dan write off.

Yang dipertahankan: karyawan lama (ID, akun, password, peran, departemen), departemen,
lembar pengesahan BA, serta aset 1.1.2.15 (kulkas Sharp) dan 1.3.2 (AC Daikin) beserta
fotonya. Karyawan simulasi baru memakai password PASSWORD di bawah.

Data dibuat langsung (bulk) dengan aturan yang sama seperti alur aplikasi: satu movement
aktif & satu serah terima aktif per aset, jadwal preventive berikutnya dihitung dari
pekerjaan terakhir, laporan aktif → aset Maintenance & jadwal preventive dilewati, dll.
"""
import heapq
import random
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.apps import apps
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from bibasset.models import (
    BA_NOMOR_FORMAT, BULAN_ROMAWI, Asset, AssetAssignment, AssetCategory, AssetComponent, AssetFinancial,
    AssetGroup, AssetMaintenanceConfig, AssetMovement, AssetPhoto, AssetWriteOff, BeritaAcara, BeritaAcaraFoto,
    BeritaAcaraItem, ComponentReplacement, DamageReport, Department, Employee, FinanceProfile, GAProfile, Location,
    MaintenanceRecord, MaintenanceSchedule, MovementReport, UserProfile, get_repair_threshold, ringkas_laporan,
    salinan_penanda_tangan,
)

from . import _katalog as K

PASSWORD = 'Simulasi#2026'
TZ = ZoneInfo('Asia/Jakarta')
START = date(2019, 1, 7)
PROTECTED = {
    '1.1.2.15': 'Angsana > Office Angsana > Lantai 1 > Pantry',
    '1.3.2': 'Angsana > Mess > Mess 1 > Kamar 1.1',
}
PROTECTED_CAT = {'1.1.2.15': ('Elektronik', 'Kulkas / Refrigerator'), '1.3.2': ('Elektronik', 'AC')}
WRITE_OFF_LABEL = dict(AssetWriteOff.REASON)
SEVERITY_LABEL = dict(DamageReport.SEVERITY)
HASIL_LABEL = {k: v.split(' —')[0] for k, v in MaintenanceRecord.RESULT}


def dt(d, h=9, m=0):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def workday(d, back=True):
    while d.weekday() >= 5:
        d += timedelta(days=-1 if back else 1)
    return d


def d_of(x):
    return x.astimezone(TZ).date() if isinstance(x, datetime) else x


@contextmanager
def manual_timestamps():
    """Matikan auto_now / auto_now_add sementara supaya tanggal riwayat bisa diisi sendiri."""
    touched = []
    for model in apps.get_app_config('bibasset').get_models():
        for f in model._meta.concrete_fields:
            if getattr(f, 'auto_now', False) or getattr(f, 'auto_now_add', False):
                touched.append((f, f.auto_now, f.auto_now_add))
                f.auto_now = f.auto_now_add = False
    try:
        yield
    finally:
        for f, now, add in touched:
            f.auto_now, f.auto_now_add = now, add


class E:
    """Karyawan dalam simulasi."""

    def __init__(self, emp, dept, site, join, leave):
        self.emp, self.dept, self.site, self.join, self.leave = emp, dept, site, join, leave
        job = emp.jabatan
        self.field = any(k in job for k in K.FIELD_ROLES)
        self.lead = any(k in job for k in K.LEAD_ROLES)
        self.tier = 'atas' if self.lead else 'teknis' if any(k in job for k in K.TECH_JOBS) else 'standar'

    def active(self, d):
        return self.join <= d and (self.leave is None or d <= self.leave)


class Plan:
    """Satu aset beserta seluruh rencana riwayatnya."""
    counter = 0

    def __init__(self, code, cat, name, price, life, buy, loc, dept, opts='', protected=None):
        Plan.counter += 1
        self.key = Plan.counter
        self.code, self.cat, self.name, self.price, self.life = code, cat, name, price, life
        self.buy, self.loc, self.dept, self.opts = buy, loc, dept, opts
        self.fam = K.FAMILY.get(cat.name, 'office')
        for word, fam in K.NAME_FAMILY:
            if word in name:
                self.fam = fam
        self.protected = protected      # Asset lama yang dipertahankan
        self.segs = []                  # posisi berurutan (lihat seg())
        self.writeoff = None            # dict tanggal, alasan, keterangan, nilai_jual, rujukan_lemon
        self.lemon = False              # sering rusak → BA rekomendasi hapus
        self.final_report = None        # tanggal laporan terakhir aset lemon
        self.forced = []                # laporan wajib (rusak saat dikembalikan)
        self.use = []                   # periode dipakai (untuk barang pribadi)
        self.personal = False
        self.po = None
        self.asset = None

    def seg(self, t, loc, owner, holder=None, share=None, note='', by=None, ret='', report=None, ho_note=''):
        if self.segs and t <= self.segs[-1]['t']:
            t = self.segs[-1]['t'] + timedelta(minutes=30)
        s = dict(t=t, loc=loc, owner=owner, holder=holder, share=share, note=note, by=by, ret=ret,
                 report=report, ho_note=ho_note)
        self.segs.append(s)
        return s

    def at(self, d):
        """Segmen yang berlaku pada tanggal d."""
        cur = self.segs[0]
        for s in self.segs:
            if d_of(s['t']) <= d:
                cur = s
            else:
                break
        return cur

    @property
    def end(self):
        return self.writeoff['date'] if self.writeoff else None


class Command(BaseCommand):
    help = 'Bangun ulang seluruh data aset simulasi (kecuali karyawan lama & departemen).'

    def add_arguments(self, parser):
        parser.add_argument('--yes', action='store_true', help='Konfirmasi: data lama selain karyawan akan dihapus')
        parser.add_argument('--seed', type=int, default=2026)

    # ════════════════════════════════════════════════════════════════════
    def handle(self, *args, **opts):
        if not opts['yes']:
            raise CommandError('Perintah ini menghapus semua data aset. Jalankan dengan --yes bila yakin.')
        self.rng = random.Random(opts['seed'])
        self.today = timezone.localdate()
        self.now = timezone.now()
        self.stats = defaultdict(int)
        with transaction.atomic(), manual_timestamps():
            self.keep = self.wipe()
            self.build_locations()
            self.build_categories()
            self.build_employees()
            self.plans = []
            self.plan_shared()
            self.plan_fates()
            self.plan_personal()
            self.plan_rooms_and_lockers()
            self.plan_shared_history()
            self.number_assets()
            self.save_assets()
            self.plan_maintenance()
            self.save_history()
            self.save_maintenance()
            self.save_writeoffs_and_ba()
            self.finalize()
            self.verify()
        for k in sorted(self.stats):
            self.stdout.write(f'  {k:32} {self.stats[k]}')
        self.stdout.write(self.style.SUCCESS('Data simulasi selesai dibangun ulang.'))

    # ── 1. Bersihkan ────────────────────────────────────────────────────
    def wipe(self):
        keep_assets = list(Asset.objects.filter(asset_number__in=PROTECTED))
        if len(keep_assets) != len(PROTECTED):
            raise CommandError('Aset berfoto 1.1.2.15 / 1.3.2 tidak ditemukan.')
        keep_ids = [a.pk for a in keep_assets]
        for model in (BeritaAcaraFoto, BeritaAcaraItem, BeritaAcara, MovementReport, MaintenanceSchedule,
                      ComponentReplacement, DamageReport, MaintenanceRecord, AssetComponent, AssetMaintenanceConfig,
                      AssetWriteOff, AssetAssignment, AssetMovement, AssetFinancial):
            model.objects.all()._raw_delete(model.objects.db)
        AssetPhoto.objects.exclude(asset_id__in=keep_ids)._raw_delete(AssetPhoto.objects.db)
        Asset.objects.exclude(pk__in=keep_ids)._raw_delete(Asset.objects.db)
        Asset.objects.filter(pk__in=keep_ids).update(lokasi_asset=None, asset_category=None, owned_by_department=None)
        for model in (Location, AssetCategory, AssetGroup):
            model.objects.all()._raw_delete(model.objects.db)
        with connection.cursor() as c:
            for model in (BeritaAcaraFoto, BeritaAcaraItem, BeritaAcara, MovementReport, MaintenanceSchedule,
                          ComponentReplacement, DamageReport, MaintenanceRecord, AssetComponent, AssetMaintenanceConfig,
                          AssetWriteOff, AssetAssignment, AssetMovement, AssetFinancial, Location, AssetCategory, AssetGroup):
                c.execute('UPDATE sqlite_sequence SET seq = 0 WHERE name = %s', [model._meta.db_table])
        # Karyawan simulasi lama dibuat ulang; karyawan asli tidak disentuh
        return {a.asset_number: a for a in keep_assets}

    # ── 2. Master data ──────────────────────────────────────────────────
    def build_locations(self):
        self.L = {}

        def walk(parent, path, node):
            items = node.items() if isinstance(node, dict) else ((n, []) for n in node)
            for name, children in items:
                storage = name.startswith('*')
                name = name.lstrip('*')
                full = f'{path} > {name}' if path else name
                loc = Location(name=name, parent=parent, full_path=full, is_storage=storage)
                loc.save_base(raw=True)
                self.L[full] = loc
                walk(loc, full, children)

        walk(None, '', K.LOCATION_TREE)
        self.stats['lokasi'] = len(self.L)

    def loc(self, path):
        try:
            return self.L[path]
        except KeyError:
            raise CommandError(f'Lokasi tidak ada di katalog: {path}')

    def build_categories(self):
        self.C = {}
        self.cat_meta = {}
        for gname, cats in K.CATEGORIES.items():
            g = AssetGroup.objects.create(name=gname)
            for name, val, unit, personal, life, kind, checklist in cats:
                lines = [l for l in checklist.splitlines() if l.strip()]
                c = AssetCategory.objects.create(
                    group=g, name=name, default_interval_value=val, default_interval_unit=unit, personal_use=personal,
                    maintenance_description='\n'.join(f'{i}. {l}' for i, l in enumerate(lines, 1)),
                )
                self.C[(gname, name)] = c
                self.cat_meta[c.pk] = dict(life=life, kind=kind, checklist=lines)
        self.by_name = {}
        for (g, n), c in self.C.items():
            self.by_name.setdefault(n, c)
        self.stats['grup'] = len(K.CATEGORIES)
        self.stats['kategori'] = len(self.C)

    def cat(self, name):
        if '::' in name:
            return self.C[tuple(name.split('::', 1))]
        return self.by_name[name]

    def build_employees(self):
        self.D = {d.name: d for d in Department.objects.all()}
        # Karyawan simulasi baru dibuat ulang setiap kali (idempoten)
        names = [n for n, *_ in K.NEW_EMPLOYEES]
        old_new = Employee.objects.filter(nama__in=names).select_related('user')
        User.objects.filter(pk__in=[e.user_id for e in old_new]).delete()
        pw = make_password(PASSWORD)
        taken = set(User.objects.values_list('username', flat=True))
        last_id = max(int(e.split('-')[1]) for e in Employee.objects.filter(employee_id__startswith='BIB-')
                      .values_list('employee_id', flat=True)) if Employee.objects.filter(employee_id__startswith='BIB-').exists() else 1000
        created = []
        for i, (nama, jabatan, dept, site, ga, join, leave) in enumerate(K.NEW_EMPLOYEES, start=1):
            parts = nama.lower().replace("'", '').split()
            username = '.'.join(parts[:2]) if len(parts) > 1 else parts[0]
            while username in taken:
                username += str(i)
            taken.add(username)
            user = User(username=username, password=pw, first_name=nama.split()[0], last_name=' '.join(nama.split()[1:]),
                        is_active=leave is None, date_joined=dt(date.fromisoformat(join), 8))
            user.save()
            emp = Employee.objects.create(user=user, employee_id=f'BIB-{last_id + i * 7}', nama=nama,
                                          department=self.D[dept], jabatan=jabatan)
            UserProfile.objects.create(employee=emp)
            if ga:
                GAProfile.objects.create(employee=emp)
            created.append(emp)
        self.stats['karyawan baru'] = len(created)
        new_info = {n: (site, join, leave) for n, _, _, site, _, join, leave in K.NEW_EMPLOYEES}

        self.E = []
        self.staff = defaultdict(list)
        self.ga_staff = []
        self.fin_staff = []
        for emp in Employee.objects.select_related('department', 'user', 'user_profile', 'ga_profile', 'finance_profile'):
            dept = emp.department.name if emp.department else None
            if emp.nama in new_info:
                site, join, leave = new_info[emp.nama]
                join, leave = date.fromisoformat(join), (date.fromisoformat(leave) if leave else None)
            else:
                site = K.OLD_SITE.get(emp.nama) or ('Bunati' if dept in K.PORT_DEPTS else 'Angsana')
                join = date.fromisoformat(K.OLD_JOIN.get(emp.nama, '2019-01-02'))
                leave = date.fromisoformat(K.OLD_RESIGN[emp.nama]) if emp.nama in K.OLD_RESIGN else None
                if not emp.user.is_active and leave is None:
                    leave = date(2025, 12, 31)
            e = E(emp, dept, site, join, leave)
            e.has_user = hasattr(emp, 'user_profile')
            if hasattr(emp, 'ga_profile'):
                self.ga_staff.append(e)
            if hasattr(emp, 'finance_profile') or dept == 'Finance':
                self.fin_staff.append(e)
            self.E.append(e)
            if dept:
                self.staff[dept].append(e)
        self.by_emp = {e.emp.pk: e for e in self.E}

    # ── Pilihan orang ───────────────────────────────────────────────────
    def pick(self, people, d, site=None):
        live = [p for p in people if p.active(d)]
        if site:
            same = [p for p in live if p.site == site]
            live = same or live
        return self.rng.choice(live) if live else None

    def dept_staff(self, dept, d, site=None, kws=None, need_user=True):
        people = self.staff.get(dept.name if dept else '', [])
        if kws:
            people = [p for p in people if any(k in p.emp.jabatan for k in kws)] or people
        return self.pick([p for p in people if p.has_user or not need_user], d, site)

    def ga(self, d, site='Angsana'):
        return (self.pick(self.ga_staff, d, site) or self.ga_staff[0]).emp

    def site_of(self, loc):
        return loc.site if loc else 'Angsana'

    def gudang(self, site):
        return self.loc(K.GUDANG[site])

    def model_for(self, models, year):
        ok = [m for m in models if m[2] <= year <= m[3]] or models
        return self.rng.choice(ok)

    def price(self, base):
        return int(round(base * self.rng.uniform(0.95, 1.06) / 5000) * 5000)

    # ── 3. Aset bersama ─────────────────────────────────────────────────
    def plan_shared(self):
        self.label_seq = defaultdict(int)
        for code, cat_name, count, dept, locs, models, (y0, y1), opts in K.LINES:
            cat = self.cat(cat_name)
            life = self.cat_meta[cat.pk]['life']
            years = self.spread_years(count, y0, y1)
            batches = defaultdict(list)
            for i in range(count):
                batches[years[i]].append(i)
            for year, idxs in sorted(batches.items()):
                po_day = self.po_date(year)
                for i in idxs:
                    buy = min(po_day + timedelta(days=self.rng.randint(0, 14)), self.today - timedelta(days=3))
                    loc = self.loc(locs[i % len(locs)])
                    self.add_shared(code, cat, models, buy, loc, self.D[dept], opts, life)
        for code, cat_name, dept, path, model, base, opts, age in K.RECENT_PURCHASES:
            cat = self.cat(cat_name)
            buy = workday(self.today - timedelta(days=age))
            p = self.add_shared(code, cat, [(model, base, 2019, 2100)], buy, self.loc(path), self.D[dept], opts,
                                self.cat_meta[cat.pk]['life'])
            p.recent = True
        for number, path in PROTECTED.items():
            a = self.keep[number]
            g, c = PROTECTED_CAT[number]
            p = Plan(number, self.C[(g, c)], a.nama_asset, 0, 8, a.tanggal_pembelian, self.loc(path), self.D['HRGA'],
                     protected=a)
            fin = AssetFinancial.objects.filter(asset=a).first()
            p.price = int(fin.nilai_awal_aset) if fin else (2_230_000 if number == '1.1.2.15' else 5_142_000)
            p.po = a.no_po
            self.plans.append(p)
        self.stats['aset bersama (rencana)'] = len(self.plans)

    def add_shared(self, code, cat, models, buy, loc, dept, opts, life, year=None):
        if models == 'LV':
            model, base, *_ = self.model_for(K.LV_MODELS, buy.year)
        else:
            model, base, *_ = self.model_for(models, buy.year)
        name = model
        if 'plat' in opts:
            name = f'{model} - DA {self.rng.randint(8001, 9899)} {self.rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ")}{self.rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ")}'
        for tok in opts.split():
            if tok.startswith('label:'):
                lab = tok.split(':')[1]
                self.label_seq[lab] += 1
                name = f'{model} ({lab}-{self.label_seq[lab]:02d})'
        p = Plan(code, cat, name, self.price(base), life, buy, loc, dept, opts)
        p.line_models = models
        if cat.name in ('Light Vehicle', 'Truk Operasional', 'Bus Karyawan', 'Ambulans', 'Fire Truck'):
            brand = model.split()[1] if model.split()[0] in ('Bus', 'Microbus', 'Truk', 'Ambulans', 'Fire') else model.split()[0]
            p.comp_key = K.VEHICLE_COMP.get(brand, 'truk' if cat.name != 'Light Vehicle' else 'lv_toyota')
        else:
            p.comp_key = p.fam if p.fam in K.COMPONENTS else None
        self.plans.append(p)
        return p

    def spread_years(self, count, y0, y1):
        years = list(range(y0, y1 + 1))
        weights = [1.6 if y <= 2021 else 1.0 for y in years]
        out = sorted(self.rng.choices(years, weights=weights, k=count))
        return out

    def po_date(self, year):
        d = date(year, self.rng.randint(1, 12), self.rng.randint(1, 25))
        if year == self.today.year:
            d = min(d, self.today - timedelta(days=self.rng.randint(20, 90)))
        return workday(max(d, START))

    # ── 4. Nasib aset bersama (write off) ───────────────────────────────
    def ba_calendar(self, year):
        out = [workday(date(year, 6, 20)), workday(date(year, 12, 15))]
        return [d for d in out if d < self.today]

    def next_ba(self, d):
        for y in range(d.year, self.today.year + 1):
            for b in self.ba_calendar(y):
                if b >= d + timedelta(days=5):
                    return b
        return None

    def plan_fates(self):
        rng = self.rng
        shared = [p for p in self.plans if not p.protected]

        def pick(pred, n, sort=None):
            pool = [p for p in shared if pred(p) and not p.writeoff and not p.lemon]
            if sort:
                pool.sort(key=sort)
                pool = pool[:max(n * 2, n)]
            rng.shuffle(pool)
            return pool[:n]

        lemon_fams = {'ac', 'dispenser', 'laundry', 'dapur', 'printer', 'ups', 'av', 'kulkas', 'fotokopi', 'pompa', 'lt',
                      'cctv', 'genset', 'office'}
        for p in pick(lambda p: p.fam in lemon_fams and p.buy.year <= 2022 and p.price < 400_000_000, 22):
            lo = max(p.buy + timedelta(days=700), date(2022, 2, 1))
            hi = self.today - timedelta(days=12)
            if lo >= hi:
                continue
            r = lo + timedelta(days=rng.randint(0, (hi - lo).days))
            p.lemon, p.final_report = True, workday(r)
            b = self.next_ba(p.final_report)
            if b:
                wo = workday(b + timedelta(days=rng.randint(14, 30)), back=False)
                if wo < self.today - timedelta(days=2):
                    p.writeoff = dict(date=wo, alasan='rusak', nilai_jual=None, lemon=True,
                                      keterangan='Rusak berat, biaya perbaikan tidak ekonomis sesuai Berita Acara Kerusakan.')
                    self.maybe_replace(p, wo)
        # Rusak berat yang baru dilaporkan — masih di draf BA dengan rekomendasi hapus
        for p in pick(lambda p: p.fam in lemon_fams and p.buy.year <= 2022 and p.price < 400_000_000, 3):
            p.lemon = True
            p.final_report = workday(self.today - timedelta(days=rng.randint(8, 70)))

        def fate(plans, alasan, when, ket, replace=False, jual=None):
            for p in plans:
                d = workday(when(p))
                if d <= p.buy + timedelta(days=200) or d >= self.today:
                    continue
                p.writeoff = dict(date=d, alasan=alasan, nilai_jual=jual(p) if jual else None, lemon=False, keterangan=ket)
                if replace:
                    self.maybe_replace(p, d)

        old = lambda p: p.buy
        fate(pick(lambda p: p.cat.name == 'TV / Monitor Display' and p.buy.year == 2019, 2, old), 'usang',
             lambda p: date(2024, 5, 21), 'Panel redup dan sudah tidak didukung update; diganti TV baru.', True)
        fate(pick(lambda p: p.cat.name == 'Mesin Fotokopi' and p.buy.year <= 2020, 1, old), 'usang',
             lambda p: date(2024, 5, 21), 'Counter melewati 1,2 juta lembar, sparepart tidak tersedia lagi.', True)
        fate(pick(lambda p: p.cat.name == 'Access Point' and 'TP-Link' in p.name, 2, old), 'usang',
             lambda p: date(2025, 5, 20), 'Tidak mendukung WiFi 6 dan manajemen terpusat; diganti perangkat baru.', True)
        fate(pick(lambda p: p.cat.name == 'Switch' and 'OfficeConnect' in p.name, 1, old), 'usang',
             lambda p: date(2025, 5, 20), 'End of support dari vendor; diganti switch PoE baru.', True)
        fate(pick(lambda p: p.cat.name == 'APAR' and p.buy.year == 2019, 3, old), 'usang',
             lambda p: date(2024, 11, 12), 'Tabung gagal uji hidrostatik (korosi), tidak layak direfill.', True)
        fate(pick(lambda p: p.cat.name == 'Monitor' and p.buy.year <= 2020, 1, old), 'usang',
             lambda p: date(2025, 5, 20), 'Layar menguning dan dead pixel; habis umur pakai.')
        fate(pick(lambda p: p.cat.name == 'Light Vehicle' and p.buy.year == 2019, 2, old), 'dijual',
             lambda p: date(2025, 3, 18), 'Dilelang melalui KPKNL/balai lelang setelah melewati 6 tahun pemakaian di area tambang.',
             True, jual=lambda p: Decimal(rng.randrange(165, 240) * 1_000_000))
        fate(pick(lambda p: p.cat.name in ('Rak Dokumen', 'Whiteboard') and p.buy.year <= 2020, 3, old), 'dihibahkan',
             lambda p: date(2024, 8, 14), 'Dihibahkan ke SDN 2 Angsana melalui program CSR & Empowerment.')
        fate(pick(lambda p: p.cat.name in ('Meja Meeting', 'Kursi Meeting') and p.buy.year <= 2019, 2, old), 'dihibahkan',
             lambda p: date(2024, 8, 14), 'Dihibahkan ke Kantor Desa Angsana melalui program CSR & Empowerment.')
        fate(pick(lambda p: p.cat.name == 'CCTV' and 'Pos KM 27' in p.loc.full_path, 1), 'hilang',
             lambda p: date(2023, 11, 8), 'Kamera dicuri di Pos KM 27; sudah dilaporkan ke Polsek Angsana (LP/112/XI/2023).', True)
        fate(pick(lambda p: p.cat.name == 'GPS Handheld', 1), 'hilang',
             lambda p: date(2024, 7, 23), 'Terjatuh di sungai saat survey batas IUP Kusan; pencarian 3 hari tidak berhasil.')
        self.stats['write off direncanakan (bersama)'] = sum(1 for p in self.plans if p.writeoff)

    def maybe_replace(self, p, wo):
        if not getattr(p, 'line_models', None):
            return
        buy = workday(wo - timedelta(days=self.rng.randint(4, 25)))
        if buy >= self.today or buy <= p.buy:
            return
        np_ = self.add_shared(p.code, p.cat, p.line_models, buy, p.loc, p.dept, p.opts, p.life)
        np_.replaces = p

    # ── 5. Barang pribadi: laptop, kursi kerja, meja manajer, HT ────────
    def needs(self, e):
        if not e.has_user or not e.dept:
            return []
        out = []
        home = self.home(e)
        office_room = 'Office' in home.full_path
        if not e.field:
            out.append('laptop')
            if office_room:
                out.append('kursi')
            if e.lead and office_room:
                out.append('meja')
        if e.field or e.dept in K.FIELD_DEPTS or e.dept in ('HSE', 'Coal Chain Coordinator', 'Operasional Tambang'):
            out.append('ht')
        return out

    def home(self, e):
        if getattr(e, '_home', None):
            return e._home
        kind = 'field' if e.field else 'office'
        path = None
        if e.dept in K.PORT_DEPTS:
            path = K.DEPT_HOME[e.dept] if not e.field else K.PO + ('Workshop Port' if 'Mekanik' in e.emp.jabatan or 'Electric' in e.emp.jabatan else 'Jetty 1')
            if 'Laboratorium' in e.emp.jabatan or 'Sampler' in e.emp.jabatan:
                path = K.PO + 'Laboratorium Batubara'
        elif e.site in ('Kusan', 'Bunati'):
            path = K.SITE_HOME[(e.site, kind)]
        else:
            path = K.DEPT_HOME.get(e.dept, K.A1 + 'Ruangan Attitude')
            if e.field and 'Office' in path:
                path = {'HRGA': K.WS + 'Workshop Light Vehicle' if 'Mekanik' in e.emp.jabatan else K.NO + 'Pos Security Utama',
                        'HSE Operation': K.NO + 'Klinik', 'Logistik & Procurement': K.WS + 'Gudang Logistik',
                        'Infrastructure Project & Maintenance': 'Angsana > Mess',
                        'Environment & Reclamation': K.ENV + 'Nursery'}.get(e.dept, K.PIT + 'Pos Pengawas Pit')
                if 'Housekeeping' in e.emp.jabatan or 'Staff Mess' in e.emp.jabatan:
                    path = 'Angsana > Mess'
                if 'Driver' in e.emp.jabatan:
                    path = K.WS + 'Workshop Light Vehicle'
        e._home = self.loc(path)
        return e._home

    PERSONAL_KIND = {
        'laptop': ('IT-LP', 'Laptop'), 'kursi': ('FRN-KK', 'Kursi Kerja'), 'meja': ('FRN-MD', 'Meja Kerja'),
        'ht': ('KOM-HT', 'Handy Talky'),
    }

    def buy_personal(self, kind, d, e):
        code, cat_name = self.PERSONAL_KIND[kind]
        cat = self.cat(cat_name)
        models = {'laptop': K.LAPTOP_MODELS[e.tier if e else 'standar'], 'kursi': K.CHAIR_MODELS, 'meja': K.DESK_MODELS,
                  'ht': K.HT_MODELS}[kind]
        if kind == 'kursi' and e and not e.lead:
            models = [m for m in models if 'Herman' not in m[0]]
        buy = workday(max(d - timedelta(days=self.rng.randint(3, 12)), START))
        model, base, *_ = self.model_for(models, buy.year)
        dept = self.D[e.dept] if e and e.dept else self.D['HRGA']
        p = Plan(code, cat, model, self.price(base), self.cat_meta[cat.pk]['life'], buy,
                 self.gudang(e.site if e else 'Angsana'), dept)
        p.personal, p.kind, p.tier = True, kind, (e.tier if e and kind == 'laptop' else 'standar')
        p.comp_key = {'laptop': 'laptop', 'kursi': 'kursi', 'ht': 'radio'}.get(kind)
        p.seg(dt(buy + timedelta(days=self.rng.randint(1, 3)), self.rng.randint(9, 15)), p.loc, dept,
              note=f'Penerimaan aset baru', by=self.ga(buy, e.site if e else 'Angsana'))
        p.holder, p.avail, p.retired = None, buy, False
        self.plans.append(p)
        self.stock[kind].append(p)
        return p

    def plan_personal(self):
        rng = self.rng
        self.stock = defaultdict(list)
        q = []
        seq = 0

        def push(d, kind, payload):
            nonlocal seq
            seq += 1
            heapq.heappush(q, (d, seq, kind, payload))

        for e in self.E:
            for kind in self.needs(e):
                start = max(e.join, START + timedelta(days=rng.randint(0, 40)))
                if start < self.today:
                    push(start, 'need', (e, kind))
                if e.leave and e.leave < self.today:
                    push(e.leave, 'leave', (e, kind))
        for y in range(2021, self.today.year + 1):
            d = workday(date(y, 2, 10))
            if d < self.today:
                push(d, 'refresh', y)
        # HT hilang di lapangan
        lost = {date(2023, 9, 12), date(2025, 7, 3)}
        for d in lost:
            if d < self.today:
                push(d, 'lost', None)
        # Stok cadangan untuk karyawan baru (Tersedia di gudang GA)
        spare = {'laptop': 4, 'kursi': 3, 'ht': 5}
        held = {}                                  # (e, kind) → unit

        def give(e, kind, d, note='Fasilitas kerja karyawan baru'):
            pool = [u for u in self.stock[kind] if u.holder is None and not u.retired and u.avail <= d
                    and (kind != 'laptop' or (d - u.buy).days < 365 * 3.5)]
            if kind == 'laptop':
                pool = [u for u in pool if u.tier == e.tier]
            unit = min(pool, key=lambda u: u.avail) if pool else self.buy_personal(kind, d, e)
            if unit.buy > d:
                d = unit.buy + timedelta(days=2)
            t = dt(workday(d, back=False), rng.randint(9, 15), rng.randint(0, 59))
            reuse = len(unit.segs) > 1
            ho = note if not reuse else rng.choice(['Unit eks. karyawan lain, sudah dicek dan dibersihkan',
                                                    'Unit stok gudang GA, kondisi baik', note])
            if kind == 'laptop':
                ho += rng.choice(['', '. Lengkap dengan charger dan tas', '. Sudah diinstal ulang IT dan dienkripsi BitLocker'])
            unit.seg(t, self.home(e), unit.dept, holder=e, note=f'Serah terima ke {e.emp.nama}', by=self.ga(d_of(t), e.site),
                     ho_note=ho)
            unit.holder = e
            unit.use.append([d_of(t), None, e])
            held[(e, kind)] = unit

        def take_back(e, kind, d, why, cond=None, retire=False):
            unit = held.pop((e, kind), None)
            if not unit:
                return None
            cond = cond or rng.choices(['baik', 'rusak_ringan', 'rusak_berat'], [84, 12, 4])[0]
            t = dt(workday(d), rng.randint(13, 16), rng.randint(0, 59))
            if unit.use and unit.use[-1][1] is None:
                unit.use[-1][1] = d_of(t)
            unit.seg(t, self.gudang(e.site), unit.dept, note=f'Dikembalikan oleh {e.emp.nama} ({why})',
                     by=self.ga(d_of(t), e.site), ret=cond)
            unit.holder = None
            unit.avail = d_of(t) + timedelta(days=rng.randint(7, 25))
            if cond != 'baik':
                desc = {'laptop': ['Engsel layar patah dan casing retak.', 'Baterai menggembung, touchpad terangkat.',
                                   'Beberapa tombol keyboard tidak berfungsi.'],
                        'kursi': ['Hidrolik turun sendiri dan roda patah.'], 'meja': ['Laci rusak dan kaki goyang.'],
                        'ht': ['Antena patah dan baterai tidak bisa diisi.', 'Casing retak, tombol PTT keras.']}[kind]
                unit.forced.append(dict(date=d_of(t), cond=cond, desc=rng.choice(desc)))
                unit.avail += timedelta(days=20)
            if retire or (cond == 'rusak_berat' and (d - unit.buy).days > 365 * 3):
                unit.retired = d_of(t)
            return unit

        while q:
            d, _, kind, payload = heapq.heappop(q)
            if kind == 'need':
                e, k = payload
                if (e, k) not in held:
                    give(e, k, d)
            elif kind == 'leave':
                e, k = payload
                take_back(e, k, d, 'resign')
            elif kind == 'refresh':
                for (e, k), unit in list(held.items()):
                    if k == 'laptop' and (d - unit.buy).days > 365 * 4.2 and e.active(d):
                        take_back(e, k, d, 'peremajaan laptop', cond='baik', retire=True)
                        give(e, k, d + timedelta(days=rng.randint(1, 6)), note='Peremajaan laptop (pengganti unit lama)')
            elif kind == 'lost':
                hts = [(e, u) for (e, k), u in held.items() if k == 'ht' and e.field and e.active(d)]
                if hts:
                    e, u = rng.choice(hts)
                    held.pop((e, 'ht'))
                    if u.use and u.use[-1][1] is None:
                        u.use[-1][1] = d
                    u.writeoff = dict(date=workday(d + timedelta(days=rng.randint(10, 30)), back=False), alasan='hilang',
                                      nilai_jual=None, lemon=False,
                                      keterangan=f'HT hilang saat dipakai {e.emp.nama} di area kerja; sudah dicari dan dilaporkan ke Security.',
                                      lost_by=e, lost_at=d)
                    u.retired = d
                    give(e, 'ht', d + timedelta(days=rng.randint(2, 6)), note='Pengganti HT yang hilang')
        # Stok cadangan
        for kind, n in spare.items():
            for _ in range(n):
                d = workday(self.today - timedelta(days=rng.randint(6, 70)))
                self.buy_personal(kind, d, None)
        # Laptop lama yang dipensiunkan → lelang tahunan (dijual) / rusak berat
        for u in self.stock['laptop'] + self.stock['kursi'] + self.stock['ht']:
            if u.retired and not u.writeoff:
                y = u.retired.year if u.retired.month < 7 else u.retired.year + 1
                wo = workday(date(y, 8, 19))
                if wo <= u.retired + timedelta(days=30):
                    wo = workday(date(y + 1, 8, 19))
                if wo < self.today:
                    sold = u.kind == 'laptop' and rng.random() < 0.8
                    u.writeoff = dict(date=wo, alasan='dijual' if sold else 'usang', lemon=False,
                                      nilai_jual=Decimal(rng.randrange(12, 36) * 100_000) if sold else None,
                                      keterangan='Dilelang ke karyawan setelah peremajaan laptop.' if sold else
                                      'Habis umur pakai dan rusak; tidak ekonomis diperbaiki.')
        self.stats['barang pribadi (unit)'] = sum(len(v) for v in self.stock.values())

    # ── 6. Kamar mess & locker ──────────────────────────────────────────
    def plan_rooms_and_lockers(self):
        rng = self.rng
        room_units = defaultdict(list)
        locker_units = defaultdict(list)
        for p in self.plans:
            if 'kamar' in p.opts:
                room_units[p.loc.full_path].append(p)
            elif 'locker' in p.opts:
                locker_units[p.loc.full_path].append(p)
            if 'kamar' in p.opts or 'locker' in p.opts:
                p.personal = True
                p.seg(dt(p.buy + timedelta(days=rng.randint(1, 4)), rng.randint(9, 15)), p.loc, p.dept,
                      note='Penerimaan aset baru', by=self.ga(p.buy, p.loc.site))

        # Kasur lama diganti 2024 (usang) → unit baru di kamar yang sama
        old_kasur = sorted([p for paths in room_units.values() for p in paths if p.cat.name == 'Kasur' and p.buy.year == 2019],
                           key=lambda p: p.buy)[:8]
        repl_day = date(2024, 3, 12)
        for p in old_kasur:
            p.swap_out = repl_day
            p.writeoff = dict(date=workday(date(2024, 5, 21)), alasan='usang', nilai_jual=None, lemon=False,
                              keterangan='Per dan busa sudah rusak setelah 5 tahun dipakai penghuni mess; diganti kasur baru.')
            model, base, *_ = self.model_for(p.line_models, 2024)
            np_ = Plan(p.code, p.cat, model, self.price(base), p.life, workday(repl_day - timedelta(days=6)), p.loc, p.dept, 'kamar')
            np_.personal, np_.line_models, np_.comp_key, np_.swap_in = True, p.line_models, None, repl_day
            np_.seg(dt(repl_day, 10), p.loc, p.dept, note='Penerimaan aset baru (pengganti kasur lama)', by=self.ga(repl_day))
            self.plans.append(np_)
            room_units[p.loc.full_path].append(np_)

        # Penghuni mess: karyawan lapangan & karyawan site Kusan/Bunati
        occupants = []
        for e in self.E:
            if not e.has_user or not e.dept or e.lead and e.site == 'Angsana':
                continue
            if e.site != 'Angsana' or (e.field and rng.random() < 0.75) or (e.dept in K.FIELD_DEPTS and rng.random() < 0.35):
                occupants.append(e)
        occupants.sort(key=lambda e: e.join)
        free_from = {r: date(2019, 1, 1) for r in K.MESS_ROOMS['Angsana'] + K.MESS_ROOMS['Kusan'] + K.MESS_ROOMS['Bunati']}
        if 'Angsana > Mess > Mess 1 > Kamar 1.1' in free_from:
            pass
        stays = []
        for e in occupants:
            start = max(e.join, date(2019, 3, 1))
            site_rooms = K.MESS_ROOMS[e.site] if e.site in K.MESS_ROOMS else K.MESS_ROOMS['Angsana']
            room = next((r for r in site_rooms if free_from[r] <= start), None)
            if not room:
                continue
            end = e.leave
            why = 'resign'
            if not end and rng.random() < 0.18:
                end = start + timedelta(days=rng.randint(400, 1100))
                why = 'pindah ke rumah kontrakan'
                if end >= self.today:
                    end, why = None, 'resign'
            free_from[room] = (end + timedelta(days=rng.randint(3, 20))) if end else date(2100, 1, 1)
            stays.append((room, e, start, end, why))
        self.stats['penghuni mess'] = len(stays)

        def apply(units, place_label, place, e, start, end, why, ho=None):
            for p in units:
                u0 = getattr(p, 'swap_in', p.buy + timedelta(days=5))
                u1 = getattr(p, 'swap_out', None)
                s, t = max(start, u0), min(end or date(2100, 1, 1), u1 or date(2100, 1, 1))
                if s >= t or s >= self.today:
                    continue
                ts = dt(workday(s, back=False), rng.randint(9, 15), rng.randint(0, 59))
                p.seg(ts, p.loc, p.dept, holder=e, note=f'Serah terima {place_label} ke {e.emp.nama}', by=self.ga(s, p.loc.site),
                      ho_note=ho or f'Penghuni {place}')
                p.use.append([d_of(ts), None, e])
                stop = None
                if u1 and (end is None or u1 < end):
                    stop = (u1, 'diganti unit baru')
                elif end and end < self.today:
                    stop = (end, why)
                if stop:
                    te = dt(workday(stop[0]), rng.randint(13, 16), rng.randint(0, 59))
                    loc = self.gudang(p.loc.site) if stop[1] == 'diganti unit baru' else p.loc
                    p.seg(te, loc, p.dept, note=f'{place_label.capitalize()} dikembalikan {e.emp.nama} ({stop[1]})',
                          by=self.ga(stop[0], p.loc.site), ret='baik' if rng.random() < 0.9 else 'rusak_ringan')
                    p.use[-1][1] = d_of(te)
                    if p.segs[-1]['ret'] == 'rusak_ringan':
                        p.forced.append(dict(date=d_of(te), cond='rusak_ringan', desc='Ada kerusakan ringan saat kamar dikosongkan.'))

        for room, e, start, end, why in sorted(stays, key=lambda s: s[2]):
            apply(sorted(room_units[room], key=lambda p: p.buy), 'perabot kamar', room.split(' > ')[-1], e, start, end, why)
        # Kasur lama yang diganti saat kamar kosong tetap ditarik ke gudang
        for p in old_kasur:
            if not p.segs or d_of(p.segs[-1]['t']) < p.swap_out and p.segs[-1]['loc'] == p.loc:
                p.seg(dt(p.swap_out, 14), self.gudang(p.loc.site), p.dept, note='Ditarik ke Gudang GA: diganti kasur baru',
                      by=self.ga(p.swap_out, p.loc.site))

        # Locker pribadi untuk karyawan lapangan di site masing-masing
        free_l = {u.key: u.buy + timedelta(days=5) for units in locker_units.values() for u in units}
        by_site = defaultdict(list)
        for path, units in locker_units.items():
            by_site[self.L[path].site].extend(units)
        for e in sorted([e for e in self.E if e.has_user and e.field], key=lambda e: e.join):
            units = by_site.get(e.site) or by_site['Angsana']
            start = max(e.join, date(2020, 3, 1))
            unit = next((u for u in sorted(units, key=lambda u: u.key) if free_l[u.key] <= start), None)
            if not unit:
                continue
            end = e.leave if e.leave and e.leave < self.today else None
            free_l[unit.key] = (end + timedelta(days=10)) if end else date(2100, 1, 1)
            apply([unit], 'locker', unit.loc.name, e, start, end, 'resign', ho=f'Locker pribadi di {unit.loc.name}')

    # ── 7. Riwayat aset bersama ─────────────────────────────────────────
    def plan_shared_history(self):
        rng = self.rng
        self.mreports = []
        for p in self.plans:
            if p.personal:
                continue
            site = p.loc.site
            t0 = dt(p.buy + timedelta(days=rng.randint(1, 6)), rng.randint(8, 15), rng.randint(0, 59))
            p.seg(t0, p.loc, p.dept, note='Penerimaan aset baru' + (' (pengganti unit lama)' if getattr(p, 'replaces', None) else ''),
                  by=self.ga(d_of(t0), site))
            share = None
            if 'serah' in p.opts:
                t1 = t0 + timedelta(days=rng.randint(0, 6), hours=rng.randint(1, 4))
                share = p.dept
                p.seg(t1, p.loc, p.dept, share=share, note=f'Serah terima ke departemen {p.dept.name}', by=self.ga(d_of(t1), site),
                      ho_note=rng.choice(['Kondisi baru, lengkap dengan manual dan kartu garansi', 'Diterima oleh supervisor departemen',
                                          'Unit baru sesuai PR departemen']))
            end = p.writeoff['date'] if p.writeoff else self.today
            self.relocations(p, share, end)
            if p.writeoff:
                wo = p.writeoff['date']
                last = p.segs[-1]
                small = p.price < 60_000_000 and p.fam not in ('kendaraan',)
                if small and last['loc'] != self.gudang(last['loc'].site) and p.writeoff['alasan'] != 'hilang':
                    tw = dt(workday(wo - timedelta(days=rng.randint(5, 25))), rng.randint(9, 15))
                    if tw > last['t']:
                        p.seg(tw, self.gudang(last['loc'].site), last['owner'], note='Ditarik ke Gudang GA menunggu penghapusan',
                              by=self.ga(d_of(tw), last['loc'].site), ret='rusak_berat' if p.lemon else 'baik')

        # Laporan pindah yang ditolak & yang masih menunggu (tanpa movement)
        movers = [p for p in self.plans if 'gerak' in p.opts and not p.writeoff]
        for p in rng.sample(movers, min(6, len(movers))):
            d = workday(self.today - timedelta(days=rng.randint(60, 900)))
            cur = p.at(d)
            rep = self.dept_staff(cur['owner'], d, cur['loc'].site)
            if not rep or d_of(cur['t']) >= d:
                continue
            other = 'Kusan' if cur['loc'].site == 'Angsana' else 'Angsana'
            to = self.loc(rng.choice(K.PITS.get(other) or K.PITS['Angsana']))
            t = dt(d, rng.randint(8, 16), rng.randint(0, 59))
            self.mreports.append(dict(plan=p, by=rep, frm=cur, to_loc=to, to_dept=cur['owner'], to_holder=None, status='rejected',
                                      reasons='Pindah site', notes='Dibutuhkan untuk pekerjaan malam di site ' + other,
                                      created=t, reviewer=self.ga(d), reviewed=t + timedelta(hours=rng.randint(2, 30)),
                                      review_notes=rng.choice(['Unit masih dibutuhkan di site asal; ajukan peminjaman dari unit cadangan.',
                                                               'Pemindahan antar site harus melalui permintaan resmi ke GA, bukan laporan pindah.',
                                                               'Tidak disetujui: rencana tambang bulan ini masih memakai unit di lokasi sekarang.'])))
        for p in rng.sample(movers, min(3, len(movers))):
            cur = p.segs[-1]
            d = self.today - timedelta(days=rng.randint(0, 4))
            rep = self.dept_staff(cur['owner'], d, cur['loc'].site)
            if not rep or d_of(cur['t']) >= d:
                continue
            other = 'Kusan' if cur['loc'].site == 'Angsana' else 'Angsana'
            to = self.loc(rng.choice(K.PITS.get(other) or K.PITS['Angsana']))
            self.mreports.append(dict(plan=p, by=rep, frm=cur, to_loc=to, to_dept=cur['owner'], to_holder=None, status='pending',
                                      reasons='Pindah site', notes=f'Pinjam pakai untuk area kerja malam di site {other}',
                                      created=dt(d, rng.randint(7, 11), rng.randint(0, 59))))

    def relocations(self, p, share, end):
        """Perpindahan selama umur aset: dilaporkan karyawan departemen pemilik atau dicatat GA."""
        rng = self.rng
        cat = p.cat.name
        last = p.segs[-1]
        t = last['t']

        def move(when, loc, owner=None, new_share=None, note='', by_report=True, holder=None):
            nonlocal last, share
            cur = last
            owner = owner or cur['owner']
            keep_share = share if new_share is None else new_share
            rep = self.dept_staff(cur['owner'], d_of(when), cur['loc'].site) if by_report else None
            crucial = []
            if owner != cur['owner']:
                crucial.append('Pindah departemen')
            if loc.site != cur['loc'].site:
                crucial.append('Pindah site')
            report = None
            if rep and when > dt(date(2021, 1, 4)):
                status = 'approved' if crucial else 'recorded'
                created = when - (timedelta(hours=rng.randint(4, 40)) if crucial else timedelta(minutes=rng.randint(5, 50)))
                report = dict(plan=p, by=rep, frm=cur, to_loc=loc, to_dept=owner, to_holder=holder, status=status,
                              reasons=' · '.join(crucial), notes=note, created=created,
                              reviewer=self.ga(d_of(when), cur['loc'].site) if crucial else None,
                              reviewed=when if crucial else None,
                              review_notes=rng.choice(['', 'OK, sudah dikonfirmasi ke supervisor.', 'Disetujui sesuai rencana kerja.']) if crucial else '')
                self.mreports.append(report)
                by = report['reviewer'] if crucial else rep.emp
                mnote = f'Dilaporkan {rep.emp.nama}' + (f': {note}' if note else '')
            else:
                by = self.ga(d_of(when), cur['loc'].site)
                mnote = note
            s = p.seg(when, loc, owner, holder=holder, share=keep_share, note=mnote, by=by, report=report)
            if report:
                report['seg'] = s
            last = s
            share = keep_share
            return s

        if 'gerak' in p.opts:
            pool = (K.SUMPS if cat == 'Pompa Dewatering' else K.PITS)
            while True:
                t = t + timedelta(days=rng.randint(35, 160), hours=rng.randint(0, 6))
                if d_of(t) >= end - timedelta(days=2) or d_of(t) >= self.today:
                    break
                site = last['loc'].site
                if rng.random() < 0.08:
                    site = 'Kusan' if site == 'Angsana' else 'Angsana'
                options = [x for x in pool.get(site, pool['Angsana']) if x != last['loc'].full_path] or \
                          [x for x in pool['Angsana'] if x != last['loc'].full_path]
                loc = self.loc(rng.choice(options))
                why = rng.choice(['Mengikuti kemajuan front penambangan', 'Dipindah ke area kerja shift malam', 'Penerangan area loading',
                                  'Dipindah karena area lama sudah selesai ditambang', 'Permintaan pengawas pit'])
                if cat == 'Pompa Dewatering':
                    why = rng.choice(['Level air sump naik setelah hujan', 'Dipindah ke sump baru', 'Penanganan genangan di front kerja'])
                move(t, loc, note=why)
        elif cat == 'Light Vehicle' and rng.random() < 0.3:
            t = t + timedelta(days=rng.randint(200, 900))
            if d_of(t) < end - timedelta(days=10) and d_of(t) < self.today:
                new = self.D[rng.choice(['Operasional Tambang', 'HSE Operation', 'Coal Rom & Hauling Dept', 'Survey, Geology & Exploration',
                                         'Environment & Reclamation', 'Infrastructure Project & Maintenance'])]
                if new != last['owner']:
                    home = self.loc(K.DEPT_HOME.get(new.name, K.PIT + 'Pos Pengawas Pit'))
                    if 'Office' in home.full_path and new.name != 'Infrastructure Project & Maintenance':
                        home = self.loc(K.PIT + 'Pos Pengawas Pit')
                    move(t, home, owner=new, new_share=new, by_report=False,
                         note=f'Serah terima ke departemen {new.name} (realokasi armada)')
        elif cat in ('TV / Monitor Display', 'Proyektor', 'Kursi Meeting', 'Whiteboard', 'Printer', 'Speaker / Audio System',
                     'Dispenser', 'Monitor', 'GNSS RTK', 'Total Station', 'Genset') and rng.random() < 0.22 and 'label:GS' not in p.opts:
            t = t + timedelta(days=rng.randint(120, 800))
            if d_of(t) < end - timedelta(days=10) and d_of(t) < self.today:
                if cat in ('GNSS RTK', 'Total Station'):
                    to = self.loc(K.KT + 'Pos Pengawas Kusan') if last['loc'].site == 'Angsana' else self.loc(K.A2 + 'Ruang Planning')
                    move(t, to, note='Dipakai untuk pengukuran progres tambang dan stockpile')
                else:
                    same = [x for x in K.OFFICE_ROOMS + K.SITE_ROOMS if self.L[x].site == last['loc'].site and x != last['loc'].full_path]
                    move(t, self.loc(rng.choice(same)), by_report=rng.random() < 0.6,
                         note=rng.choice(['Ruangan lama direnovasi', 'Dipindah sesuai kebutuhan ruang meeting', 'Penataan ulang ruangan',
                                          'Pindah ke ruangan yang lebih sering dipakai']))
        elif cat == 'AC' and rng.random() < 0.06 and last['loc'].site == 'Angsana':
            t = dt(workday(date(2023, 7, rng.randint(3, 28))), 10)
            if t > last['t'] and d_of(t) < end - timedelta(days=10):
                same = [x for x in K.OFFICE_ROOMS if x != last['loc'].full_path]
                move(t, self.loc(rng.choice(same)), by_report=False, note='Relokasi AC karena renovasi ruangan')

    # ── 8. Nomor aset, PO & penyimpanan aset ────────────────────────────
    def number_assets(self):
        plans = sorted(self.plans, key=lambda p: (p.buy, p.code, p.key))
        po_keys = {}
        po_seq = 4500010000
        per_code = defaultdict(int)
        for p in plans:
            if p.protected:
                continue
            group = (p.code, p.buy.year, p.buy.month // 2 if p.personal else p.buy.isocalendar()[1] // 3)
            if group not in po_keys:
                po_seq += self.rng.randint(3, 19)
                po_keys[group] = str(po_seq)
            p.po = po_keys[group]
            per_code[p.code] += 1
            p.number = f'{p.code}-{per_code[p.code]:03d}'
        self.plans = plans

    def save_assets(self):
        rng = self.rng
        keep_ids = {a.pk for a in self.keep.values()}
        next_id = 1
        new_assets = []
        for p in self.plans:
            last = p.segs[-1]
            if p.protected:
                a = p.protected
                a.lokasi_asset, a.asset_category, a.owned_by_department = last['loc'], p.cat, last['owner']
                a.lifecycle_status = 'active'
                a.save()
                p.asset = a
                continue
            while next_id in keep_ids:
                next_id += 1
            created = dt(p.buy + timedelta(days=rng.randint(1, 4)), rng.randint(9, 16), rng.randint(0, 59))
            a = Asset(id=next_id, no_po=p.po, asset_number=p.number, nama_asset=p.name, tanggal_pembelian=p.buy,
                      lokasi_asset=last['loc'], owned_by_department=last['owner'], asset_category=p.cat,
                      lifecycle_status='active', created_by=self.ga(p.buy, p.loc.site), created_at=created)
            next_id += 1
            new_assets.append(a)
            p.asset = a
        Asset.objects.bulk_create(new_assets, batch_size=500)
        self.stats['aset'] = len(self.plans)

        fins, comps, configs = [], [], []
        for p in self.plans:
            a = p.asset
            if getattr(p, 'recent', False):
                continue    # baru diterima, belum dinilai Finance
            residual = Decimal(int(p.price * rng.choice([0.05, 0.1]) / 1000) * 1000)
            fin_by = self.pick(self.fin_staff, p.buy) or self.fin_staff[0]
            fins.append(AssetFinancial(asset=a, nilai_awal_aset=Decimal(p.price), nilai_residual=residual, umur_ekonomis=p.life,
                                       created_by=fin_by.emp, created_at=dt(p.buy + timedelta(days=rng.randint(3, 14)), rng.randint(9, 16))))
        AssetFinancial.objects.bulk_create(fins, batch_size=500)
        self.fin_of = {f.asset_id: f for f in fins}
        self.stats['aset dinilai Finance'] = len(fins)

        self.comp_of = defaultdict(dict)
        for p in self.plans:
            tmpl = K.COMPONENTS.get(getattr(p, 'comp_key', None) or '')
            if not tmpl:
                continue
            unit = ''.join(ch for ch in p.name.split()[1 if len(p.name.split()) > 1 else 0].upper() if ch.isalnum())[:6] or 'STD'
            for key, nm, pn, qty, crit, sup, price, every in tmpl:
                c = AssetComponent(asset=p.asset, part_number=pn.format(u=unit), component_name=nm, quantity=qty, is_critical=crit,
                                   supplier_name=sup, estimated_price=Decimal(price), last_replaced=None,
                                   created_by=self.ga(p.buy), created_at=dt(p.buy + timedelta(days=rng.randint(5, 20)), 10),
                                   updated_at=dt(p.buy + timedelta(days=rng.randint(5, 20)), 10))
                c.key, c.every = key, every
                comps.append(c)
                self.comp_of[p.key][key] = c
        AssetComponent.objects.bulk_create(comps, batch_size=500)
        self.stats['komponen (BOM)'] = len(comps)

        custom = {
            ('Genset', 'Bunati'): (14, 'Genset port beroperasi hampir 24 jam saat suplai listrik tidak stabil; servis tiap ±250 jam operasi.'),
            ('AC', 'Ruang Server'): (30, 'AC ruang server menyala 24 jam; filter cepat kotor oleh debu batubara.'),
            ('Lighting Tower', 'Jetty'): (21, 'Operasi malam penuh di jetty; servis tiap ±250 jam operasi.'),
            ('Dispenser', 'Klinik'): (30, 'Standar higienitas klinik perusahaan.'),
            ('Crusher', 'Crushing Plant'): (21, 'Throughput naik sejak penambahan kapasitas port 2024; inspeksi roll lebih sering.'),
        }
        for p in self.plans:
            for (cat, where), (days, why) in custom.items():
                if p.cat.name == cat and where in p.loc.full_path and not p.writeoff:
                    configs.append(AssetMaintenanceConfig(asset=p.asset, custom_interval_days=days, reason=why,
                                                          set_by=self.ga(p.buy), created_at=dt(p.buy + timedelta(days=20), 10)))
                    p.custom_days = days
        AssetMaintenanceConfig.objects.bulk_create(configs)
        self.stats['interval khusus'] = len(configs)

    # ── 9. Maintenance & laporan kerusakan ──────────────────────────────
    def interval_days(self, p):
        if getattr(p, 'custom_days', None):
            return p.custom_days
        return p.cat.default_interval_in_days()

    def tech(self, p, d):
        team, vendor_p, vendors = K.TECH_TEAM.get(p.fam, ('ga', 0.3, ['CV Kalsel Teknik']))
        dept, kws = K.TECH_PEOPLE[team]
        site = p.at(d)['loc'].site
        e = self.dept_staff(self.D[dept], d, site, kws, need_user=False)
        who = e.emp if e else self.ga(d, site)
        vendor = self.rng.choice(vendors) if self.rng.random() < vendor_p else None
        return who, vendor

    def reporter(self, p, d):
        s = p.at(d)
        if s['holder'] and s['holder'].active(d):
            return s['holder'].emp
        dept = s['share'] or s['owner']
        e = self.dept_staff(dept, d, s['loc'].site)
        if e and self.rng.random() < 0.85:
            return e.emp
        e = self.pick([x for x in self.E if x.has_user], d, s['loc'].site)
        return e.emp if e and self.rng.random() < 0.5 else self.ga(d, s['loc'].site)

    def repair_cap(self, p):
        fin = self.fin_of.get(p.asset.pk)
        value = float(fin.nilai_awal_aset) if fin else float(p.price)
        th = get_repair_threshold(value)
        if p.lemon:
            return value * th['critical'] / 100 * self.rng.uniform(1.05, 1.4)
        if self.rng.random() < 0.12:
            return value * self.rng.uniform(th['warning'], th['waspada'] - 1) / 100
        return value * th['warning'] / 100 * self.rng.uniform(0.25, 0.85)

    def plan_maintenance(self):
        rng = self.rng
        self.reports, self.records, self.schedules, self.replacements, self.findings = [], [], [], [], []
        overdue_pool = set()
        for p in self.plans:
            if not p.writeoff and rng.random() < 0.06:
                overdue_pool.add(p.key)
        for p in self.plans:
            end = p.end or self.today
            start = p.buy + timedelta(days=5)
            reps = self.damage_dates(p, start, end) if end > start else []
            self.chain(p, start, end, reps, p.key in overdue_pool)
        self.stats['laporan kerusakan'] = len(self.reports)
        self.stats['catatan maintenance'] = len(self.records)
        self.stats['jadwal maintenance'] = len(self.schedules)
        self.stats['penggantian komponen'] = len(self.replacements)

    def damage_dates(self, p, start, end):
        """Tanggal laporan kerusakan selama aset dipakai (barang pribadi: saat dipegang karyawan)."""
        rng = self.rng
        rate = K.DAMAGE_RATE.get(p.fam, 0.15) * (3.2 if p.lemon else 1.0) * rng.uniform(0.6, 1.4)
        windows = [(max(a, start), min(b or end, end)) for a, b, _ in p.use] if p.personal else [(start, end)]
        out = []
        for a, b in windows:
            d = a
            while True:
                d = d + timedelta(days=max(25, int(rng.expovariate(rate / 365.0))))
                if d >= b or d >= self.today:
                    break
                out.append(dict(date=workday(d), kind='random'))
        for f in p.forced:
            out = [r for r in out if abs((r['date'] - f['date']).days) > 30]
            out.append(dict(date=f['date'], kind='return', cond=f['cond'], desc=f['desc']))
        if p.lemon and p.final_report:
            out = [r for r in out if r['date'] < p.final_report - timedelta(days=30)]
            out.append(dict(date=p.final_report, kind='final'))
        if p.writeoff and p.writeoff['alasan'] == 'hilang':
            out = [r for r in out if r['date'] < p.writeoff['date'] - timedelta(days=20)]
        out.sort(key=lambda r: r['date'])
        # jaga jarak minimum antar laporan
        clean = []
        for r in out:
            if clean and (r['date'] - clean[-1]['date']).days < 25 and r['kind'] == 'random':
                continue
            clean.append(r)
        return clean

    def chain(self, p, start, end, reps, overdue):
        """Susun preventive/inspeksi, laporan kerusakan & corrective secara kronologis, seperti alur aplikasi."""
        rng = self.rng
        interval = self.interval_days(p)
        meta = self.cat_meta[p.cat.pk]
        cap = self.repair_cap(p)
        spent = 0.0
        last_tmpl = None
        base = p.buy
        pending_due = base + timedelta(days=interval) if interval else None
        i = 0
        active_report = None
        comps = self.comp_of.get(p.key, {})
        periodic = {k: c for k, c in comps.items() if c.every}
        next_periodic = {k: p.buy + timedelta(days=int(365 * c.every * rng.uniform(0.85, 1.15))) for k, c in periodic.items()}

        def add_finding(d, sumber, text, report=None, record=None, rekom='perbaiki'):
            self.findings.append(dict(date=d, plan=p, dept=p.at(d)['owner'], sumber=sumber, text=text, report=report,
                                      record=record, rekom=rekom))

        while True:
            nxt_rep = reps[i] if i < len(reps) else None
            # preventive berikutnya sebelum laporan berikutnya?
            if interval and pending_due and (not nxt_rep or pending_due < nxt_rep['date']):
                due = pending_due
                if due >= end or due > self.today:
                    break
                if overdue and (self.today - due).days < 70 and due < self.today and not p.writeoff:
                    self.schedules.append(dict(plan=p, date=due, status='overdue'))
                    pending_due = None
                    break
                if rng.random() < 0.025:
                    self.schedules.append(dict(plan=p, date=due, status='skipped',
                                               notes=rng.choice(['Dilewati: unit sedang dipakai operasi penuh', 'Dilewati: teknisi vendor berhalangan'])))
                    base = due
                    pending_due = base + timedelta(days=interval)
                    continue
                done = min(due + timedelta(days=rng.randint(-3, 10)), end, self.today)
                if done < base:
                    done = due
                rec = self.pm_record(p, done, meta, comps, periodic, next_periodic)
                self.schedules.append(dict(plan=p, date=due, status='done', record=rec))
                if rec['hasil'] in ('minor_issue', 'major_issue'):
                    jenis = 'inspeksi' if rec['type'] == 'inspection' else 'maintenance preventive'
                    add_finding(done, 'inspeksi', f"{HASIL_LABEL[rec['hasil']]} saat {jenis}: {rec['temuan']}", record=rec)
                base = done
                pending_due = base + timedelta(days=interval)
                continue
            if not nxt_rep:
                break
            i += 1
            d = nxt_rep['date']
            if d >= end and nxt_rep['kind'] != 'final' or d > self.today:
                continue
            # Laporan kerusakan
            tmpl = self.pick_template(p, last_tmpl, nxt_rep)
            last_tmpl = tmpl[0]
            title, desc, sev, (lo, hi), part, fix = tmpl
            if nxt_rep['kind'] == 'return':
                title = 'Rusak saat dikembalikan'
                desc = nxt_rep['desc']
                sev = 'low' if nxt_rep['cond'] == 'rusak_ringan' else 'high'
                by = self.ga(d, p.at(d)['loc'].site)
            else:
                by = self.reporter(p, d)
            t = dt(d, rng.randint(7, 17), rng.randint(0, 59))
            rep = dict(plan=p, title=title, desc=desc, sev=sev, by=by, at=t, status='resolved', part=part, fix=fix)
            self.reports.append(rep)
            # preventive yang sedang menunggu ikut dilewati (aplikasi: upcoming → skipped)
            if interval and pending_due:
                self.schedules.append(dict(plan=p, date=pending_due, status='skipped', notes=''))
                pending_due = None
            final_lemon = nxt_rep['kind'] == 'final'
            reject = (not final_lemon and nxt_rep['kind'] == 'random' and rng.random() < 0.055)
            handler = self.ga(d, p.at(d)['loc'].site)
            rep['handler'] = handler
            if reject:
                rep['status'] = 'rejected'
                rep['ga_notes'] = rng.choice(K.REJECT_NOTES)
                rep['updated'] = t + timedelta(hours=rng.randint(3, 40))
                if interval and pending_due is None:
                    pending_due = base + timedelta(days=interval)
                    if pending_due <= d:
                        pending_due = d + timedelta(days=interval)
                continue
            add_finding(d, 'laporan', ringkas_laporan(title, desc), report=rep, rekom='hapus' if final_lemon else 'perbaiki')
            if final_lemon:
                rep['status'] = 'in_progress' if (self.today - d).days > 6 else 'acknowledged'
                rep['ga_notes'] = 'Biaya perbaikan tidak ekonomis; diusulkan penghapusan melalui Berita Acara Kerusakan.'
                if p.writeoff:
                    wo = p.writeoff['date']
                    rep.update(status='resolved', resolved=dt(wo, 15), ga_notes='Ditutup karena aset di-write off.', record=None)
                    self.schedules.append(dict(plan=p, date=d, status='skipped', report=rep,
                                               notes=self.corr_note(rep)))
                else:
                    self.schedules.append(dict(plan=p, date=d, status='overdue', report=rep, notes=self.corr_note(rep)))
                    active_report = rep
                pending_due = None
                break
            dur = {'kendaraan': (1, 12), 'alat_berat': (2, 18), 'conveyor': (1, 6), 'crusher': (2, 12), 'lab': (5, 28),
                   'survey': (7, 30), 'drone': (7, 30), 'server': (1, 5), 'fotokopi': (2, 10)}.get(p.fam, (1, 10))
            res_day = d + timedelta(days=rng.randint(*dur))
            nxt_d = reps[i]['date'] if i < len(reps) else None
            if nxt_d and res_day >= nxt_d:
                res_day = nxt_d - timedelta(days=2)
            if p.writeoff and res_day >= end:
                rep.update(status='resolved', resolved=dt(end, 15), ga_notes='Ditutup karena aset di-write off.', record=None)
                self.schedules.append(dict(plan=p, date=d, status='skipped', report=rep, notes=self.corr_note(rep)))
                pending_due = None
                break
            if res_day >= self.today or res_day >= end:
                age = (self.today - d).days
                rep['status'] = 'pending' if age <= 2 else 'acknowledged' if age <= 6 else 'in_progress'
                rep['ga_notes'] = '' if rep['status'] == 'pending' else rng.choice(K.GA_NOTES)
                self.schedules.append(dict(plan=p, date=d, status='overdue', report=rep, notes=self.corr_note(rep)))
                active_report = rep
                pending_due = None
                break
            cost = rng.uniform(lo, hi)
            if p.lemon:
                cost *= rng.uniform(1.4, 2.4)
                room = max(cap - spent, cost)
            else:
                room = cap - spent
            if cost > room:
                cost = max(room, lo * 0.35)
            if not p.lemon and spent + cost > cap * 1.08:
                cost = max(0.0, cap - spent) if cap - spent > 50_000 else rng.uniform(50_000, 150_000)
            cost = int(cost / 5000) * 5000
            spent += cost
            who, vendor = self.tech(p, res_day)
            replaced = part in comps if part else False
            rec = dict(plan=p, type='corrective', date=res_day, hasil='replaced_component' if replaced else 'ok', cost=cost,
                       by=who, vendor=vendor, report=rep, text=fix, temuan='')
            self.records.append(rec)
            if replaced:
                c = comps[part]
                self.replacements.append(dict(comp=c, date=res_day, reason=title, cost=min(cost, int(c.estimated_price * c.quantity)),
                                              by=who, old=c.part_number))
                c.last_replaced = res_day
            rep.update(record=rec, resolved=dt(res_day, rng.randint(13, 17), rng.randint(0, 59)),
                       ga_notes=rng.choice(['Sudah diperbaiki, unit normal kembali.', 'Perbaikan selesai dan sudah dicoba bersama pelapor.',
                                            f'Selesai. {fix}.', 'Unit sudah berfungsi normal.']))
            self.schedules.append(dict(plan=p, date=d, status='done', report=rep, record=rec, notes=self.corr_note(rep)))
            base = res_day
            if interval:
                pending_due = base + timedelta(days=interval)
        # Jadwal aktif terakhir
        if p.writeoff:
            if interval and pending_due and pending_due <= p.writeoff['date'] + timedelta(days=interval):
                self.schedules.append(dict(plan=p, date=pending_due, status='skipped',
                                           notes=f"Dihentikan: aset di-write off {p.writeoff['date']:%d %b %Y}"))
            return
        if interval and pending_due:
            status = 'upcoming' if pending_due >= self.today else 'overdue'
            self.schedules.append(dict(plan=p, date=pending_due, status='skipped' if active_report else status, notes=''))
        p.active_report = active_report

    def corr_note(self, rep):
        return '__CORR__'

    def pick_template(self, p, last, r):
        pool = K.CATEGORY_DAMAGE.get(p.cat.name) if p.fam not in ('kulkas', 'gym', 'meja') else None
        pool = pool or K.DAMAGE.get(p.fam) or K.DAMAGE['office']
        if r['kind'] == 'final':
            heavy = [t for t in pool if t[2] in ('high', 'critical')] or pool
            return self.rng.choice(heavy)
        fresh = [t for t in pool if t[0] != last] or pool
        return self.rng.choice(fresh)

    def pm_record(self, p, d, meta, comps, periodic, next_periodic):
        rng = self.rng
        typ = 'inspection' if meta['kind'] == 'insp' else 'preventive'
        hasil = rng.choices(['ok', 'minor_issue', 'major_issue'], [91, 8, 1])[0]
        lo, hi = K.PM_COST.get(p.fam, (100_000, 400_000))
        cost = int(rng.uniform(lo, hi) / 5000) * 5000
        who, vendor = self.tech(p, d)
        temuan = ''
        if hasil != 'ok':
            temuan = rng.choice(['ditemukan keausan ringan, penggantian part dijadwalkan', 'ada rembesan kecil pada sambungan, sudah dikencangkan',
                                 'kabel sedikit terkelupas, sudah diisolasi sementara', 'baut dudukan kendur, sudah dikencangkan',
                                 'performa menurun dibanding servis sebelumnya, perlu dipantau', 'karat pada rangka, perlu dicat ulang'])
            if p.fam == 'apar':
                temuan = rng.choice(['tekanan mendekati batas bawah, dijadwalkan refill', 'label dan segel pudar, perlu diganti'])
        due_parts = [k for k, nd in next_periodic.items() if nd <= d]
        if due_parts and hasil == 'ok':
            hasil = 'replaced_component'
            for k in due_parts:
                c = periodic[k]
                part_cost = int(c.estimated_price * c.quantity)
                cost += part_cost
                self.replacements.append(dict(comp=c, date=d, reason='Penggantian berkala sesuai umur pakai', cost=part_cost,
                                              by=who, old=c.part_number))
                c.last_replaced = d
                next_periodic[k] = d + timedelta(days=int(365 * c.every * rng.uniform(0.9, 1.1)))
        for k in ('oil_filter', 'fuel_filter', 'air_filter'):
            if k in comps and typ == 'preventive':
                comps[k].last_replaced = d
        rec = dict(plan=p, type=typ, date=d, hasil=hasil, cost=cost, by=who, vendor=vendor, report=None,
                   text=('Inspeksi berkala: ' if typ == 'inspection' else 'Preventive maintenance rutin: ') +
                   (', '.join(l[0].lower() + l[1:] for l in meta['checklist']) or 'pemeriksaan umum dan pembersihan'),
                   temuan=temuan)
        self.records.append(rec)
        return rec

    # ── 10. Simpan movement, serah terima & laporan pindah ──────────────
    def save_history(self):
        rng = self.rng
        moves, assigns = [], []
        for p in self.plans:
            segs = sorted(p.segs, key=lambda s: s['t'])
            if p.writeoff:
                last = segs[-1]
                wt = dt(p.writeoff['date'], 15, rng.randint(0, 59))
                if wt <= last['t']:
                    wt = last['t'] + timedelta(hours=2)
                segs.append(dict(t=wt, loc=last['loc'], owner=last['owner'], holder=None, share=None,
                                 note=f"Write off: {WRITE_OFF_LABEL[p.writeoff['alasan']]}", by=self.ga(p.writeoff['date'], last['loc'].site),
                                 ret='', report=None, ho_note='', wo=True))
            p.segs = segs
            cur = None
            for i, s in enumerate(segs):
                nxt = segs[i + 1]['t'] if i + 1 < len(segs) else None
                holder = s['holder'].emp if s['holder'] else None
                dept = (s['holder'].emp.department if s['holder'] else None) or s['share'] or s['owner']
                m = AssetMovement(asset=p.asset, held_by=holder, department=dept, location=s['loc'], moved_in=s['t'],
                                  moved_out=nxt, is_current=nxt is None, notes=s['note'], recorded_by=s['by'], created_at=s['t'])
                s['mv'] = m
                moves.append(m)
                # serah terima mengikuti penerima pada segmen ini
                want = ('e', holder.pk) if holder else ('d', s['share'].pk) if s['share'] else None
                have = (('e', cur.assigned_to_id) if cur.assigned_to_id else ('d', cur.department_id)) if cur else None
                if want == have:
                    continue
                d = d_of(s['t'])
                if cur:
                    cur.status = 'transferred' if want else 'returned'
                    cur.returned_date = d
                    if not want:
                        cur.return_condition = s['ret'] or 'baik'
                        if s.get('wo'):
                            cur.return_condition = ''
                        elif rng.random() < 0.3 and s['ret'] == 'baik':
                            cur.notes = f"{cur.notes}\n[Dikembalikan: {rng.choice(['Lengkap', 'Kondisi baik dan bersih', 'Lengkap dengan aksesoris'])}]".strip()
                    cur = None
                if want:
                    cur = AssetAssignment(asset=p.asset, assigned_to=holder, department=None if holder else s['share'],
                                          assigned_by=s['by'], assigned_date=d, status='active', notes=s['ho_note'],
                                          created_at=s['t'])
                    assigns.append(cur)
        AssetMovement.objects.bulk_create(moves, batch_size=500)
        AssetAssignment.objects.bulk_create(assigns, batch_size=500)
        self.stats['movement'] = len(moves)
        self.stats['serah terima'] = len(assigns)
        self.stats['serah terima ke departemen'] = sum(1 for a in assigns if a.department_id)

        reports = []
        for r in self.mreports:
            p, frm = r['plan'], r['frm']
            seg = r.get('seg')
            mr = MovementReport(asset=p.asset, reported_by=r['by'].emp, from_location=frm['loc'], from_department=frm['owner'],
                                from_holder=frm['holder'].emp if frm['holder'] else None, to_location=r['to_loc'],
                                to_department=r['to_dept'], to_holder=r['to_holder'].emp if r['to_holder'] else None,
                                notes=r['notes'], reasons=r['reasons'], status=r['status'],
                                reviewed_by=r.get('reviewer'), reviewed_at=r.get('reviewed'), review_notes=r.get('review_notes', ''),
                                movement=seg['mv'] if seg else None, created_at=r['created'])
            reports.append(mr)
        MovementReport.objects.bulk_create(reports, batch_size=500)
        self.stats['laporan pindah karyawan'] = len(reports)

    # ── 11. Simpan laporan kerusakan, maintenance, jadwal ───────────────
    def save_maintenance(self):
        rng = self.rng
        objs = []
        for r in self.reports:
            dr = DamageReport(asset=r['plan'].asset, reported_by=r['by'], title=r['title'], description=r['desc'], severity=r['sev'],
                              report_status=r['status'], handled_by=r.get('handler') if r['status'] != 'pending' else None,
                              ga_notes=r.get('ga_notes', ''), reported_at=r['at'],
                              resolved_at=r.get('resolved') if r['status'] == 'resolved' else None,
                              updated_at=r.get('resolved') or r.get('updated') or r['at'] + timedelta(hours=rng.randint(1, 30)))
            r['obj'] = dr
            objs.append(dr)
        DamageReport.objects.bulk_create(objs, batch_size=500)

        recs = []
        for rec in self.records:
            p = rec['plan']
            desc = rec['text']
            if rec['report']:
                rep = rec['report']
                desc = (f"[Dari Laporan Kerusakan #{rep['obj'].pk}]\nJudul: {rep['title']}\nDeskripsi awal: {rep['desc']}\n\n"
                        f"Tindakan perbaikan: {rec['text']}")
            note = f"Dikerjakan vendor: {rec['vendor']}. " if rec['vendor'] else ''
            if rec['temuan']:
                note += rec['temuan'][0].upper() + rec['temuan'][1:] + '.'
            mr = MaintenanceRecord(asset=p.asset, maintenance_type=rec['type'], tanggal_pelaksanaan=rec['date'],
                                   deskripsi_pekerjaan=desc, hasil=rec['hasil'], biaya=Decimal(rec['cost']),
                                   catatan_tambahan=note.strip(), performed_by=rec['by'],
                                   created_at=dt(rec['date'], rng.randint(14, 17), rng.randint(0, 59)))
            rec['obj'] = mr
            recs.append(mr)
        MaintenanceRecord.objects.bulk_create(recs, batch_size=500)
        for r in self.reports:
            if r.get('record'):
                r['obj'].resolved_maintenance = r['record']['obj']
        DamageReport.objects.bulk_update([r['obj'] for r in self.reports if r.get('record')], ['resolved_maintenance'], batch_size=500)

        sch = []
        for s in self.schedules:
            rep = s.get('report')
            notes = s.get('notes', '')
            if notes == '__CORR__':
                notes = (f"[CORRECTIVE - Laporan Kerusakan #{rep['obj'].pk}] {rep['title']} "
                         f"(Dilaporkan oleh: {rep['by'].nama}, Severity: {SEVERITY_LABEL[rep['sev']]})")
            created = dt(rep['at'].astimezone(TZ).date(), 12) if rep else dt(s['date'] - timedelta(days=self.interval_days(s['plan']) or 30), 16)
            sch.append(MaintenanceSchedule(asset=s['plan'].asset, scheduled_date=s['date'], status=s['status'],
                                           maintenance_record=s['record']['obj'] if s.get('record') else None,
                                           damage_report=rep['obj'] if rep else None, notes=notes, created_at=created))
        MaintenanceSchedule.objects.bulk_create(sch, batch_size=500)

        reps = []
        for r in self.replacements:
            reps.append(ComponentReplacement(component=r['comp'], replaced_date=r['date'], reason=r['reason'], cost=Decimal(r['cost']),
                                             old_part_number=r['old'], performed_by=r['by'], created_at=dt(r['date'], 16)))
        ComponentReplacement.objects.bulk_create(reps, batch_size=500)
        comps = [c for d in self.comp_of.values() for c in d.values() if c.last_replaced]
        for c in comps:
            c.updated_at = dt(c.last_replaced, 16)
        AssetComponent.objects.bulk_update(comps, ['last_replaced', 'updated_at'], batch_size=500)

    # ── 12. Write off & Berita Acara ────────────────────────────────────
    def save_writeoffs_and_ba(self):
        rng = self.rng
        signers = salinan_penanda_tangan()
        ga_sign = self.ga_staff

        # BA Kerusakan per departemen: Juni & Desember, plus satu BA HRGA terbit Oktober tahun ini
        by_dept = defaultdict(list)
        for f in self.findings:
            if f['report'] and f['report']['status'] == 'rejected':
                continue
            by_dept[f['dept'].pk].append(f)
        bas = []        # (ba, [findings])
        drafts = []
        extra = workday(self.today - timedelta(days=2))
        for dept_id, fs in by_dept.items():
            fs.sort(key=lambda f: f['date'])
            dept = next(f['dept'] for f in fs)
            cal = [b for y in range(2020, self.today.year + 1) for b in self.ba_calendar(y)]
            if dept.name == 'HRGA':
                cal.append(extra)
            prev = date(2000, 1, 1)
            for b in cal:
                items = [f for f in fs if prev < f['date'] <= b - timedelta(days=4)]
                if not items:
                    continue
                # BA diterbitkan bila temuan cukup banyak, ada yang lama tertunda, atau ada rekomendasi hapus
                enough = len(items) >= 6 or (b.month == 12 and len(items) >= 3) or (b - items[0]['date']).days > 330 \
                    or any(f['rekom'] == 'hapus' for f in items) or b == extra
                if not enough:
                    continue
                prev = b - timedelta(days=4)
                signed = b + timedelta(days=rng.randint(4, 9))
                status = 'ditandatangani' if signed < self.today and b != extra else 'terbit'
                ba = BeritaAcara(jenis='kerusakan', status=status, department=dept, tanggal=b,
                                 dibuat_oleh=self.ga(items[0]['date']), diterbitkan_oleh=self.ga(b),
                                 diterbitkan_at=dt(b, 10), ditandatangani_at=dt(signed, 14) if status == 'ditandatangani' else None,
                                 penanda_tangan=signers, created_at=dt(items[0]['date'], 12), updated_at=dt(signed if status == 'ditandatangani' else b, 14))
                bas.append((ba, items))
            rest = [f for f in fs if f['date'] > prev]
            if rest:
                ba = BeritaAcara(jenis='kerusakan', status='draft', department=dept, dibuat_oleh=self.ga(rest[0]['date']),
                                 created_at=dt(rest[0]['date'], 12), updated_at=dt(rest[-1]['date'], 12))
                drafts.append((ba, rest))

        # Write off → BA Penghapusan per (tanggal, alasan, BA rujukan)
        wo_plans = [p for p in self.plans if p.writeoff]
        lemon_ba = {}
        for ba, items in bas:
            for f in items:
                if f['rekom'] == 'hapus':
                    lemon_ba[f['plan'].key] = (ba, f)
        groups = defaultdict(list)
        for p in wo_plans:
            ref = lemon_ba.get(p.key, (None, None))[0] if p.writeoff.get('lemon') else None
            groups[(p.writeoff['date'], p.writeoff['alasan'], id(ref) if ref else None)].append(p)
        hapus = []
        for (d, alasan, _), plans in sorted(groups.items(), key=lambda g: g[0][0]):
            ref = lemon_ba.get(plans[0].key, (None, None))[0] if plans[0].writeoff.get('lemon') else None
            depts = {p.segs[-1]['owner'].pk for p in plans}
            status = 'ditandatangani' if d + timedelta(days=8) < self.today else 'terbit'
            signed = d + timedelta(days=rng.randint(3, 7))
            ba = BeritaAcara(jenis='penghapusan', status=status, department=plans[0].segs[-1]['owner'] if len(depts) == 1 else None,
                             tanggal=d, rujukan=ref, dibuat_oleh=self.ga(d), diterbitkan_oleh=self.ga(d), diterbitkan_at=dt(d, 15),
                             ditandatangani_at=dt(signed, 14) if status == 'ditandatangani' else None, penanda_tangan=signers,
                             created_at=dt(d, 14), updated_at=dt(signed, 14))
            ba.ref = ref
            hapus.append((ba, plans))

        # Nomor urut tahunan, dipakai bersama kedua jenis BA
        issued = sorted([b for b, _ in bas] + [b for b, _ in hapus], key=lambda b: (b.tanggal, 0 if b.jenis == 'kerusakan' else 1))
        seq = defaultdict(int)
        for b in issued:
            seq[b.tanggal.year] += 1
            b.urutan = seq[b.tanggal.year]
            b.nomor = BA_NOMOR_FORMAT.format(urutan=b.urutan, bulan_romawi=BULAN_ROMAWI[b.tanggal.month - 1], tahun=b.tanggal.year)
        all_ba = [b for b, _ in bas] + [b for b, _ in drafts]
        BeritaAcara.objects.bulk_create(all_ba, batch_size=200)
        for b, _ in hapus:
            if b.rujukan is not None:
                b.rujukan_id = b.rujukan.pk
        BeritaAcara.objects.bulk_create([b for b, _ in hapus], batch_size=200)

        items = []
        src_item = {}
        for ba, fs in bas + drafts:
            merged = {}
            for f in fs:
                it = merged.get(f['plan'].key)
                if it is None:
                    it = BeritaAcaraItem(ba=ba, asset=f['plan'].asset, sumber=f['sumber'], kerusakan=f['text'],
                                         damage_report=f['report']['obj'] if f['report'] else None,
                                         maintenance_record=f['record']['obj'] if f['record'] else None,
                                         rekomendasi=f['rekom'], created_at=dt(f['date'], 13))
                    merged[f['plan'].key] = it
                else:
                    if f['text'] not in it.kerusakan:
                        it.kerusakan = f"{it.kerusakan}; {f['text']}"
                    it.damage_report = it.damage_report or (f['report']['obj'] if f['report'] else None)
                    it.maintenance_record = it.maintenance_record or (f['record']['obj'] if f['record'] else None)
                    if f['rekom'] == 'hapus':
                        it.rekomendasi = 'hapus'
            for key, it in merged.items():
                items.append(it)
                src_item[(id(ba), key)] = it

        wos = []
        for ba, plans in hapus:
            for p in plans:
                src = src_item.get((id(ba.ref), p.key)) if ba.ref is not None else None
                items.append(BeritaAcaraItem(ba=ba, asset=p.asset, sumber='manual', rekomendasi='hapus',
                                             kerusakan=src.kerusakan if src else p.writeoff['keterangan'],
                                             damage_report=src.damage_report if src else None, created_at=dt(ba.tanggal, 15)))
                fin = self.fin_of.get(p.asset.pk)
                if fin:
                    fin.asset = p.asset
                nb = fin.book_value(p.writeoff['date']) if fin else None
                wos.append(AssetWriteOff(asset=p.asset, tanggal=p.writeoff['date'], alasan=p.writeoff['alasan'],
                                         keterangan=p.writeoff['keterangan'], nilai_perolehan=fin.nilai_awal_aset if fin else None,
                                         nilai_buku=nb.quantize(Decimal('0.01')) if nb is not None else None,
                                         nilai_jual=p.writeoff['nilai_jual'], created_by=self.ga(p.writeoff['date']),
                                         created_at=dt(p.writeoff['date'], 15)))
        BeritaAcaraItem.objects.bulk_create(items, batch_size=500)
        AssetWriteOff.objects.bulk_create(wos, batch_size=500)

        # Paragraf pembuka baku (butuh item & nomor rujukan)
        for b in [x for x, _ in bas] + [x for x, _ in hapus]:
            b.isi = b.isi_baku(b.tanggal)
        BeritaAcara.objects.bulk_update([x for x, _ in bas] + [x for x, _ in hapus], ['isi'], batch_size=200)
        self.stats['BA kerusakan terbit'] = len(bas)
        self.stats['BA kerusakan draf'] = len(drafts)
        self.stats['BA penghapusan'] = len(hapus)
        self.stats['write off'] = len(wos)

    # ── 13. Status akhir aset ───────────────────────────────────────────
    def finalize(self):
        status = {}
        for p in self.plans:
            if p.writeoff:
                status[p.asset.pk] = 'writeoff'
            elif getattr(p, 'active_report', None):
                status[p.asset.pk] = 'maintenance'
        for s in ('writeoff', 'maintenance'):
            Asset.objects.filter(pk__in=[k for k, v in status.items() if v == s]).update(lifecycle_status=s)
        for p in self.plans:
            last = p.segs[-1]
            Asset.objects.filter(pk=p.asset.pk).update(lokasi_asset=last['loc'], owned_by_department=last['owner'])
        self.stats['aset maintenance (laporan aktif)'] = sum(1 for v in status.values() if v == 'maintenance')

    def verify(self):
        problems = []
        cur = AssetMovement.objects.filter(is_current=True)
        if cur.count() != Asset.objects.count():
            problems.append(f'movement aktif {cur.count()} ≠ aset {Asset.objects.count()}')
        loc = dict(cur.values_list('asset_id', 'location_id'))
        for a in Asset.objects.only('id', 'lokasi_asset_id'):
            if loc.get(a.pk) != a.lokasi_asset_id:
                problems.append(f'lokasi aset {a.pk} tidak sama dengan movement aktif')
                break
        held = dict(cur.values_list('asset_id', 'held_by_id'))
        for asg in AssetAssignment.objects.filter(status='active'):
            if asg.assigned_to_id and held.get(asg.asset_id) != asg.assigned_to_id:
                problems.append(f'serah terima {asg.pk} tidak cocok dengan pemegang movement')
                break
        active_rep = set(DamageReport.objects.filter(report_status__in=DamageReport.ACTIVE_STATUS).values_list('asset_id', flat=True))
        maint = set(Asset.objects.filter(lifecycle_status='maintenance').values_list('id', flat=True))
        if active_rep != maint:
            problems.append(f'aset maintenance {len(maint)} ≠ aset dengan laporan aktif {len(active_rep)}')
        for a in Asset.objects.exclude(lifecycle_status='writeoff').select_related('asset_category', 'maintenance_config'):
            if a.get_effective_interval_days() and a.lifecycle_status == 'active':
                n = MaintenanceSchedule.objects.filter(asset=a, status__in=('upcoming', 'overdue'), damage_report__isnull=True).count()
                if n != 1:
                    problems.append(f'aset {a.asset_number}: {n} jadwal preventive aktif')
                    break
        if MaintenanceSchedule.objects.filter(status='upcoming', scheduled_date__lt=self.today).exists():
            problems.append('ada jadwal upcoming yang tanggalnya sudah lewat')
        if Asset.objects.filter(lifecycle_status='writeoff', assignments__status='active').exists():
            problems.append('aset write off masih punya serah terima aktif')
        if problems:
            raise CommandError('Pemeriksaan gagal:\n- ' + '\n- '.join(problems))
        self.stats['pemeriksaan konsistensi'] = 'OK'
