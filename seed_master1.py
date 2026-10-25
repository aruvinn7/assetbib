"""
╔══════════════════════════════════════════════════════════════════╗
║  SEED MASTER ASET — Asset Information Management System         ║
║  Berdasarkan: Asset_Group_and_Category_Structure.xlsx           ║
║  Departemen & Lokasi: sesuai data acuan perusahaan              ║
║                                                                  ║
║  Cara jalankan:                                                  ║
║    python manage.py shell < seed_master.py                      ║
║                                                                  ║
║  Sesuaikan nama app di baris import (default: bibasset)         ║
╚══════════════════════════════════════════════════════════════════╝
"""

from django.contrib.auth.models import User
from datetime import date, timedelta
import random
from decimal import Decimal, ROUND_DOWN
from django.db.models import Sum

# ── GANTI dengan nama app Anda ───────────────────────────────────
from bibasset.models import (
    Department, Employee, GAProfile, FinanceProfile,
    Location, AssetGroup, AssetCategory,
    Asset, AssetFinancial, AssetComponent,
    MaintenanceRecord, MaintenanceSchedule,
    initialize_schedule_for_asset, refresh_overdue_schedules,
)

TODAY = date.today()
print("=" * 72)
print("  SEED MASTER ASET")
print("=" * 72)


# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════

def buat_finansial(asset, nilai_awal, finance_emp=None):
    """
    Straight-Line Depreciation
      Residual   = 10 % dari nilai_awal
      Dep/tahun  = (NilaiAwal − Residual) / UmurEkonomis
      Book Value = NilaiAwal − (Dep × TahunTerpakai)  ≥ Residual
    """
    if hasattr(asset, "financial"):
        return asset.financial
    na  = Decimal(str(nilai_awal))
    nr  = (na * Decimal("0.10")).quantize(Decimal("1"), rounding=ROUND_DOWN)
    fin = AssetFinancial.objects.create(
        asset=asset, nilai_awal_aset=na, nilai_residual=nr,
        created_by=finance_emp,
    )
    dep = (na - nr) / Decimal(str(asset.umur_asset)) if asset.umur_asset else 0
    thn = max(TODAY.year - asset.tanggal_pembelian.year, 0)
    bv  = max(na - dep * thn, nr)
    print(f"   💰 [{asset.asset_number:<14}] "
          f"Rp {na:>13,.0f} | Resid Rp {nr:>10,.0f} | "
          f"Dep/thn Rp {dep:>10,.0f} | BV Rp {bv:>12,.0f}")
    return fin


def beli(tahun_lalu, bulan=None):
    m     = bulan or random.randint(1, 12)
    d_max = {1:31,2:28,3:31,4:30,5:31,6:30,7:31,8:31,9:30,10:31,11:30,12:31}
    d     = random.randint(1, d_max[m])
    return date(TODAY.year - tahun_lalu, m, d)


def buat_riwayat_maintenance(asset, ga_emp):
    """
    Generate riwayat maintenance realistis untuk suatu aset.

    Logika:
    - Mulai dari tanggal_pembelian + interval
    - Generate record berulang sampai "last maintenance" = 30–90 hari lalu
    - MaintenanceRecord.save() otomatis buat schedule berikutnya
    - Hasil: overdue terbesar ~90 hari, bukan 900+ hari
    """
    interval_days = asset.get_effective_interval_days()
    if not interval_days:
        return 0  # Kategori tidak punya interval → skip

    # Hapus schedule & record lama (idempotent re-seed)
    asset.schedules.all().delete()
    asset.maintenance_records.all().delete()

    # "Last maintenance" = 30–90 hari yang lalu (random)
    days_since_last = random.randint(30, 90)
    last_maint_date = TODAY - timedelta(days=days_since_last)

    # Tanggal maintenance pertama
    current_date = asset.tanggal_pembelian + timedelta(days=interval_days)

    maintenance_types = ['preventive', 'preventive', 'preventive', 'inspection', 'corrective']
    hasil_choices     = ['ok', 'ok', 'ok', 'minor_issue']
    biaya_map = {
        'preventive': lambda: random.randint(1, 5) * 50_000,
        'inspection': lambda: random.randint(1, 3) * 25_000,
        'corrective': lambda: random.randint(2, 8) * 100_000,
    }

    records_created = 0
    while current_date <= last_maint_date:
        mtype      = random.choice(maintenance_types)
        hasil      = random.choice(hasil_choices)
        biaya      = biaya_map[mtype]()
        # Variasi ±7 hari dari jadwal ideal
        actual_date = current_date + timedelta(days=random.randint(-3, 7))
        actual_date = min(actual_date, last_maint_date)

        cat_name = asset.asset_category.name if asset.asset_category else "umum"
        MaintenanceRecord.objects.create(
            asset=asset,
            maintenance_type=mtype,
            tanggal_pelaksanaan=actual_date,
            deskripsi_pekerjaan=f"Maintenance rutin — {cat_name}",
            hasil=hasil,
            biaya=Decimal(str(biaya)),
            catatan_tambahan="",
            performed_by=ga_emp,
        )
        records_created += 1
        current_date = actual_date + timedelta(days=interval_days)

    # Aset baru (interval lebih panjang dari usia) → buat schedule awal saja
    if records_created == 0:
        initialize_schedule_for_asset(asset)

    return records_created


# ════════════════════════════════════════════════════════════════
# STEP 1 — USERS & EMPLOYEE
# ════════════════════════════════════════════════════════════════
print("\n[1/7] Users & Employee …")

users_raw = [
    ("ga.staff",    "ga123",  "Ahmad Fauzi",     "EMP-001", "GA Staff",           "HRGA"),
    ("ga.spv",      "ga123",  "Siti Rahayu",     "EMP-002", "GA Supervisor",      "HRGA"),
    ("finance1",    "fin123", "Budi Santoso",    "EMP-003", "Finance Staff",      "Finance"),
    ("finance.spv", "fin123", "Dewi Kurniawati", "EMP-004", "Finance Supervisor", "Finance"),
]

all_depts, all_emps = {}, {}
for username, pwd, nama, emp_id, jabatan, dept_name in users_raw:
    dept, _ = Department.objects.get_or_create(name=dept_name)
    all_depts[dept_name] = dept
    user, created = User.objects.get_or_create(username=username)
    if created:
        user.set_password(pwd); user.save()
    emp, _ = Employee.objects.get_or_create(
        user=user,
        defaults={"employee_id": emp_id, "nama": nama,
                  "department": dept, "jabatan": jabatan},
    )
    all_emps[username] = emp
    if "ga" in username:
        GAProfile.objects.get_or_create(employee=emp)
    else:
        FinanceProfile.objects.get_or_create(employee=emp)
    print(f"   ✓ {username}")

GA      = all_emps["ga.staff"]
FINANCE = all_emps["finance1"]


# ════════════════════════════════════════════════════════════════
# STEP 2 — DEPARTEMEN (22 departemen sesuai data acuan)
# ════════════════════════════════════════════════════════════════
print("\n[2/7] Departemen …")

dept_list = [
    "Business Control & Contract Management",
    "Strategic Long Term Planning & Optimization",
    "Survey, Geology & Exploration",
    "Mtc. Planning, Reliability & QA Dept",
    "Coal Chain Equipment Mntc Department",
    "Infrastructure Project & Maintenance",
    "Project Improvement & Development",
    "Port Coal Barging & Shipment",
    "Coal Chain Coordinator",
    "Road Maintenance",
    "Coal Rom & Hauling Dept",
    "Environment & Reclamation",
    "HSE Operation",
    "HSE System, Training & Compliance",
    "Short Term Planning",
    "Continuous Improvement",
    "ER & Sustainability Development",
    "CSR & Empowerment",
    "Finance",
    "HRGA",
    "IT",
    "Digital & Technology Solutions Division",
]

for dn in dept_list:
    d, _ = Department.objects.get_or_create(name=dn)
    all_depts[dn] = d

print(f"   ✓ {len(dept_list)} departemen")

# Alias pendek
D = all_depts


# ════════════════════════════════════════════════════════════════
# STEP 3 — LOKASI (sesuai data acuan, termasuk sub-ruangan)
# ════════════════════════════════════════════════════════════════
print("\n[3/7] Lokasi …")

L = {}   # shortcut dict yang dipakai di assets_raw

def mk(name, parent=None):
    obj, _ = Location.objects.get_or_create(name=name, parent=parent)
    return obj

# ── Lokasi mandiri (top-level) ───────────────────────────────
for nama in [
    "Office Kusan", "Office Bunati", "Kantin", "Gym",
    "Gudang Electrical", "Gudang ATK", "Gudang File",
    "Logistic", "Kantor Satpam", "Mushalla", "B Mart",
    "Laundry Point", "Logistic Point", "Water Treatment Plant",
    "Mess Guest House",
]:
    L[nama] = mk(nama)

# Mess 1 – Mess 20
for i in range(1, 21):
    L[f"Mess {i}"] = mk(f"Mess {i}")

# ── Office Angsana + sub-ruangan ────────────────────────────
angsana = mk("Office Angsana")
L["Office Angsana"] = angsana

lt1 = mk("Lantai 1", parent=angsana);  L["Lantai 1"] = lt1
lt2 = mk("Lantai 2", parent=angsana);  L["Lantai 2"] = lt2

# Lantai 1
for r in ["Ruangan Attitude", "Ruangan Improvement",
          "Ruangan Integrity", "Ruang Loyal"]:
    L[r] = mk(r, parent=lt1)

# Lantai 2
for r in ["Ruangan Inovasi", "Ruangan Komitmen",
          "BOD Room", "KTT Room", "Digitech Room", "Tech Lab"]:
    L[r] = mk(r, parent=lt2)

print(f"   ✓ {len(L)} lokasi (termasuk sub-ruangan Office Angsana)")

# ── Alias pendek untuk keterbacaan di assets_raw ────────────
# Office Angsana rooms
_lt1       = L["Lantai 1"]
_lt2       = L["Lantai 2"]
_attitude  = L["Ruangan Attitude"]
_improve   = L["Ruangan Improvement"]
_integrity = L["Ruangan Integrity"]
_loyal     = L["Ruang Loyal"]
_inovasi   = L["Ruangan Inovasi"]
_komitmen  = L["Ruangan Komitmen"]
_bod       = L["BOD Room"]
_ktt       = L["KTT Room"]
_digitech  = L["Digitech Room"]
_techlab   = L["Tech Lab"]
# Lokasi lain
_kusan     = L["Office Kusan"]
_bunati    = L["Office Bunati"]
_kantin    = L["Kantin"]
_gym       = L["Gym"]
_g_elec    = L["Gudang Electrical"]
_g_atk     = L["Gudang ATK"]
_g_file    = L["Gudang File"]
_logistic  = L["Logistic"]
_satpam    = L["Kantor Satpam"]
_mushalla  = L["Mushalla"]
_bmart     = L["B Mart"]
_laundry   = L["Laundry Point"]
_log_pt    = L["Logistic Point"]
_wtp       = L["Water Treatment Plant"]
_guest     = L["Mess Guest House"]
_angsana   = L["Office Angsana"]   # root (untuk panel listrik / genset dll)
# Mess 1-20 → _m1 … _m20
for i in range(1, 21):
    globals()[f"_m{i}"] = L[f"Mess {i}"]


# ════════════════════════════════════════════════════════════════
# STEP 4 — ASSET GROUP & CATEGORY
# ════════════════════════════════════════════════════════════════
print("\n[4/7] Asset Group & Category …")

cat_defs = [
    # ( group_name, category_name, interval_hari, checklist )
    ("Elektronik","AC / Air Conditioner",30,
     "1. Bersihkan filter AC\n2. Cek kondensor & evaporator\n3. Cek refrigerant\n4. Bersihkan drainase\n5. Cek remote & timer"),
    ("Elektronik","Kulkas / Refrigerator",90,
     "1. Bersihkan bagian dalam\n2. Cek seal pintu\n3. Bersihkan kondensor\n4. Defrost jika perlu\n5. Cek suhu operasional"),
    ("Elektronik","Dispenser",60,
     "1. Sanitasi kran panas & dingin\n2. Bersihkan drip tray\n3. Flush tangki\n4. Cek pemanas & pendingin"),
    ("Elektronik","TV / Monitor Display",180,
     "1. Bersihkan layar\n2. Bersihkan ventilasi\n3. Cek kabel & koneksi\n4. Update firmware"),
    ("Elektronik","Microwave",90,
     "1. Bersihkan bagian dalam\n2. Cek pintu & seal\n3. Test fungsi timer\n4. Bersihkan filter ventilasi"),
    ("Elektronik","Rice Cooker",90,
     "1. Bersihkan panci dalam\n2. Cek seal & gasket\n3. Bersihkan elemen pemanas\n4. Test fungsi"),
    ("Elektronik","Mesin Cuci",90,
     "1. Bersihkan tabung & filter\n2. Cek selang air\n3. Bersihkan dispenser deterjen\n4. Test spin"),
    ("Elektronik","Water Heater",180,
     "1. Cek elemen pemanas\n2. Bersihkan tangki dari kerak\n3. Cek safety valve\n4. Test suhu output"),
    ("Elektronik","Kompor Listrik",90,
     "1. Bersihkan elemen\n2. Cek kabel & saklar\n3. Test masing-masing tungku\n4. Bersihkan badan"),
    ("Elektronik","Speaker / Audio System",180,
     "1. Bersihkan grille & body\n2. Cek kabel & koneksi\n3. Test suara semua channel\n4. Cek power supply"),
    ("IT Infrastructure","Laptop",180,
     "1. Bersihkan debu ventilasi/fan\n2. Bersihkan keyboard & layar\n3. Cek kondisi baterai\n4. Update OS & antivirus\n5. Cek performa disk"),
    ("IT Infrastructure","Desktop / PC",180,
     "1. Buka casing, bersihkan debu\n2. Cek thermal paste\n3. Cek semua fan\n4. Update OS & driver"),
    ("IT Infrastructure","Printer",90,
     "1. Head cleaning\n2. Cek level tinta/toner\n3. Bersihkan roller\n4. Test nozzle check"),
    ("IT Infrastructure","Scanner",180,
     "1. Bersihkan kaca scanner\n2. Bersihkan roller ADF\n3. Kalibrasi warna\n4. Test scan"),
    ("IT Infrastructure","UPS / Stabilizer",90,
     "1. Test fungsi UPS\n2. Cek kapasitas baterai\n3. Bersihkan ventilasi\n4. Catat runtime"),
    ("IT Infrastructure","Switch",180,
     "1. Bersihkan ventilasi\n2. Cek indikator port\n3. Verifikasi firmware\n4. Test konektivitas"),
    ("IT Infrastructure","Router",180,
     "1. Cek lampu indikator\n2. Bersihkan ventilasi\n3. Update firmware\n4. Backup konfigurasi"),
    ("IT Infrastructure","Access Point",180,
     "1. Bersihkan body & ventilasi\n2. Cek sinyal & coverage\n3. Update firmware\n4. Test speed"),
    ("IT Infrastructure","CCTV",60,
     "1. Bersihkan lensa kamera\n2. Cek sudut & posisi\n3. Verifikasi rekaman DVR/NVR\n4. Cek storage"),
    ("IT Infrastructure","Server",90,
     "1. Bersihkan debu internal\n2. Cek suhu CPU & storage\n3. Verifikasi backup\n4. Update patch OS"),
    ("IT Infrastructure","Storage Device / NAS",90,
     "1. Cek status disk RAID\n2. Monitor kapasitas\n3. Verifikasi backup\n4. Update firmware NAS"),
    ("IT Infrastructure","Fingerprint Attendance",90,
     "1. Bersihkan sensor fingerprint\n2. Cek akurasi pembacaan\n3. Backup data absensi\n4. Sinkronisasi waktu"),
    ("Perlengkapan Office","Mesin Fotokopi",60,
     "1. Bersihkan kaca scanner\n2. Cek level toner\n3. Bersihkan drum\n4. Test kualitas copy"),
    ("Perlengkapan Office","Proyektor",90,
     "1. Bersihkan lensa\n2. Cek jam lampu\n3. Bersihkan filter\n4. Test proyeksi"),
    ("Perlengkapan Office","Mesin Laminating",180,
     "1. Bersihkan roller laminating\n2. Cek suhu operasional\n3. Test laminasi\n4. Bersihkan badan"),
    ("Perlengkapan Office","Paper Shredder",180,
     "1. Bersihkan pisau dengan oli\n2. Cek kapasitas tempat kertas\n3. Test fungsi\n4. Bersihkan badan"),
    ("Perlengkapan Office","Whiteboard",365,
     "1. Bersihkan permukaan\n2. Cek kondisi frame\n3. Pastikan marker & eraser tersedia"),
    ("Perlengkapan Office","Mesin Absensi",90,
     "1. Bersihkan sensor\n2. Backup data\n3. Sinkronisasi waktu\n4. Cek baterai backup"),
    ("Furnitur","Kursi Kerja",365,
     "1. Cek roda/castor\n2. Cek mekanisme tinggi\n3. Bersihkan kain/mesh\n4. Cek armrest"),
    ("Furnitur","Kursi Meeting",365,
     "1. Cek kaki kursi\n2. Bersihkan jok\n3. Cek kondisi bantalan"),
    ("Furnitur","Meja Kerja",365,
     "1. Cek kondisi permukaan\n2. Cek kaki meja\n3. Bersihkan laci\n4. Cek cable management"),
    ("Furnitur","Meja Meeting",365,
     "1. Cek kondisi permukaan\n2. Cek kaki meja\n3. Bersihkan dan poles"),
    ("Furnitur","Lemari Arsip",365,
     "1. Cek engsel & kunci\n2. Bersihkan dalam & luar\n3. Cek kondisi laci"),
    ("Furnitur","Lemari Pakaian",365,
     "1. Cek engsel pintu\n2. Bersihkan dalam & luar\n3. Cek kondisi gantungan"),
    ("Furnitur","Tempat Tidur",365,
     "1. Cek rangka & sambungan\n2. Kencangkan baut\n3. Bersihkan headboard"),
    ("Furnitur","Kasur",180,
     "1. Balik & rotasi kasur\n2. Vacuum permukaan\n3. Cek kondisi pegas/foam"),
    ("Furnitur","Sofa",180,
     "1. Vacuum jok\n2. Bersihkan kain/kulit\n3. Cek kaki sofa\n4. Cek kondisi busa"),
    ("Furnitur","Rak Dokumen",365,
     "1. Cek kondisi rak\n2. Bersihkan dari debu\n3. Cek keseimbangan"),
    ("Furnitur","Locker",365,
     "1. Cek kunci & engsel\n2. Bersihkan dalam & luar\n3. Cat ulang jika berkarat"),
    ("Utilitas & Mekanikal","Genset",30,
     "1. Cek level oli\n2. Cek BBM & air radiator\n3. Test run 30 menit\n4. Cek aki starter\n5. Cek filter udara\n6. Catat jam operasional"),
    ("Utilitas & Mekanikal","Pompa Air",90,
     "1. Cek kebocoran pipa\n2. Cek tekanan operasional\n3. Bersihkan strainer\n4. Cek kondisi motor"),
    ("Utilitas & Mekanikal","Panel Listrik",90,
     "1. Cek semua breaker\n2. Cek kondisi kabel\n3. Thermal check\n4. Bersihkan panel"),
    ("Utilitas & Mekanikal","Exhaust Fan",90,
     "1. Bersihkan bilah kipas\n2. Cek bearing\n3. Cek kecepatan putaran\n4. Pelumas bearing"),
    ("Utilitas & Mekanikal","Stabilizer Tegangan Besar",90,
     "1. Cek output voltage\n2. Bersihkan ventilasi\n3. Test fungsi proteksi\n4. Cek kabel"),
    ("Utilitas & Mekanikal","APAR",180,
     "1. Cek tekanan tabung\n2. Cek segel & pin pengaman\n3. Cek kondisi selang\n4. Cek label & tanggal"),
]

grp_objs, cat_objs = {}, {}
for gn, cn, iv, cl in cat_defs:
    grp, _ = AssetGroup.objects.get_or_create(name=gn)
    grp_objs[gn] = grp
    cat, _ = AssetCategory.objects.get_or_create(
        group=grp, name=cn,
        defaults={"default_interval_value": iv,
                  "default_interval_unit": "days",
                  "maintenance_description": cl},
    )
    cat_objs[cn] = cat

print(f"   ✓ {len(grp_objs)} group, {len(cat_objs)} kategori")


# ════════════════════════════════════════════════════════════════
# STEP 5 — ASET PER ITEM
#
# Kolom tuple:
#   (no_po, asset_number, nama_asset, tgl_beli, umur_ekonomis,
#    lokasi_obj, dept_obj, group_name, category_name, nilai_awal_rp)
#
# Umur ekonomis:
#   Laptop / PC       : 4 thn    Elektronik besar : 5 thn
#   Elektronik kecil  : 3 thn    Furnitur         : 8 thn
#   Utilitas besar    : 10 thn   Utilitas kecil   : 5 thn
#
# Variasi pembelian:
#   Mayoritas 1-3 tahun lalu; sedikit yang 4-5 tahun lalu
#   (5 tahun hanya untuk aset umur ekonomis panjang seperti genset/panel)
# ════════════════════════════════════════════════════════════════
print("\n[5/7] Membuat Aset …")

assets_raw = [

    # ╔══════════════════════════════════════════════════════════╗
    # ║  ELEKTRONIK — AC / AIR CONDITIONER                      ║
    # ╚══════════════════════════════════════════════════════════╝
    # Prioritas: ruangan kantor Angsana, Office Kusan, Bunati, Mess
    ("PO-2023-001","ELK-AC-001","AC Split Daikin 2 PK — BOD Room",
     beli(2,3),5,_bod,      D["HRGA"],                                "Elektronik","AC / Air Conditioner",9_500_000),
    ("PO-2023-001","ELK-AC-002","AC Split Daikin 2 PK — KTT Room",
     beli(2,4),5,_ktt,      D["HRGA"],                                "Elektronik","AC / Air Conditioner",9_500_000),
    ("PO-2023-002","ELK-AC-003","AC Split Panasonic 1.5 PK — Ruangan Inovasi",
     beli(2,6),5,_inovasi,  D["Continuous Improvement"],              "Elektronik","AC / Air Conditioner",6_800_000),
    ("PO-2023-002","ELK-AC-004","AC Split Panasonic 1.5 PK — Digitech Room",
     beli(2,6),5,_digitech, D["Digital & Technology Solutions Division"],"Elektronik","AC / Air Conditioner",6_800_000),
    ("PO-2024-003","ELK-AC-005","AC Split Samsung 1.5 PK — Tech Lab",
     beli(1,2),5,_techlab,  D["IT"],                                  "Elektronik","AC / Air Conditioner",6_800_000),
    ("PO-2022-004","ELK-AC-006","AC Split LG 1 PK — Ruangan Attitude",
     beli(3,5),5,_attitude, D["HRGA"],                                "Elektronik","AC / Air Conditioner",5_200_000),
    ("PO-2022-004","ELK-AC-007","AC Split LG 1 PK — Ruangan Improvement",
     beli(3,5),5,_improve,  D["Continuous Improvement"],              "Elektronik","AC / Air Conditioner",5_200_000),
    ("PO-2022-004","ELK-AC-008","AC Split LG 1 PK — Ruangan Integrity",
     beli(3,7),5,_integrity,D["HRGA"],                                "Elektronik","AC / Air Conditioner",5_200_000),
    ("PO-2022-004","ELK-AC-009","AC Split LG 1 PK — Ruang Loyal",
     beli(3,7),5,_loyal,    D["HRGA"],                                "Elektronik","AC / Air Conditioner",5_200_000),
    ("PO-2022-004","ELK-AC-010","AC Split LG 1 PK — Ruangan Komitmen",
     beli(3,8),5,_komitmen, D["HRGA"],                                "Elektronik","AC / Air Conditioner",5_200_000),
    ("PO-2021-005","ELK-AC-011","AC Cassette Panasonic 3 PK — Lantai 1 Angsana",
     beli(4,9),5,_lt1,      D["HRGA"],                                "Elektronik","AC / Air Conditioner",18_000_000),
    ("PO-2021-005","ELK-AC-012","AC Cassette Panasonic 3 PK — Lantai 2 Angsana",
     beli(4,9),5,_lt2,      D["HRGA"],                                "Elektronik","AC / Air Conditioner",18_000_000),
    ("PO-2024-006","ELK-AC-013","AC Split Samsung 1 PK — Office Kusan",
     beli(1,3),5,_kusan,    D["HRGA"],                                "Elektronik","AC / Air Conditioner",5_200_000),
    ("PO-2024-006","ELK-AC-014","AC Split Samsung 1 PK — Office Bunati",
     beli(1,3),5,_bunati,   D["HRGA"],                                "Elektronik","AC / Air Conditioner",5_200_000),
    ("PO-2023-007","ELK-AC-015","AC Split Daikin 1.5 PK — Kantor Satpam",
     beli(2,8),5,_satpam,   D["HSE Operation"],                       "Elektronik","AC / Air Conditioner",6_500_000),
    ("PO-2024-008","ELK-AC-016","AC Split Samsung 1 PK — Mess Guest House",
     beli(1,5),5,_guest,    D["HRGA"],                                "Elektronik","AC / Air Conditioner",5_200_000),
    ("PO-2024-008","ELK-AC-017","AC Split Samsung 1 PK — Mess 1",
     beli(1,5),5,_m1,       D["HRGA"],                                "Elektronik","AC / Air Conditioner",5_200_000),
    ("PO-2024-008","ELK-AC-018","AC Split Samsung 1 PK — Mess 2",
     beli(1,6),5,_m2,       D["HRGA"],                                "Elektronik","AC / Air Conditioner",5_200_000),
    ("PO-2022-009","ELK-AC-019","AC Split Sharp 1 PK — Mess 3",
     beli(3,4),5,_m3,       D["HRGA"],                                "Elektronik","AC / Air Conditioner",4_800_000),
    ("PO-2022-009","ELK-AC-020","AC Split Sharp 1 PK — Mess 4",
     beli(3,4),5,_m4,       D["HRGA"],                                "Elektronik","AC / Air Conditioner",4_800_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  ELEKTRONIK — KULKAS / REFRIGERATOR                     ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-020","ELK-KL-001","Kulkas 2 Pintu Sharp 400L — Kantin",
     beli(3,8),5,_kantin,   D["HRGA"],                                "Elektronik","Kulkas / Refrigerator",6_500_000),
    ("PO-2023-021","ELK-KL-002","Kulkas 2 Pintu LG 300L — Lantai 2 Angsana",
     beli(2,4),5,_lt2,      D["HRGA"],                                "Elektronik","Kulkas / Refrigerator",5_200_000),
    ("PO-2024-022","ELK-KL-003","Kulkas Mini Polytron 150L — Mess Guest House",
     beli(1,3),3,_guest,    D["HRGA"],                                "Elektronik","Kulkas / Refrigerator",3_200_000),
    ("PO-2022-023","ELK-KL-004","Kulkas Mini Sharp 120L — BOD Room",
     beli(3,9),3,_bod,      D["HRGA"],                                "Elektronik","Kulkas / Refrigerator",2_800_000),
    ("PO-2023-024","ELK-KL-005","Kulkas 1 Pintu Panasonic 220L — Office Kusan",
     beli(2,6),5,_kusan,    D["HRGA"],                                "Elektronik","Kulkas / Refrigerator",4_200_000),
    ("PO-2024-025","ELK-KL-006","Kulkas 1 Pintu Samsung 190L — Office Bunati",
     beli(1,8),5,_bunati,   D["HRGA"],                                "Elektronik","Kulkas / Refrigerator",3_800_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  ELEKTRONIK — DISPENSER                                 ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-030","ELK-DS-001","Dispenser Miyako Hot&Cool — Lantai 1 Angsana",
     beli(3,5),3,_lt1,      D["HRGA"],                                "Elektronik","Dispenser",850_000),
    ("PO-2022-030","ELK-DS-002","Dispenser Miyako Hot&Cool — Lantai 2 Angsana",
     beli(3,6),3,_lt2,      D["HRGA"],                                "Elektronik","Dispenser",850_000),
    ("PO-2024-031","ELK-DS-003","Dispenser Cosmos — Office Kusan",
     beli(1,1),3,_kusan,    D["HRGA"],                                "Elektronik","Dispenser",750_000),
    ("PO-2023-032","ELK-DS-004","Dispenser Sanken 3 Suhu — Kantin",
     beli(2,7),3,_kantin,   D["HRGA"],                                "Elektronik","Dispenser",950_000),
    ("PO-2023-033","ELK-DS-005","Dispenser Miyako — Office Bunati",
     beli(2,9),3,_bunati,   D["HRGA"],                                "Elektronik","Dispenser",850_000),
    ("PO-2024-034","ELK-DS-006","Dispenser Cosmos — Mess Guest House",
     beli(1,4),3,_guest,    D["HRGA"],                                "Elektronik","Dispenser",750_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  ELEKTRONIK — TV / MONITOR DISPLAY                      ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2023-040","ELK-TV-001","Smart TV Samsung 65\" 4K — BOD Room",
     beli(2,6),5,_bod,      D["HRGA"],                                "Elektronik","TV / Monitor Display",14_500_000),
    ("PO-2023-041","ELK-TV-002","Smart TV Samsung 55\" 4K — KTT Room",
     beli(2,5),5,_ktt,      D["HRGA"],                                "Elektronik","TV / Monitor Display",9_500_000),
    ("PO-2022-042","ELK-TV-003","Smart TV LG 43\" — Lantai 1 Angsana",
     beli(3,9),5,_lt1,      D["HRGA"],                                "Elektronik","TV / Monitor Display",6_500_000),
    ("PO-2024-043","ELK-TV-004","Smart TV TCL 50\" 4K — Mess Guest House",
     beli(1,4),5,_guest,    D["HRGA"],                                "Elektronik","TV / Monitor Display",7_200_000),
    ("PO-2023-044","ELK-TV-005","Display 43\" — Digitech Room",
     beli(2,7),5,_digitech, D["Digital & Technology Solutions Division"],"Elektronik","TV / Monitor Display",6_800_000),
    ("PO-2024-045","ELK-TV-006","Smart TV Samsung 43\" — Office Kusan",
     beli(1,2),5,_kusan,    D["HRGA"],                                "Elektronik","TV / Monitor Display",6_500_000),
    ("PO-2022-046","ELK-TV-007","Smart TV LG 43\" — Kantin",
     beli(3,8),5,_kantin,   D["HRGA"],                                "Elektronik","TV / Monitor Display",6_000_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  ELEKTRONIK — MICROWAVE                                 ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-050","ELK-MW-001","Microwave Sharp 23L — Kantin",
     beli(3,7),3,_kantin,   D["HRGA"],                                "Elektronik","Microwave",950_000),
    ("PO-2023-051","ELK-MW-002","Microwave Panasonic 27L — Mess Guest House",
     beli(2,9),3,_guest,    D["HRGA"],                                "Elektronik","Microwave",1_200_000),
    ("PO-2024-052","ELK-MW-003","Microwave Samsung 28L — Office Kusan",
     beli(1,2),3,_kusan,    D["HRGA"],                                "Elektronik","Microwave",1_100_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  ELEKTRONIK — RICE COOKER                               ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-060","ELK-RC-001","Rice Cooker Miyako 1.8L — Kantin",
     beli(3,6),3,_kantin,   D["HRGA"],                                "Elektronik","Rice Cooker",650_000),
    ("PO-2023-061","ELK-RC-002","Rice Cooker Panasonic 1.8L — Mess Guest House",
     beli(2,8),3,_guest,    D["HRGA"],                                "Elektronik","Rice Cooker",750_000),
    ("PO-2024-062","ELK-RC-003","Rice Cooker Sharp 1.8L — Office Kusan",
     beli(1,5),3,_kusan,    D["HRGA"],                                "Elektronik","Rice Cooker",700_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  ELEKTRONIK — MESIN CUCI                                ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-070","ELK-MC-001","Mesin Cuci LG Front Load 9kg — Laundry Point",
     beli(3,5),5,_laundry,  D["HRGA"],                                "Elektronik","Mesin Cuci",6_800_000),
    ("PO-2024-071","ELK-MC-002","Mesin Cuci Sharp 2 Tabung 10kg — Laundry Point",
     beli(1,8),5,_laundry,  D["HRGA"],                                "Elektronik","Mesin Cuci",2_200_000),
    ("PO-2023-072","ELK-MC-003","Mesin Cuci Samsung Front Load 8kg — Mess Guest House",
     beli(2,4),5,_guest,    D["HRGA"],                                "Elektronik","Mesin Cuci",5_500_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  ELEKTRONIK — WATER HEATER                              ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2023-080","ELK-WH-001","Water Heater Ariston 30L — Mess 1",
     beli(2,4),5,_m1,       D["HRGA"],                                "Elektronik","Water Heater",1_800_000),
    ("PO-2023-080","ELK-WH-002","Water Heater Ariston 30L — Mess 2",
     beli(2,4),5,_m2,       D["HRGA"],                                "Elektronik","Water Heater",1_800_000),
    ("PO-2024-081","ELK-WH-003","Water Heater Ariston 30L — Mess Guest House",
     beli(1,6),5,_guest,    D["HRGA"],                                "Elektronik","Water Heater",1_800_000),
    ("PO-2022-082","ELK-WH-004","Water Heater Domo 30L — Mess 3",
     beli(3,7),5,_m3,       D["HRGA"],                                "Elektronik","Water Heater",1_500_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  ELEKTRONIK — KOMPOR LISTRIK                            ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-090","ELK-KP-001","Kompor Listrik Modena 2 Tungku — Kantin",
     beli(3,7),3,_kantin,   D["HRGA"],                                "Elektronik","Kompor Listrik",1_500_000),
    ("PO-2024-091","ELK-KP-002","Kompor Listrik Philips 2 Tungku — Mess Guest House",
     beli(1,2),3,_guest,    D["HRGA"],                                "Elektronik","Kompor Listrik",1_200_000),
    ("PO-2023-092","ELK-KP-003","Kompor Listrik Sharp 2 Tungku — Office Kusan",
     beli(2,9),3,_kusan,    D["HRGA"],                                "Elektronik","Kompor Listrik",1_100_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  ELEKTRONIK — SPEAKER / AUDIO                           ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2023-095","ELK-SP-001","Speaker JBL Eon — BOD Room",
     beli(2,5),5,_bod,      D["HRGA"],                                "Elektronik","Speaker / Audio System",4_500_000),
    ("PO-2022-096","ELK-SP-002","Sound System TOA Set — Kantin",
     beli(3,9),5,_kantin,   D["HRGA"],                                "Elektronik","Speaker / Audio System",6_500_000),
    ("PO-2024-097","ELK-SP-003","Speaker Portable JBL — Tech Lab",
     beli(1,3),5,_techlab,  D["IT"],                                  "Elektronik","Speaker / Audio System",3_200_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  IT INFRASTRUCTURE — LAPTOP (semua Lenovo ThinkPad)     ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2024-100","IT-LP-001","Laptop Lenovo ThinkPad X1 Carbon — Pimpinan",
     beli(1,4),4,_bod,      D["HRGA"],                                "IT Infrastructure","Laptop",28_000_000),
    ("PO-2023-101","IT-LP-002","Laptop Lenovo ThinkPad E15 G4 — Finance-01",
     beli(2,3),4,_lt2,      D["Finance"],                             "IT Infrastructure","Laptop",12_500_000),
    ("PO-2023-101","IT-LP-003","Laptop Lenovo ThinkPad E15 G4 — Finance-02",
     beli(2,3),4,_lt2,      D["Finance"],                             "IT Infrastructure","Laptop",12_500_000),
    ("PO-2023-101","IT-LP-004","Laptop Lenovo ThinkPad E14 G4 — Finance-03",
     beli(2,5),4,_lt2,      D["Finance"],                             "IT Infrastructure","Laptop",11_800_000),
    ("PO-2024-102","IT-LP-005","Laptop Lenovo ThinkPad L14 G4 — IT-01",
     beli(1,2),4,_techlab,  D["IT"],                                  "IT Infrastructure","Laptop",14_500_000),
    ("PO-2024-102","IT-LP-006","Laptop Lenovo ThinkPad L14 G4 — IT-02",
     beli(1,2),4,_techlab,  D["IT"],                                  "IT Infrastructure","Laptop",14_500_000),
    ("PO-2024-103","IT-LP-007","Laptop Lenovo ThinkPad T14s — DigiTech-01",
     beli(1,6),4,_digitech, D["Digital & Technology Solutions Division"],"IT Infrastructure","Laptop",16_500_000),
    ("PO-2024-103","IT-LP-008","Laptop Lenovo ThinkPad T14s — DigiTech-02",
     beli(1,6),4,_digitech, D["Digital & Technology Solutions Division"],"IT Infrastructure","Laptop",16_500_000),
    ("PO-2022-104","IT-LP-009","Laptop Lenovo ThinkPad E15 G3 — HRGA-01",
     beli(3,8),4,_lt1,      D["HRGA"],                                "IT Infrastructure","Laptop",11_000_000),
    ("PO-2022-104","IT-LP-010","Laptop Lenovo ThinkPad E15 G3 — HRGA-02",
     beli(3,8),4,_lt1,      D["HRGA"],                                "IT Infrastructure","Laptop",11_000_000),
    ("PO-2023-105","IT-LP-011","Laptop Lenovo ThinkPad E14 — BizControl-01",
     beli(2,9),4,_lt2,      D["Business Control & Contract Management"],"IT Infrastructure","Laptop",11_800_000),
    ("PO-2023-105","IT-LP-012","Laptop Lenovo ThinkPad E14 — BizControl-02",
     beli(2,9),4,_lt2,      D["Business Control & Contract Management"],"IT Infrastructure","Laptop",11_800_000),
    ("PO-2023-106","IT-LP-013","Laptop Lenovo ThinkPad E14 — LongPlan-01",
     beli(2,7),4,_lt2,      D["Strategic Long Term Planning & Optimization"],"IT Infrastructure","Laptop",11_800_000),
    ("PO-2024-107","IT-LP-014","Laptop Lenovo ThinkPad E15 — ShortPlan-01",
     beli(1,5),4,_lt2,      D["Short Term Planning"],                 "IT Infrastructure","Laptop",12_500_000),
    ("PO-2022-108","IT-LP-015","Laptop Lenovo ThinkPad E14 G2 — Survey-01",
     beli(3,6),4,_kusan,    D["Survey, Geology & Exploration"],        "IT Infrastructure","Laptop",11_000_000),
    ("PO-2022-108","IT-LP-016","Laptop Lenovo ThinkPad E14 G2 — Survey-02",
     beli(3,6),4,_kusan,    D["Survey, Geology & Exploration"],        "IT Infrastructure","Laptop",11_000_000),
    ("PO-2023-109","IT-LP-017","Laptop Lenovo ThinkPad E15 — HSE-01",
     beli(2,8),4,_lt1,      D["HSE System, Training & Compliance"],    "IT Infrastructure","Laptop",11_800_000),
    ("PO-2024-110","IT-LP-018","Laptop Lenovo ThinkPad L15 — EnvRec-01",
     beli(1,3),4,_lt2,      D["Environment & Reclamation"],            "IT Infrastructure","Laptop",13_500_000),
    ("PO-2023-111","IT-LP-019","Laptop Lenovo ThinkPad E14 — CSR-01",
     beli(2,11),4,_lt2,     D["CSR & Empowerment"],                   "IT Infrastructure","Laptop",11_800_000),
    ("PO-2024-112","IT-LP-020","Laptop Lenovo ThinkPad T14s — ContImprv-01",
     beli(1,7),4,_improve,  D["Continuous Improvement"],               "IT Infrastructure","Laptop",16_500_000),
    ("PO-2023-113","IT-LP-021","Laptop Lenovo ThinkPad E14 — MtcPlan-01",
     beli(2,6),4,_lt2,      D["Mtc. Planning, Reliability & QA Dept"],"IT Infrastructure","Laptop",11_800_000),
    ("PO-2024-114","IT-LP-022","Laptop Lenovo ThinkPad E15 — CoalChain-01",
     beli(1,9),4,_lt2,      D["Coal Chain Coordinator"],               "IT Infrastructure","Laptop",12_500_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  IT INFRASTRUCTURE — DESKTOP / PC                       ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-120","IT-PC-001","Desktop HP ProDesk 400 G6 — Tech Lab",
     beli(3,4),4,_techlab,  D["IT"],                                  "IT Infrastructure","Desktop / PC",9_500_000),
    ("PO-2023-121","IT-PC-002","Desktop Lenovo ThinkCentre — Office Kusan",
     beli(2,7),4,_kusan,    D["HRGA"],                                "IT Infrastructure","Desktop / PC",8_500_000),
    ("PO-2022-122","IT-PC-003","Desktop HP ProDesk — Office Bunati",
     beli(3,4),4,_bunati,   D["HRGA"],                                "IT Infrastructure","Desktop / PC",8_500_000),
    ("PO-2024-123","IT-PC-004","Desktop Lenovo ThinkCentre — Logistic",
     beli(1,6),4,_logistic, D["Coal Chain Coordinator"],               "IT Infrastructure","Desktop / PC",8_500_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  IT INFRASTRUCTURE — PRINTER                            ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-130","IT-PR-001","Printer Brother DCP-L2540DW — Lantai 1 Shared",
     beli(3,6),3,_lt1,      D["HRGA"],                                "IT Infrastructure","Printer",3_800_000),
    ("PO-2023-131","IT-PR-002","Printer HP LaserJet M404 — Finance",
     beli(2,8),3,_lt2,      D["Finance"],                             "IT Infrastructure","Printer",4_500_000),
    ("PO-2024-132","IT-PR-003","Printer Epson EcoTank L3250 — HRGA",
     beli(1,3),3,_lt1,      D["HRGA"],                                "IT Infrastructure","Printer",2_800_000),
    ("PO-2023-133","IT-PR-004","Printer Canon G3010 — Office Kusan",
     beli(2,10),3,_kusan,   D["HRGA"],                                "IT Infrastructure","Printer",2_500_000),
    ("PO-2022-134","IT-PR-005","Printer Brother HL-L2350 — Office Bunati",
     beli(3,5),3,_bunati,   D["HRGA"],                                "IT Infrastructure","Printer",3_200_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  IT INFRASTRUCTURE — SCANNER                            ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-140","IT-SC-001","Scanner Epson DS-320 — Finance",
     beli(3,7),4,_lt2,      D["Finance"],                             "IT Infrastructure","Scanner",3_200_000),
    ("PO-2023-141","IT-SC-002","Scanner Fujitsu fi-7030 — HRGA",
     beli(2,9),4,_lt1,      D["HRGA"],                                "IT Infrastructure","Scanner",5_800_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  IT INFRASTRUCTURE — UPS / STABILIZER                   ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-150","IT-UPS-001","UPS APC Smart-UPS 1500VA — Tech Lab",
     beli(3,5),4,_techlab,  D["IT"],                                  "IT Infrastructure","UPS / Stabilizer",9_500_000),
    ("PO-2023-151","IT-UPS-002","UPS CyberPower 1000VA — Finance",
     beli(2,7),4,_lt2,      D["Finance"],                             "IT Infrastructure","UPS / Stabilizer",3_200_000),
    ("PO-2024-152","IT-UPS-003","UPS Eaton 850VA — Office Kusan",
     beli(1,1),4,_kusan,    D["HRGA"],                                "IT Infrastructure","UPS / Stabilizer",2_800_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  IT INFRASTRUCTURE — SWITCH                             ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-160","IT-SW-001","Switch Cisco Catalyst 24-Port — Tech Lab Core",
     beli(3,4),5,_techlab,  D["IT"],                                  "IT Infrastructure","Switch",18_000_000),
    ("PO-2023-161","IT-SW-002","Switch TP-Link 24-Port Gigabit — Lantai 1",
     beli(2,6),5,_lt1,      D["IT"],                                  "IT Infrastructure","Switch",3_500_000),
    ("PO-2023-161","IT-SW-003","Switch TP-Link 24-Port Gigabit — Lantai 2",
     beli(2,6),5,_lt2,      D["IT"],                                  "IT Infrastructure","Switch",3_500_000),
    ("PO-2024-162","IT-SW-004","Switch TP-Link 8-Port — Office Kusan",
     beli(1,8),5,_kusan,    D["IT"],                                  "IT Infrastructure","Switch",1_800_000),
    ("PO-2024-162","IT-SW-005","Switch TP-Link 8-Port — Office Bunati",
     beli(1,8),5,_bunati,   D["IT"],                                  "IT Infrastructure","Switch",1_800_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  IT INFRASTRUCTURE — ROUTER                             ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-170","IT-RT-001","Router Mikrotik CCR1036 — Core Angsana",
     beli(3,3),5,_techlab,  D["IT"],                                  "IT Infrastructure","Router",15_000_000),
    ("PO-2024-171","IT-RT-002","Router Mikrotik hEX S — Office Kusan",
     beli(1,7),5,_kusan,    D["IT"],                                  "IT Infrastructure","Router",1_800_000),
    ("PO-2024-171","IT-RT-003","Router Mikrotik hEX S — Office Bunati",
     beli(1,9),5,_bunati,   D["IT"],                                  "IT Infrastructure","Router",1_800_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  IT INFRASTRUCTURE — ACCESS POINT                       ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2023-180","IT-AP-001","Access Point Ubiquiti UniFi U6 — Lantai 1",
     beli(2,4),4,_lt1,      D["IT"],                                  "IT Infrastructure","Access Point",2_200_000),
    ("PO-2023-180","IT-AP-002","Access Point Ubiquiti UniFi U6 — Lantai 2",
     beli(2,4),4,_lt2,      D["IT"],                                  "IT Infrastructure","Access Point",2_200_000),
    ("PO-2023-180","IT-AP-003","Access Point Ubiquiti UniFi U6 — Digitech Room",
     beli(2,4),4,_digitech, D["IT"],                                  "IT Infrastructure","Access Point",2_200_000),
    ("PO-2024-181","IT-AP-004","Access Point TP-Link EAP225 — Office Kusan",
     beli(1,9),4,_kusan,    D["IT"],                                  "IT Infrastructure","Access Point",1_200_000),
    ("PO-2024-181","IT-AP-005","Access Point TP-Link EAP225 — Office Bunati",
     beli(1,9),4,_bunati,   D["IT"],                                  "IT Infrastructure","Access Point",1_200_000),
    ("PO-2024-181","IT-AP-006","Access Point TP-Link EAP225 — Kantin & Area",
     beli(1,5),4,_kantin,   D["IT"],                                  "IT Infrastructure","Access Point",1_200_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  IT INFRASTRUCTURE — CCTV                               ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-190","IT-CCTV-001","CCTV Hikvision DS-2CD2 — Lantai 1 Lobby",
     beli(3,5),5,_lt1,      D["HSE Operation"],                       "IT Infrastructure","CCTV",2_800_000),
    ("PO-2022-190","IT-CCTV-002","CCTV Hikvision DS-2CD2 — Lantai 2 Angsana",
     beli(3,5),5,_lt2,      D["HSE Operation"],                       "IT Infrastructure","CCTV",2_800_000),
    ("PO-2022-190","IT-CCTV-003","CCTV Dahua 4MP — Gudang Electrical",
     beli(3,5),5,_g_elec,   D["HSE Operation"],                       "IT Infrastructure","CCTV",2_400_000),
    ("PO-2023-191","IT-CCTV-004","CCTV Hikvision PTZ — Area Logistic",
     beli(2,7),5,_logistic, D["HSE Operation"],                       "IT Infrastructure","CCTV",5_500_000),
    ("PO-2024-192","IT-CCTV-005","CCTV Dahua 2MP — Kantor Satpam",
     beli(1,3),5,_satpam,   D["HSE Operation"],                       "IT Infrastructure","CCTV",1_800_000),
    ("PO-2023-193","IT-CCTV-006","CCTV Hikvision — Office Kusan",
     beli(2,6),5,_kusan,    D["HSE Operation"],                       "IT Infrastructure","CCTV",2_800_000),
    ("PO-2024-194","IT-CCTV-007","CCTV Dahua — Office Bunati",
     beli(1,4),5,_bunati,   D["HSE Operation"],                       "IT Infrastructure","CCTV",2_400_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  IT INFRASTRUCTURE — SERVER                             ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-200","IT-SRV-001","Server HP ProLiant DL380 Gen10 — Tech Lab Utama",
     beli(3,2),5,_techlab,  D["IT"],                                  "IT Infrastructure","Server",95_000_000),
    ("PO-2024-201","IT-SRV-002","Server Dell PowerEdge R350 — Tech Lab Backup",
     beli(1,6),5,_techlab,  D["IT"],                                  "IT Infrastructure","Server",68_000_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  IT INFRASTRUCTURE — NAS / STORAGE                      ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-210","IT-NAS-001","NAS Synology DS920+ 4-Bay — Tech Lab",
     beli(3,4),5,_techlab,  D["IT"],                                  "IT Infrastructure","Storage Device / NAS",12_000_000),
    ("PO-2024-211","IT-NAS-002","NAS Synology DS720+ — Office Kusan",
     beli(1,7),5,_kusan,    D["IT"],                                  "IT Infrastructure","Storage Device / NAS",6_500_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  IT INFRASTRUCTURE — FINGERPRINT ATTENDANCE             ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2023-220","IT-FP-001","Fingerprint ZKTeco F22 — Lantai 1 Angsana",
     beli(2,5),4,_lt1,      D["HRGA"],                                "IT Infrastructure","Fingerprint Attendance",1_800_000),
    ("PO-2023-220","IT-FP-002","Fingerprint ZKTeco F22 — Lantai 2 Angsana",
     beli(2,5),4,_lt2,      D["HRGA"],                                "IT Infrastructure","Fingerprint Attendance",1_800_000),
    ("PO-2024-221","IT-FP-003","Fingerprint ZKTeco F22 — Office Kusan",
     beli(1,3),4,_kusan,    D["HRGA"],                                "IT Infrastructure","Fingerprint Attendance",1_800_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  PERLENGKAPAN OFFICE — MESIN FOTOKOPI                   ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-230","OFF-FK-001","Mesin Fotokopi Canon iR2206 — Lantai 1 Shared",
     beli(3,7),5,_lt1,      D["HRGA"],                                "Perlengkapan Office","Mesin Fotokopi",32_000_000),
    ("PO-2024-231","OFF-FK-002","Mesin Fotokopi Canon iR2425 — Finance",
     beli(1,4),5,_lt2,      D["Finance"],                             "Perlengkapan Office","Mesin Fotokopi",42_000_000),
    ("PO-2023-232","OFF-FK-003","Mesin Fotokopi Ricoh MP 2014 — Office Kusan",
     beli(2,6),5,_kusan,    D["HRGA"],                                "Perlengkapan Office","Mesin Fotokopi",28_000_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  PERLENGKAPAN OFFICE — PROYEKTOR                        ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2023-240","OFF-PJ-001","Proyektor Epson EB-X41 — BOD Room",
     beli(2,6),5,_bod,      D["HRGA"],                                "Perlengkapan Office","Proyektor",7_500_000),
    ("PO-2022-241","OFF-PJ-002","Proyektor Epson EB-S41 — KTT Room",
     beli(3,9),5,_ktt,      D["HRGA"],                                "Perlengkapan Office","Proyektor",5_800_000),
    ("PO-2024-242","OFF-PJ-003","Proyektor BenQ MS560 — Ruangan Inovasi",
     beli(1,4),5,_inovasi,  D["Continuous Improvement"],              "Perlengkapan Office","Proyektor",6_200_000),
    ("PO-2023-243","OFF-PJ-004","Proyektor Epson EB-S05 — Office Kusan",
     beli(2,8),5,_kusan,    D["HRGA"],                                "Perlengkapan Office","Proyektor",5_000_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  PERLENGKAPAN OFFICE — LAINNYA                          ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-250","OFF-LM-001","Mesin Laminating GMP A3 — HRGA",
     beli(3,8),3,_lt1,      D["HRGA"],                                "Perlengkapan Office","Mesin Laminating",1_200_000),
    ("PO-2023-251","OFF-SH-001","Paper Shredder Fellowes — Finance",
     beli(2,7),3,_lt2,      D["Finance"],                             "Perlengkapan Office","Paper Shredder",2_500_000),
    ("PO-2023-252","OFF-SH-002","Paper Shredder Ideal 2603 — HRGA",
     beli(2,4),3,_lt1,      D["HRGA"],                                "Perlengkapan Office","Paper Shredder",2_000_000),
    ("PO-2022-260","OFF-WB-001","Whiteboard 120x240cm — BOD Room",
     beli(3,6),8,_bod,      D["HRGA"],                                "Perlengkapan Office","Whiteboard",1_800_000),
    ("PO-2023-261","OFF-WB-002","Whiteboard 90x120cm — Ruangan Improvement",
     beli(2,9),8,_improve,  D["Continuous Improvement"],              "Perlengkapan Office","Whiteboard",950_000),
    ("PO-2024-262","OFF-WB-003","Whiteboard 90x120cm — Ruangan Inovasi",
     beli(1,5),8,_inovasi,  D["Continuous Improvement"],              "Perlengkapan Office","Whiteboard",950_000),
    ("PO-2022-270","OFF-AB-001","Mesin Absensi Amano — Lantai 1 Angsana",
     beli(3,5),5,_lt1,      D["HRGA"],                                "Perlengkapan Office","Mesin Absensi",3_500_000),
    ("PO-2024-271","OFF-AB-002","Mesin Absensi ZKTeco — Office Kusan",
     beli(1,2),5,_kusan,    D["HRGA"],                                "Perlengkapan Office","Mesin Absensi",3_200_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  FURNITUR — KURSI KERJA                                 ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-300","FRN-KK-001","Kursi Ergonomis Futura — HRGA-01",
     beli(3,4),8,_lt1,      D["HRGA"],                                "Furnitur","Kursi Kerja",2_800_000),
    ("PO-2022-300","FRN-KK-002","Kursi Ergonomis Futura — HRGA-02",
     beli(3,4),8,_lt1,      D["HRGA"],                                "Furnitur","Kursi Kerja",2_800_000),
    ("PO-2023-301","FRN-KK-003","Kursi Ergonomis Donati — Finance-01",
     beli(2,7),8,_lt2,      D["Finance"],                             "Furnitur","Kursi Kerja",3_500_000),
    ("PO-2023-301","FRN-KK-004","Kursi Ergonomis Donati — Finance-02",
     beli(2,7),8,_lt2,      D["Finance"],                             "Furnitur","Kursi Kerja",3_500_000),
    ("PO-2023-301","FRN-KK-005","Kursi Ergonomis Donati — Finance-03",
     beli(2,7),8,_lt2,      D["Finance"],                             "Furnitur","Kursi Kerja",3_500_000),
    ("PO-2024-302","FRN-KK-006","Kursi Herman Miller Aeron — BOD Room",
     beli(1,3),8,_bod,      D["HRGA"],                                "Furnitur","Kursi Kerja",18_000_000),
    ("PO-2022-303","FRN-KK-007","Kursi Ergonomis Futura — IT-01",
     beli(3,6),8,_techlab,  D["IT"],                                  "Furnitur","Kursi Kerja",2_800_000),
    ("PO-2022-303","FRN-KK-008","Kursi Ergonomis Futura — IT-02",
     beli(3,6),8,_techlab,  D["IT"],                                  "Furnitur","Kursi Kerja",2_800_000),
    ("PO-2024-304","FRN-KK-009","Kursi Ergonomis Sihoo — DigiTech-01",
     beli(1,5),8,_digitech, D["Digital & Technology Solutions Division"],"Furnitur","Kursi Kerja",2_200_000),
    ("PO-2024-304","FRN-KK-010","Kursi Ergonomis Sihoo — DigiTech-02",
     beli(1,5),8,_digitech, D["Digital & Technology Solutions Division"],"Furnitur","Kursi Kerja",2_200_000),
    ("PO-2023-305","FRN-KK-011","Kursi Ergonomis Futura — BizControl-01",
     beli(2,8),8,_lt2,      D["Business Control & Contract Management"],"Furnitur","Kursi Kerja",2_800_000),
    ("PO-2023-305","FRN-KK-012","Kursi Ergonomis Futura — Planning-01",
     beli(2,8),8,_lt2,      D["Strategic Long Term Planning & Optimization"],"Furnitur","Kursi Kerja",2_800_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  FURNITUR — KURSI MEETING                               ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-310","FRN-KM-001","Kursi Meeting Futura set 12 — BOD Room",
     beli(3,4),8,_bod,      D["HRGA"],                                "Furnitur","Kursi Meeting",21_600_000),
    ("PO-2023-311","FRN-KM-002","Kursi Meeting Donati set 8 — KTT Room",
     beli(2,6),8,_ktt,      D["HRGA"],                                "Furnitur","Kursi Meeting",12_000_000),
    ("PO-2024-312","FRN-KM-003","Kursi Meeting set 6 — Ruangan Inovasi",
     beli(1,3),8,_inovasi,  D["HRGA"],                                "Furnitur","Kursi Meeting",7_200_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  FURNITUR — MEJA KERJA & MEJA MEETING                   ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-320","FRN-MK-001","Meja Kerja Uno 120cm — HRGA Area set 4",
     beli(3,4),8,_lt1,      D["HRGA"],                                "Furnitur","Meja Kerja",5_600_000),
    ("PO-2023-321","FRN-MK-002","Meja Kerja Futura 140cm — Finance Area set 4",
     beli(2,7),8,_lt2,      D["Finance"],                             "Furnitur","Meja Kerja",7_200_000),
    ("PO-2024-322","FRN-MK-003","Meja Kerja Standing Desk — Tech Lab set 2",
     beli(1,3),8,_techlab,  D["IT"],                                  "Furnitur","Meja Kerja",9_500_000),
    ("PO-2023-323","FRN-MK-004","Meja Kerja Uno 120cm — DigiTech set 3",
     beli(2,5),8,_digitech, D["Digital & Technology Solutions Division"],"Furnitur","Meja Kerja",4_200_000),
    ("PO-2022-330","FRN-MM-001","Meja Meeting Oval 14 Seater — BOD Room",
     beli(3,4),8,_bod,      D["HRGA"],                                "Furnitur","Meja Meeting",28_000_000),
    ("PO-2023-331","FRN-MM-002","Meja Meeting 8 Seater — KTT Room",
     beli(2,6),8,_ktt,      D["HRGA"],                                "Furnitur","Meja Meeting",16_500_000),
    ("PO-2024-332","FRN-MM-003","Meja Meeting 6 Seater — Ruangan Inovasi",
     beli(1,3),8,_inovasi,  D["HRGA"],                                "Furnitur","Meja Meeting",10_500_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  FURNITUR — LEMARI & RAK                                ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-340","FRN-LA-001","Lemari Arsip Besi 4 Pintu — Finance",
     beli(3,5),8,_lt2,      D["Finance"],                             "Furnitur","Lemari Arsip",3_800_000),
    ("PO-2023-341","FRN-LA-002","Lemari Arsip Besi 2 Pintu — HRGA",
     beli(2,8),8,_lt1,      D["HRGA"],                                "Furnitur","Lemari Arsip",2_200_000),
    ("PO-2024-342","FRN-LA-003","Lemari Arsip Besi 2 Pintu — Gudang File",
     beli(1,2),8,_g_file,   D["HRGA"],                                "Furnitur","Lemari Arsip",2_200_000),
    ("PO-2022-350","FRN-RD-001","Rak Dokumen 5 Susun — Finance",
     beli(3,6),8,_lt2,      D["Finance"],                             "Furnitur","Rak Dokumen",1_500_000),
    ("PO-2023-351","FRN-RD-002","Rak Dokumen 5 Susun — Gudang File",
     beli(2,8),8,_g_file,   D["HRGA"],                                "Furnitur","Rak Dokumen",1_500_000),
    ("PO-2024-352","FRN-RD-003","Rak Dokumen 5 Susun — HRGA",
     beli(1,4),8,_lt1,      D["HRGA"],                                "Furnitur","Rak Dokumen",1_500_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  FURNITUR — MESS (Lemari, Tempat Tidur, Kasur, Sofa)    ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-360","FRN-LP-001","Lemari Pakaian 3 Pintu — Mess 1",
     beli(3,5),8,_m1,       D["HRGA"],                                "Furnitur","Lemari Pakaian",3_200_000),
    ("PO-2022-360","FRN-LP-002","Lemari Pakaian 3 Pintu — Mess 2",
     beli(3,5),8,_m2,       D["HRGA"],                                "Furnitur","Lemari Pakaian",3_200_000),
    ("PO-2023-361","FRN-LP-003","Lemari Pakaian 2 Pintu — Mess Guest House",
     beli(2,7),8,_guest,    D["HRGA"],                                "Furnitur","Lemari Pakaian",2_800_000),
    ("PO-2022-370","FRN-TT-001","Tempat Tidur Besi 120x200 set 2 — Mess 1",
     beli(3,5),8,_m1,       D["HRGA"],                                "Furnitur","Tempat Tidur",2_400_000),
    ("PO-2022-370","FRN-TT-002","Tempat Tidur Besi 120x200 set 2 — Mess 2",
     beli(3,5),8,_m2,       D["HRGA"],                                "Furnitur","Tempat Tidur",2_400_000),
    ("PO-2024-371","FRN-TT-003","Tempat Tidur Kayu 140x200 — Mess Guest House",
     beli(1,4),8,_guest,    D["HRGA"],                                "Furnitur","Tempat Tidur",4_500_000),
    ("PO-2023-380","FRN-KS-001","Kasur Spring Bed Comforta set 2 — Mess 1",
     beli(2,6),5,_m1,       D["HRGA"],                                "Furnitur","Kasur",4_800_000),
    ("PO-2023-380","FRN-KS-002","Kasur Spring Bed Comforta set 2 — Mess 2",
     beli(2,6),5,_m2,       D["HRGA"],                                "Furnitur","Kasur",4_800_000),
    ("PO-2024-381","FRN-KS-003","Kasur Spring Bed Olympic — Mess Guest House",
     beli(1,4),5,_guest,    D["HRGA"],                                "Furnitur","Kasur",6_500_000),
    ("PO-2022-390","FRN-SF-001","Sofa 3 Seater Informa — Mess Guest House",
     beli(3,7),8,_guest,    D["HRGA"],                                "Furnitur","Sofa",5_500_000),
    ("PO-2023-391","FRN-SF-002","Sofa 2 Seater Informa — Lantai 1 Lobby",
     beli(2,9),8,_lt1,      D["HRGA"],                                "Furnitur","Sofa",4_200_000),
    ("PO-2024-392","FRN-SF-003","Sofa L-Shape — BOD Room",
     beli(1,5),8,_bod,      D["HRGA"],                                "Furnitur","Sofa",12_000_000),
    ("PO-2022-400","FRN-LK-001","Locker Besi 12 Pintu — Area Mess",
     beli(3,5),8,_m1,       D["HRGA"],                                "Furnitur","Locker",4_500_000),
    ("PO-2024-401","FRN-LK-002","Locker Besi 6 Pintu — Lantai 1 Angsana",
     beli(1,4),8,_lt1,      D["HRGA"],                                "Furnitur","Locker",2_800_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  UTILITAS & MEKANIKAL — GENSET                          ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2020-500","UTL-GN-001","Genset Perkins 100 KVA — Angsana Utama",
     date(2020,8,15),10,_angsana,D["Infrastructure Project & Maintenance"],"Utilitas & Mekanikal","Genset",285_000_000),
    ("PO-2022-501","UTL-GN-002","Genset Cummins 50 KVA — Cadangan Angsana",
     beli(3,3),10,_angsana, D["Infrastructure Project & Maintenance"], "Utilitas & Mekanikal","Genset",175_000_000),
    ("PO-2023-502","UTL-GN-003","Genset Denyo 20 KVA — Office Kusan",
     beli(2,5),10,_kusan,   D["Infrastructure Project & Maintenance"], "Utilitas & Mekanikal","Genset",95_000_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  UTILITAS & MEKANIKAL — POMPA AIR                       ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2021-510","UTL-PM-001","Pompa Air Grundfos CM5 — Water Treatment Plant",
     beli(4,6),5,_wtp,      D["Infrastructure Project & Maintenance"], "Utilitas & Mekanikal","Pompa Air",14_500_000),
    ("PO-2023-511","UTL-PM-002","Pompa Air Shimizu 125W — Area Mess",
     beli(2,9),5,_m1,       D["Infrastructure Project & Maintenance"], "Utilitas & Mekanikal","Pompa Air",850_000),
    ("PO-2024-512","UTL-PM-003","Pompa Air Grundfos — Office Kusan",
     beli(1,4),5,_kusan,    D["Infrastructure Project & Maintenance"], "Utilitas & Mekanikal","Pompa Air",8_500_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  UTILITAS & MEKANIKAL — PANEL LISTRIK                   ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2021-520","UTL-PL-001","Panel Listrik Utama MDB — Angsana",
     beli(4,4),10,_angsana, D["Infrastructure Project & Maintenance"], "Utilitas & Mekanikal","Panel Listrik",65_000_000),
    ("PO-2023-521","UTL-PL-002","Panel Listrik Sub — Lantai 1 Angsana",
     beli(2,6),10,_lt1,     D["Infrastructure Project & Maintenance"], "Utilitas & Mekanikal","Panel Listrik",28_000_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  UTILITAS & MEKANIKAL — EXHAUST FAN                     ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-530","UTL-EF-001","Exhaust Fan KDK 20 — Toilet Lantai 1",
     beli(3,5),5,_lt1,      D["HRGA"],                                "Utilitas & Mekanikal","Exhaust Fan",650_000),
    ("PO-2022-530","UTL-EF-002","Exhaust Fan KDK 20 — Toilet Lantai 2",
     beli(3,5),5,_lt2,      D["HRGA"],                                "Utilitas & Mekanikal","Exhaust Fan",650_000),
    ("PO-2022-530","UTL-EF-003","Exhaust Fan KDK 30 — Kantin",
     beli(3,6),5,_kantin,   D["HRGA"],                                "Utilitas & Mekanikal","Exhaust Fan",1_200_000),
    ("PO-2023-531","UTL-EF-004","Exhaust Fan Panasonic — Mushalla",
     beli(2,8),5,_mushalla, D["HRGA"],                                "Utilitas & Mekanikal","Exhaust Fan",750_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  UTILITAS & MEKANIKAL — STABILIZER                      ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2021-540","UTL-ST-001","Stabilizer Matsunaga 20 KVA — MDB Angsana",
     beli(4,3),8,_angsana,  D["Infrastructure Project & Maintenance"], "Utilitas & Mekanikal","Stabilizer Tegangan Besar",28_000_000),

    # ╔══════════════════════════════════════════════════════════╗
    # ║  UTILITAS & MEKANIKAL — APAR                            ║
    # HSE bertanggung jawab atas semua titik APAR               ║
    # ╚══════════════════════════════════════════════════════════╝
    ("PO-2022-550","UTL-AP-001","APAR CO2 5kg — Tech Lab",
     beli(3,5),5,_techlab,  D["HSE Operation"],                       "Utilitas & Mekanikal","APAR",850_000),
    ("PO-2022-550","UTL-AP-002","APAR Powder 6kg — Gudang Electrical",
     beli(3,5),5,_g_elec,   D["HSE Operation"],                       "Utilitas & Mekanikal","APAR",650_000),
    ("PO-2022-550","UTL-AP-003","APAR CO2 3kg — Lantai 1 Angsana",
     beli(3,5),5,_lt1,      D["HSE Operation"],                       "Utilitas & Mekanikal","APAR",550_000),
    ("PO-2022-550","UTL-AP-004","APAR CO2 3kg — Lantai 2 Angsana",
     beli(3,5),5,_lt2,      D["HSE Operation"],                       "Utilitas & Mekanikal","APAR",550_000),
    ("PO-2023-551","UTL-AP-005","APAR Powder 3kg — Kantin",
     beli(2,7),5,_kantin,   D["HSE Operation"],                       "Utilitas & Mekanikal","APAR",450_000),
    ("PO-2024-552","UTL-AP-006","APAR CO2 3kg — Ruang Genset Angsana",
     beli(1,2),5,_angsana,  D["HSE Operation"],                       "Utilitas & Mekanikal","APAR",550_000),
    ("PO-2023-553","UTL-AP-007","APAR Powder 3kg — Office Kusan",
     beli(2,9),5,_kusan,    D["HSE Operation"],                       "Utilitas & Mekanikal","APAR",450_000),
    ("PO-2024-554","UTL-AP-008","APAR Powder 3kg — Office Bunati",
     beli(1,6),5,_bunati,   D["HSE Operation"],                       "Utilitas & Mekanikal","APAR",450_000),
    ("PO-2024-555","UTL-AP-009","APAR CO2 3kg — Mess Guest House",
     beli(1,3),5,_guest,    D["HSE Operation"],                       "Utilitas & Mekanikal","APAR",550_000),
    ("PO-2023-556","UTL-AP-010","APAR Powder 3kg — Area Logistic",
     beli(2,6),5,_logistic, D["HSE Operation"],                       "Utilitas & Mekanikal","APAR",450_000),
]

print(f"   Akan membuat {len(assets_raw)} aset …")
created_assets = {}

for (no_po, asset_number, nama_asset, tgl_beli, umur,
     lokasi, dept, group_name, cat_name, nilai_awal) in assets_raw:

    asset, created = Asset.objects.get_or_create(
        asset_number=asset_number,
        defaults={
            "no_po":               no_po,
            "nama_asset":          nama_asset,
            "tanggal_pembelian":   tgl_beli,
            "umur_asset":          umur,
            "lokasi_asset":        lokasi,
            "owned_by_department": dept,
            "asset_group":         grp_objs.get(group_name),
            "asset_category":      cat_objs.get(cat_name),
            "lifecycle_status":    "active",
            "created_by":          GA,
        },
    )
    created_assets[asset_number] = (asset, nilai_awal)

    if created:
        n_records = buat_riwayat_maintenance(asset, GA)
        # Jika tidak ada record (aset baru, interval panjang), schedule sudah dibuat
        # oleh buat_riwayat_maintenance via initialize_schedule_for_asset

    print(f"   [{'BARU' if created else 'ADA '}] {asset_number} — {nama_asset[:55]}")

print(f"\n   ✓ Total aset di DB: {Asset.objects.count()}")


# ════════════════════════════════════════════════════════════════
# STEP 6 — DATA FINANSIAL
# ════════════════════════════════════════════════════════════════
print("\n[6/7] Data Finansial (Straight-Line Depreciation) …")
print("   " + "─" * 70)
print(f"   {'NOMOR':<16} {'NILAI AWAL':>14} {'RESIDUAL':>11} "
      f"{'DEP/THN':>11} {'BOOK VALUE':>13}")
print("   " + "─" * 70)

for asset_number, (asset, nilai_awal) in created_assets.items():
    if not hasattr(asset, "financial"):
        buat_finansial(asset, nilai_awal, finance_emp=FINANCE)

print("   " + "─" * 70)
print(f"\n   ✓ Total tervaluasi: {AssetFinancial.objects.count()} aset")


# ════════════════════════════════════════════════════════════════
# STEP 7 — BOM (Dispenser, Kulkas, TV saja)
# ════════════════════════════════════════════════════════════════
print("\n[7/7] Bill of Materials (Dispenser, Kulkas, TV) …")

bom_data = {
    # ── DISPENSER ────────────────────────────────────────────────
    "ELK-DS-001": [
        ("DS-KRAN-PN","Kran Panas",          "Kran output air panas",      1,True, "Toko Dispenser",   45_000),
        ("DS-KRAN-DN","Kran Dingin",         "Kran output air dingin",     1,True, "Toko Dispenser",   35_000),
        ("DS-ELEMEN", "Elemen Pemanas",      "Heating element 350W",       1,True, "Toko Sparepart",  120_000),
        ("DS-KOMPR",  "Kompresor Pendingin", "Kompresor freon R134a",      1,True, "Toko Sparepart",  280_000),
        ("DS-DRIP",   "Drip Tray",           "Nampan penampung tetes",     1,False,"Toko Dispenser",   25_000),
        ("DS-GALON",  "Dudukan Galon",       "Bracket holder galon",       1,False,"Toko Dispenser",   55_000),
    ],
    "ELK-DS-002": [
        ("DS-KRAN-PN","Kran Panas",          "Kran output air panas",      1,True, "Toko Dispenser",   45_000),
        ("DS-KRAN-DN","Kran Dingin",         "Kran output air dingin",     1,True, "Toko Dispenser",   35_000),
        ("DS-ELEMEN", "Elemen Pemanas",      "Heating element 350W",       1,True, "Toko Sparepart",  120_000),
        ("DS-KOMPR",  "Kompresor Pendingin", "Kompresor freon R134a",      1,True, "Toko Sparepart",  280_000),
        ("DS-DRIP",   "Drip Tray",           "Nampan penampung tetes",     1,False,"Toko Dispenser",   25_000),
    ],
    "ELK-DS-003": [
        ("DS-KRAN-PN","Kran Panas",          "Kran output air panas",      1,True, "Toko Dispenser",   45_000),
        ("DS-KRAN-DN","Kran Dingin",         "Kran output air dingin",     1,True, "Toko Dispenser",   35_000),
        ("DS-ELEMEN", "Elemen Pemanas",      "Heating element 350W",       1,True, "Toko Sparepart",  120_000),
        ("DS-DRIP",   "Drip Tray",           "Nampan penampung tetes",     1,False,"Toko Dispenser",   25_000),
    ],
    "ELK-DS-004": [
        ("DS-KRAN-PN","Kran Panas",          "Kran output air panas",      1,True, "Toko Dispenser",   45_000),
        ("DS-KRAN-DN","Kran Dingin",         "Kran output air dingin",     1,True, "Toko Dispenser",   35_000),
        ("DS-KRAN-NR","Kran Normal / Room",  "Kran air suhu ruangan",      1,True, "Toko Dispenser",   35_000),
        ("DS-ELEMEN", "Elemen Pemanas",      "Heating element 350W",       1,True, "Toko Sparepart",  120_000),
        ("DS-KOMPR",  "Kompresor Pendingin", "Kompresor freon R134a",      1,True, "Toko Sparepart",  280_000),
        ("DS-DRIP",   "Drip Tray",           "Nampan penampung tetes",     1,False,"Toko Dispenser",   25_000),
    ],
    "ELK-DS-005": [
        ("DS-KRAN-PN","Kran Panas",          "Kran output air panas",      1,True, "Toko Dispenser",   45_000),
        ("DS-KRAN-DN","Kran Dingin",         "Kran output air dingin",     1,True, "Toko Dispenser",   35_000),
        ("DS-ELEMEN", "Elemen Pemanas",      "Heating element 350W",       1,True, "Toko Sparepart",  120_000),
        ("DS-DRIP",   "Drip Tray",           "Nampan penampung tetes",     1,False,"Toko Dispenser",   25_000),
    ],
    "ELK-DS-006": [
        ("DS-KRAN-PN","Kran Panas",          "Kran output air panas",      1,True, "Toko Dispenser",   45_000),
        ("DS-KRAN-DN","Kran Dingin",         "Kran output air dingin",     1,True, "Toko Dispenser",   35_000),
        ("DS-ELEMEN", "Elemen Pemanas",      "Heating element 350W",       1,True, "Toko Sparepart",  120_000),
        ("DS-DRIP",   "Drip Tray",           "Nampan penampung tetes",     1,False,"Toko Dispenser",   25_000),
    ],
    # ── KULKAS ───────────────────────────────────────────────────
    "ELK-KL-001": [
        ("KL-KOMPR",  "Kompresor",           "Kompresor kulkas Sharp",     1,True, "Service Center Sharp",   850_000),
        ("KL-FREON",  "Refrigerant R600a",   "Freon pengisian 100gr",      1,True, "Toko Sparepart",          95_000),
        ("KL-SEAL-P", "Seal Pintu Atas",     "Karet seal pintu freezer",   1,True, "Toko Sparepart",          85_000),
        ("KL-SEAL-B", "Seal Pintu Bawah",    "Karet seal pintu utama",     1,True, "Toko Sparepart",          75_000),
        ("KL-TERMOS", "Termostat",           "Thermostat kontrol suhu",    1,True, "Toko Sparepart",         120_000),
        ("KL-LAMPU",  "Lampu LED Dalam",     "Lampu interior kulkas",      1,False,"Toko Sparepart",          35_000),
        ("KL-RAK",    "Rak Kaca",            "Rak kaca internal",          3,False,"Service Center Sharp",    85_000),
    ],
    "ELK-KL-002": [
        ("KL-KOMPR",  "Kompresor",           "Kompresor kulkas LG",        1,True, "Service Center LG",      950_000),
        ("KL-FREON",  "Refrigerant R600a",   "Freon pengisian 100gr",      1,True, "Toko Sparepart",          95_000),
        ("KL-SEAL-P", "Seal Pintu Atas",     "Karet seal pintu freezer",   1,True, "Toko Sparepart",          85_000),
        ("KL-SEAL-B", "Seal Pintu Bawah",    "Karet seal pintu utama",     1,True, "Toko Sparepart",          75_000),
        ("KL-TERMOS", "Termostat",           "Thermostat kontrol suhu",    1,True, "Toko Sparepart",         120_000),
        ("KL-LAMPU",  "Lampu LED Dalam",     "Lampu interior kulkas",      1,False,"Toko Sparepart",          35_000),
    ],
    "ELK-KL-003": [
        ("KL-KOMPR",  "Kompresor",           "Kompresor kulkas mini",      1,True, "Toko Sparepart",         550_000),
        ("KL-SEAL-B", "Seal Pintu",          "Karet seal pintu utama",     1,True, "Toko Sparepart",          65_000),
        ("KL-TERMOS", "Termostat",           "Thermostat kontrol suhu",    1,True, "Toko Sparepart",          95_000),
        ("KL-LAMPU",  "Lampu LED Dalam",     "Lampu interior kulkas",      1,False,"Toko Sparepart",          25_000),
    ],
    "ELK-KL-004": [
        ("KL-KOMPR",  "Kompresor",           "Kompresor kulkas mini",      1,True, "Toko Sparepart",         450_000),
        ("KL-SEAL-B", "Seal Pintu",          "Karet seal pintu utama",     1,True, "Toko Sparepart",          55_000),
        ("KL-TERMOS", "Termostat",           "Thermostat kontrol suhu",    1,True, "Toko Sparepart",          85_000),
    ],
    "ELK-KL-005": [
        ("KL-KOMPR",  "Kompresor",           "Kompresor kulkas Panasonic", 1,True, "Service Center Panasonic",750_000),
        ("KL-FREON",  "Refrigerant R600a",   "Freon pengisian 100gr",      1,True, "Toko Sparepart",           95_000),
        ("KL-SEAL-P", "Seal Pintu Atas",     "Karet seal pintu freezer",   1,True, "Toko Sparepart",           85_000),
        ("KL-SEAL-B", "Seal Pintu Bawah",    "Karet seal pintu utama",     1,True, "Toko Sparepart",           75_000),
        ("KL-TERMOS", "Termostat",           "Thermostat kontrol suhu",    1,True, "Toko Sparepart",          120_000),
        ("KL-LAMPU",  "Lampu LED Dalam",     "Lampu interior kulkas",      1,False,"Toko Sparepart",           35_000),
    ],
    "ELK-KL-006": [
        ("KL-KOMPR",  "Kompresor",           "Kompresor kulkas Samsung",   1,True, "Service Center Samsung",  800_000),
        ("KL-SEAL-B", "Seal Pintu",          "Karet seal pintu utama",     1,True, "Toko Sparepart",           65_000),
        ("KL-TERMOS", "Termostat",           "Thermostat kontrol suhu",    1,True, "Toko Sparepart",           95_000),
        ("KL-LAMPU",  "Lampu LED Dalam",     "Lampu interior kulkas",      1,False,"Toko Sparepart",           35_000),
    ],
    # ── TV / MONITOR DISPLAY ─────────────────────────────────────
    "ELK-TV-001": [
        ("TV-MB",    "Main Board",           "Board utama Samsung 65\"",   1,True, "Service Center Samsung",3_800_000),
        ("TV-PSU",   "Power Supply Board",   "Board power supply",         1,True, "Service Center Samsung",1_500_000),
        ("TV-TCON",  "T-CON Board",          "Timing control board",       1,True, "Service Center Samsung",  950_000),
        ("TV-REMOTE","Remote Control",       "Remote TV Original Samsung",  1,False,"Toko Elektronik",          95_000),
        ("TV-KABEL", "Kabel HDMI 2.0 1.5m",  "Kabel koneksi HDMI",        2,False,"Toko Elektronik",           85_000),
        ("TV-BRCKT", "Wall Bracket 65\"",    "Bracket dinding TV",         1,False,"Toko Elektronik",          250_000),
    ],
    "ELK-TV-002": [
        ("TV-MB",    "Main Board",           "Board utama Samsung 55\"",   1,True, "Service Center Samsung",2_800_000),
        ("TV-PSU",   "Power Supply Board",   "Board power supply",         1,True, "Service Center Samsung",1_200_000),
        ("TV-TCON",  "T-CON Board",          "Timing control board",       1,True, "Service Center Samsung",  850_000),
        ("TV-REMOTE","Remote Control",       "Remote TV Original Samsung",  1,False,"Toko Elektronik",          95_000),
        ("TV-KABEL", "Kabel HDMI 2.0 1.5m",  "Kabel koneksi HDMI",        2,False,"Toko Elektronik",           85_000),
        ("TV-BRCKT", "Wall Bracket 55\"",    "Bracket dinding TV",         1,False,"Toko Elektronik",          185_000),
    ],
    "ELK-TV-003": [
        ("TV-MB",    "Main Board",           "Board utama LG 43\"",        1,True, "Service Center LG",    2_200_000),
        ("TV-PSU",   "Power Supply Board",   "Board power supply",         1,True, "Service Center LG",      950_000),
        ("TV-REMOTE","Remote Control",       "Remote TV Original LG",      1,False,"Toko Elektronik",          85_000),
        ("TV-KABEL", "Kabel HDMI 2.0 1.5m",  "Kabel koneksi HDMI",        2,False,"Toko Elektronik",           85_000),
        ("TV-BRCKT", "Wall Bracket",         "Bracket dinding TV",         1,False,"Toko Elektronik",          175_000),
    ],
    "ELK-TV-004": [
        ("TV-MB",    "Main Board",           "Board utama TCL 50\"",       1,True, "Service Center TCL",   2_500_000),
        ("TV-PSU",   "Power Supply Board",   "Board power supply",         1,True, "Service Center TCL",   1_000_000),
        ("TV-REMOTE","Remote Control",       "Remote TV Original TCL",     1,False,"Toko Elektronik",          85_000),
        ("TV-KABEL", "Kabel HDMI 2.0 1.5m",  "Kabel koneksi HDMI",        2,False,"Toko Elektronik",           85_000),
        ("TV-BRCKT", "Wall Bracket",         "Bracket dinding TV",         1,False,"Toko Elektronik",          175_000),
    ],
    "ELK-TV-005": [
        ("TV-MB",    "Main Board",           "Board utama Display 43\"",   1,True, "Service Center LG",    2_000_000),
        ("TV-PSU",   "Power Supply Board",   "Board power supply",         1,True, "Service Center LG",      850_000),
        ("TV-KABEL", "Kabel HDMI 2.0 1.5m",  "Kabel koneksi HDMI",        2,False,"Toko Elektronik",           85_000),
        ("TV-BRCKT", "Wall Bracket",         "Bracket dinding",            1,False,"Toko Elektronik",          175_000),
    ],
    "ELK-TV-006": [
        ("TV-MB",    "Main Board",           "Board utama Samsung 43\"",   1,True, "Service Center Samsung",2_200_000),
        ("TV-PSU",   "Power Supply Board",   "Board power supply",         1,True, "Service Center Samsung",  950_000),
        ("TV-REMOTE","Remote Control",       "Remote TV Original Samsung",  1,False,"Toko Elektronik",          85_000),
        ("TV-KABEL", "Kabel HDMI 2.0 1.5m",  "Kabel koneksi HDMI",        2,False,"Toko Elektronik",           85_000),
        ("TV-BRCKT", "Wall Bracket",         "Bracket dinding TV",         1,False,"Toko Elektronik",          175_000),
    ],
    "ELK-TV-007": [
        ("TV-MB",    "Main Board",           "Board utama LG 43\"",        1,True, "Service Center LG",    2_200_000),
        ("TV-PSU",   "Power Supply Board",   "Board power supply",         1,True, "Service Center LG",      950_000),
        ("TV-REMOTE","Remote Control",       "Remote TV Original LG",      1,False,"Toko Elektronik",          85_000),
        ("TV-KABEL", "Kabel HDMI 2.0 1.5m",  "Kabel koneksi HDMI",        2,False,"Toko Elektronik",           85_000),
    ],
}

for asset_number, comps in bom_data.items():
    tpl = created_assets.get(asset_number)
    if not tpl:
        continue
    asset = tpl[0]
    for (pn, name, desc, qty, critical, supplier, price) in comps:
        AssetComponent.objects.get_or_create(
            asset=asset, part_number=pn,
            defaults={"component_name": name, "description": desc,
                      "quantity": qty, "is_critical": critical,
                      "supplier_name": supplier,
                      "estimated_price": Decimal(str(price)),
                      "created_by": GA},
        )
        print(f"   {'⚠ KRITIS' if critical else '      '} [{asset_number}] {name}")


# ════════════════════════════════════════════════════════════════
# REFRESH OVERDUE & RINGKASAN AKHIR
# ════════════════════════════════════════════════════════════════
n = refresh_overdue_schedules()
print(f"\n   ↻ {n} jadwal diupdate → overdue")

total_nilai = AssetFinancial.objects.aggregate(t=Sum("nilai_awal_aset"))["t"] or 0

print("\n" + "=" * 72)
print("  RINGKASAN SEED MASTER")
print("=" * 72)
print(f"  Departemen          : {Department.objects.count()}")
print(f"  Lokasi (total)      : {Location.objects.count()}")
print(f"  Asset Group         : {AssetGroup.objects.count()}")
print(f"  Asset Category      : {AssetCategory.objects.count()}")
print(f"  Total Aset          : {Asset.objects.count()}")
print(f"  Data Finansial      : {AssetFinancial.objects.count()}")
print(f"  Komponen BOM        : {AssetComponent.objects.count()}")
print(f"  Riwayat Maintenance : {MaintenanceRecord.objects.count()}")
print(f"  Schedule Upcoming   : {MaintenanceSchedule.objects.filter(status='upcoming').count()}")
print(f"  Schedule Overdue    : {MaintenanceSchedule.objects.filter(status='overdue').count()}")
print(f"\n  💰 Total Nilai Aset : Rp {total_nilai:,.0f}")

print()
print("  Distribusi Umur Aset:")
for y in [1, 2, 3, 4, 5]:
    yr  = TODAY.year - y
    cnt = Asset.objects.filter(tanggal_pembelian__year=yr).count()
    print(f"    {y} thn lalu ({yr}): {cnt:3d} aset  {'█' * cnt}")

print()
print("  Distribusi per Lokasi Utama:")
for nama_lok in [
    "Office Angsana", "Lantai 1", "Lantai 2",
    "Office Kusan", "Office Bunati",
    "Kantin", "Mess Guest House",
]:
    obj = L.get(nama_lok)
    if obj:
        cnt = Asset.objects.filter(lokasi_asset=obj).count()
        print(f"    {nama_lok:<28}: {cnt:3d} aset")

print()
print("  User Login:")
print("  ┌──────────────┬───────────┬────────────────────┐")
print("  │ Username     │ Password  │ Role               │")
print("  ├──────────────┼───────────┼────────────────────┤")
print("  │ ga.staff     │ ga123     │ GA (full akses)    │")
print("  │ ga.spv       │ ga123     │ GA Supervisor      │")
print("  │ finance1     │ fin123    │ Finance            │")
print("  │ finance.spv  │ fin123    │ Finance Supervisor │")
print("  └──────────────┴───────────┴────────────────────┘")
print()
print("  Selesai! → python manage.py runserver")
print("=" * 72)
