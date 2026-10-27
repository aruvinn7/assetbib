
import re

from django.contrib.auth.models import User
from django.db import models, transaction
from django.db.models import Case, Count, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone


# ================================================================
# HEALTH RULES — satu-satunya sumber threshold & logika health aset
# ================================================================

# Threshold berdasarkan nilai aset: (batas atas nilai, {level: % biaya perbaikan})
# Dasar: aturan praktis repair-vs-replace "50%" — bila akumulasi biaya PERBAIKAN (corrective)
# mencapai ±setengah nilai perolehan, penggantian layak ditimbang. Aset murah lebih cepat
# ditandai (mengganti murah); aset mahal lebih longgar (penggantian = keputusan modal besar).
REPAIR_THRESHOLD_TIERS = [
    (2_000_000,  {'warning': 15, 'waspada': 30, 'critical': 50}),
    (10_000_000, {'warning': 20, 'waspada': 35, 'critical': 50}),
    (50_000_000, {'warning': 25, 'waspada': 40, 'critical': 60}),
    (None,       {'warning': 30, 'waspada': 50, 'critical': 75}),  # > 50jt
]
BREAKDOWN_WARNING = 5


def get_repair_threshold(nilai_awal):
    nilai = float(nilai_awal or 0)
    for ceiling, thresholds in REPAIR_THRESHOLD_TIERS:
        if ceiling is None or nilai < ceiling:
            return thresholds
    return REPAIR_THRESHOLD_TIERS[-1][1]


def compute_health(total_biaya, breakdown, nilai_awal):
    """
    Return (status, ratio_persen). Dipakai oleh list (dari annotate)
    dan detail (dari aggregate) supaya hasilnya selalu sama.
    """
    breakdown = breakdown or 0
    if not nilai_awal:
        return ('warning' if breakdown >= BREAKDOWN_WARNING else 'no_data'), None

    ratio     = float(total_biaya or 0) / float(nilai_awal) * 100
    threshold = get_repair_threshold(nilai_awal)
    if ratio >= threshold['critical']:  return 'critical', ratio
    if ratio >= threshold['waspada']:   return 'waspada', ratio
    if ratio >= threshold['warning']:   return 'warning', ratio
    if breakdown >= BREAKDOWN_WARNING:  return 'warning', ratio
    return 'normal', ratio


def selisih_tahun_bulan(awal, akhir):
    """Umur kalender penuh dari `awal` sampai `akhir` → (tahun, bulan). Satu-satunya rumus umur aset."""
    bulan = (akhir.year - awal.year) * 12 + (akhir.month - awal.month)
    if akhir.day < awal.day:
        bulan -= 1
    bulan = max(bulan, 0)
    return bulan // 12, bulan % 12


# ================================================================
# MASTER EMPLOYEE
# ================================================================

class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Employee(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="employee")
    employee_id = models.CharField(max_length=20, unique=True)
    nama = models.CharField(max_length=150)
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True
    )
    jabatan = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.employee_id} - {self.nama}"


# ================================================================
# ROLE PROFILE
# ================================================================

class GAProfile(models.Model):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="ga_profile")

    def __str__(self):
        return f"GA - {self.employee.nama}"


class FinanceProfile(models.Model):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="finance_profile")

    def __str__(self):
        return f"Finance - {self.employee.nama}"


class UserProfile(models.Model):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="user_profile")

    def __str__(self):
        return f"User - {self.employee.nama}"


# ================================================================
# MASTER LOKASI & GRUP
# ================================================================

class Location(models.Model):
    name = models.CharField(max_length=100)
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    # Cache "Induk > Anak > Cucu" — supaya __str__ tidak query parent berulang (N+1)
    full_path = models.CharField(max_length=500, blank=True, editable=False)

    class Meta:
        unique_together = ('name', 'parent')

    def __str__(self):
        return self.full_path or self.name

    def build_full_path(self):
        if not self.parent_id:
            return self.name
        parent = Location.objects.only('full_path', 'name').get(pk=self.parent_id)
        return f"{parent.full_path or parent.name} > {self.name}"

    def save(self, *args, **kwargs):
        self.full_path = self.build_full_path()
        super().save(*args, **kwargs)
        # Nama/induk berubah → path semua turunan ikut berubah
        for child in self.children.all():
            child.save()


class AssetGroup(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class AssetCategory(models.Model):
    INTERVAL_UNIT = [
        ('days',   'Hari'),
        ('months', 'Bulan'),
    ]
    # Kategori penampung untuk aset yang grupnya diketahui tapi jenisnya belum ada kategorinya
    LAINNYA = 'Lainnya'

    # Tanpa index sendiri: sudah tercakup unique (group, name)
    group = models.ForeignKey(AssetGroup, on_delete=models.CASCADE, related_name='categories', db_index=False)
    name = models.CharField(max_length=100)
    default_interval_value = models.PositiveIntegerField(null=True, blank=True)
    default_interval_unit = models.CharField(max_length=10, choices=INTERVAL_UNIT, default='months')
    maintenance_description = models.TextField(blank=True)

    class Meta:
        unique_together = ('group', 'name')
        verbose_name = 'Asset Category'
        verbose_name_plural = 'Asset Categories'

    def default_interval_in_days(self):
        if not self.default_interval_value:
            return None
        return self.default_interval_value * 30 if self.default_interval_unit == 'months' \
               else self.default_interval_value

    def __str__(self):
        return f"{self.group.name} → {self.name}"

    @classmethod
    def lainnya(cls, group):
        """Kategori 'Lainnya' milik grup ini (dibuat bila belum ada)."""
        return cls.objects.get_or_create(group=group, name=cls.LAINNYA)[0]


# ================================================================
# ASSET QUERYSET & MANAGER
# ================================================================

class AssetQuerySet(models.QuerySet):
    """
    Query logic yang tadinya berulang di views, dipindah ke sini.
    Contoh: Asset.objects.with_health_annotations().critical_health()
    """

    def with_health_annotations(self):
        """
        total_biaya_annot + jumlah_corrective dihitung di SQL,
        lalu compute_health() dipakai per baris tanpa query tambahan.
        total_biaya_annot = biaya PERBAIKAN saja (corrective). Biaya preventive &
        inspeksi tidak dihitung: aset yang rajin dirawat tidak boleh terlihat makin "sakit".
        """
        return self.annotate(
            total_biaya_annot=Coalesce(
                Sum('maintenance_records__biaya', filter=Q(maintenance_records__maintenance_type='corrective')),
                Value(Decimal('0')),
                output_field=models.DecimalField(max_digits=15, decimal_places=2),
            ),
            jumlah_corrective=Count(
                'maintenance_records',
                filter=Q(maintenance_records__maintenance_type='corrective'),
            ),
        )

    def critical_health(self):
        """
        Filter health == 'critical' di SQL (butuh with_health_annotations()).
        Batas % critical mengikuti REPAIR_THRESHOLD_TIERS.
        """
        whens = [
            When(financial__nilai_awal_aset__lt=ceiling, then=Value(t['critical']))
            for ceiling, t in REPAIR_THRESHOLD_TIERS if ceiling is not None
        ]
        critical_pct = Case(
            *whens, default=Value(REPAIR_THRESHOLD_TIERS[-1][1]['critical']),
            output_field=models.IntegerField(),
        )
        return self.alias(critical_pct=critical_pct).filter(
            financial__nilai_awal_aset__gt=0,
            total_biaya_annot__gte=F('financial__nilai_awal_aset') * F('critical_pct') / 100,
        )

    def dashboard_stats(self):
        """
        1 query untuk semua angka di stat cards dashboard & asset_list.
        Sebelumnya: 5+ query terpisah (count per status).
        """
        return self.aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(lifecycle_status='active')),
            in_maintenance=Count('id', filter=Q(lifecycle_status='maintenance')),
            writeoff=Count('id', filter=Q(lifecycle_status='writeoff')),
            unvalued=Count('id', filter=Q(financial__isnull=True)),
        )


# Semua method AssetQuerySet otomatis tersedia di Asset.objects
AssetManager = models.Manager.from_queryset(AssetQuerySet)


# ================================================================
# ASSET
# ================================================================

class Asset(models.Model):

    STATUS = [
        ('active',      'Active'),
        ('maintenance', 'Maintenance'),
        ('writeoff',    'Writeoff'),
    ]

    no_po             = models.CharField(max_length=50)
    asset_number      = models.CharField(max_length=50, unique=True)
    nama_asset        = models.CharField(max_length=200)
    tanggal_pembelian = models.DateField()
    lokasi_asset      = models.ForeignKey(Location,    on_delete=models.SET_NULL, null=True, blank=True)
    owned_by_department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    # Grup aset = asset_category.group (tidak disimpan terpisah supaya tidak bisa berbeda)
    asset_category    = models.ForeignKey(
        AssetCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='assets'
    )
    lifecycle_status  = models.CharField(max_length=20, choices=STATUS, default='active')
    created_by        = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    objects = AssetManager()  # Custom manager

    class Meta:
        # Kolom paling sering dipakai sebagai filter di views; filter lifecycle_status saja
        # sudah terlayani index pertama (kolom terdepan)
        indexes = [
            models.Index(fields=['lifecycle_status', 'asset_category']),
        ]

    # ── Properties ──────────────────────────────────────────────

    @property
    def umur_asset_detail(self):
        years, months = selisih_tahun_bulan(self.tanggal_pembelian, date.today())
        return {"years": years, "months": months}

    def __str__(self):
        return f"{self.asset_number} - {self.nama_asset}"

    # ── Helper Methods ───────────────────────────────────────────

    def has_financial(self):
        return hasattr(self, 'financial')

    def get_effective_interval_days(self):
        if hasattr(self, 'maintenance_config'):
            return self.maintenance_config.custom_interval_days
        if self.asset_category:
            return self.asset_category.default_interval_in_days()
        return None

    def get_next_schedule(self):
        return self.schedules.filter(status='upcoming').order_by('scheduled_date').first()

    def get_pending_damage_report(self):
        return self.damage_reports.filter(report_status__in=DamageReport.ACTIVE_STATUS).first()

    def maintenance_status(self):
        # Sudah dihitung massal oleh attach_maintenance_status() → 0 query
        if hasattr(self, '_maintenance_status_cache'):
            return self._maintenance_status_cache
        if not self.get_effective_interval_days():
            return 'not_configured'
        return schedule_status(self.schedules.active().order_by('scheduled_date').first())

    # ── Financial / Health Methods ───────────────────────────────

    def health_report(self, total_biaya=None, breakdown=None):
        """
        Semua angka health dalam 1 dict. Kirim total_biaya & breakdown kalau
        sudah dihitung (mis. dari aggregate di view) supaya tidak query ulang.
        Hanya biaya PERBAIKAN (corrective) yang dihitung — sama dengan with_health_annotations().
        """
        if total_biaya is None or breakdown is None:
            stats = self.maintenance_records.filter(maintenance_type='corrective').aggregate(
                total=Sum('biaya'), count=Count('id'),
            )
            total_biaya = stats['total'] if total_biaya is None else total_biaya
            breakdown   = stats['count'] if breakdown is None else breakdown
        total_biaya = total_biaya or Decimal('0')

        fin = self.financial if hasattr(self, 'financial') else None
        status, ratio = compute_health(total_biaya, breakdown, fin.nilai_awal_aset if fin else None)
        threshold     = get_repair_threshold(fin.nilai_awal_aset) if fin else None

        reasons = []
        if ratio is not None and threshold is not None:
            if ratio >= threshold['critical']:
                reasons.append(
                    f"Akumulasi biaya perbaikan ({ratio:.0f}%) sudah melewati batas wajar"
                )
            if total_biaya > fin.book_value() > 0:
                reasons.append("Total biaya perbaikan sudah melebihi nilai buku aset saat ini")

        if breakdown >= BREAKDOWN_WARNING:
            reasons.append(f"Sudah {breakdown}x mengalami kerusakan — tingkat keandalan rendah")
        elif breakdown >= 3:
            reasons.append(f"Sudah {breakdown}x mengalami kerusakan dalam riwayat operasional")

        return {
            'status': status, 'ratio': ratio,
            'threshold': threshold, 'recommendations': reasons,
        }

    def health_status(self):
        return self.health_report()['status']


# ================================================================
# ASSET PHOTO
# ================================================================

class AssetPhoto(models.Model):
    PHOTO_TYPE = [
        ('unit',     'Foto Unit — Tampilan fisik aset'),
        ('location', 'Foto Lokasi — Posisi penempatan aset'),
        ('damage',   'Foto Kerusakan — Dokumentasi kondisi bermasalah'),
        ('other',    'Lainnya'),
    ]

    # Tanpa index sendiri: sudah tercakup Index (asset, is_primary)
    asset       = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='photos', db_index=False)
    photo       = models.ImageField(upload_to='asset_photos/%Y/%m/')
    photo_type  = models.CharField(max_length=20, choices=PHOTO_TYPE, default='unit')
    caption     = models.CharField(max_length=200, blank=True)
    is_primary  = models.BooleanField(default=False)
    uploaded_by = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='uploaded_photos'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_primary', '-uploaded_at']
        # [INDEX 5] — filter is_primary sering dipakai
        indexes = [
            models.Index(fields=['asset', 'is_primary']),
        ]

    def __str__(self):
        return f"{self.asset.asset_number} — {self.get_photo_type_display()}"

    def save(self, *args, **kwargs):
        if self.is_primary:
            # Reset primary lain sebelum save — update() tanpa fetch loop
            AssetPhoto.objects.filter(
                asset=self.asset, is_primary=True
            ).exclude(pk=self.pk).update(is_primary=False)
        super().save(*args, **kwargs)


# ================================================================
# FINANCIAL
# ================================================================

class AssetFinancial(models.Model):
    asset           = models.OneToOneField(Asset, on_delete=models.CASCADE, related_name="financial")
    nilai_awal_aset = models.DecimalField(max_digits=15, decimal_places=2)
    nilai_residual  = models.DecimalField(max_digits=15, decimal_places=2)
    umur_ekonomis   = models.PositiveIntegerField(default=5)
    created_by      = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    def depresiasi_tahunan(self):
        if self.umur_ekonomis == 0:
            return Decimal('0')
        return (self.nilai_awal_aset - self.nilai_residual) / Decimal(self.umur_ekonomis)

    def _as_of(self, on=None):
        """Tanggal acuan penyusutan: hari ini, tapi berhenti di tanggal write off."""
        if on is None:
            on = date.today()
            if self.asset.lifecycle_status == 'writeoff' and hasattr(self.asset, 'write_off'):
                on = min(on, self.asset.write_off.tanggal)
        return on

    def umur_terpakai(self, on=None):
        days_elapsed   = max((self._as_of(on) - self.asset.tanggal_pembelian).days, 0)
        years_elapsed  = Decimal(days_elapsed) / Decimal('365.25')
        years_elapsed  = years_elapsed.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
        return min(years_elapsed, Decimal(self.umur_ekonomis))

    def sisa_umur_ekonomis(self):
        sisa = (Decimal(self.umur_ekonomis) - self.umur_terpakai()).quantize(
            Decimal('0.1'), rounding=ROUND_HALF_UP
        )
        return max(sisa, Decimal('0.00'))

    def book_value(self, on=None):
        """Nilai buku hari ini (atau pada tanggal `on`); aset write off berhenti di tanggal write off."""
        value = self.nilai_awal_aset - self.depresiasi_tahunan() * self.umur_terpakai(on)
        return max(value, self.nilai_residual)

    def __str__(self):
        return f"Financial - {self.asset.asset_number}"


class AssetWriteOff(models.Model):
    """
    Catatan resmi penghapusan aset. Dibuat lewat write_off_asset(), bukan dengan
    mengubah status langsung, supaya tanggal, alasan, dan nilai buku saat itu tersimpan.
    """
    REASON = [
        ('rusak',      'Rusak berat / tidak ekonomis diperbaiki'),
        ('usang',      'Usang / habis umur pakai'),
        ('hilang',     'Hilang atau dicuri'),
        ('dijual',     'Dijual / dilelang'),
        ('dihibahkan', 'Dihibahkan / didonasikan'),
        ('lainnya',    'Lainnya'),
    ]

    asset           = models.OneToOneField(Asset, on_delete=models.CASCADE, related_name='write_off')
    tanggal         = models.DateField()
    alasan          = models.CharField(max_length=20, choices=REASON)
    keterangan      = models.TextField()
    # Snapshot saat write off — tidak ikut berubah bila data finansial diedit belakangan
    nilai_perolehan = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    nilai_buku      = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    nilai_jual      = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    created_by      = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-tanggal', '-id']

    def __str__(self):
        return f"Write off {self.asset.asset_number} ({self.tanggal})"

    def gain_loss(self):
        """Laba (+) / rugi (−) pelepasan = hasil penjualan − nilai buku. None bila belum dinilai."""
        if self.nilai_buku is None:
            return None
        return (self.nilai_jual or Decimal('0')) - self.nilai_buku


# ================================================================
# ASSET COMPONENT (BOM)
# ================================================================

class AssetComponent(models.Model):
    # Tanpa index sendiri: sudah tercakup unique (asset, part_number)
    asset           = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='components', db_index=False)
    part_number     = models.CharField(max_length=100)
    component_name  = models.CharField(max_length=200)
    description     = models.TextField(blank=True)
    quantity        = models.PositiveIntegerField(default=1)
    is_critical     = models.BooleanField(default=False)
    supplier_name   = models.CharField(max_length=200, blank=True)
    estimated_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    last_replaced   = models.DateField(null=True, blank=True)
    notes           = models.TextField(blank=True)
    created_by      = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_components'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering      = ['-is_critical', 'component_name']
        unique_together = ('asset', 'part_number')

    def __str__(self):
        return f"{self.asset.asset_number} - {self.component_name}"

    def total_estimated_cost(self):
        return self.estimated_price * self.quantity if self.estimated_price else Decimal('0')


class ComponentReplacement(models.Model):
    component       = models.ForeignKey(AssetComponent, on_delete=models.CASCADE, related_name='replacements')
    replaced_date   = models.DateField()
    reason          = models.TextField()
    cost            = models.DecimalField(max_digits=12, decimal_places=2)
    old_part_number = models.CharField(max_length=100, blank=True)
    performed_by    = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-replaced_date']

    def __str__(self):
        return f"{self.component.component_name} - {self.replaced_date}"


# ================================================================
# MAINTENANCE ENGINE
# ================================================================

class AssetMaintenanceConfig(models.Model):
    asset                = models.OneToOneField(Asset, on_delete=models.CASCADE, related_name='maintenance_config')
    custom_interval_days = models.PositiveIntegerField()
    reason               = models.TextField()
    set_by               = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True)
    created_at           = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Custom config — {self.asset.asset_number} ({self.custom_interval_days} hari)"


class MaintenanceRecord(models.Model):
    TYPE = [
        ('preventive', 'Preventive — Terjadwal rutin'),
        ('corrective', 'Corrective — Perbaikan setelah rusak/laporan'),
        ('inspection', 'Inspection — Pengecekan visual/ringan'),
    ]
    RESULT = [
        ('ok',                'Baik — Tidak ada masalah'),
        ('minor_issue',       'Minor issue — Masih bisa beroperasi'),
        ('major_issue',       'Major issue — Perlu tindak lanjut segera'),
        ('replaced_component','Komponen diganti'),
    ]

    # Hasil yang dianggap "selesai" — dipindah ke konstanta agar mudah diubah/di-test
    RESOLVED_RESULTS = {'ok', 'minor_issue', 'replaced_component'}

    # Tanpa index sendiri: sudah tercakup Index (asset, …) di Meta
    asset                = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='maintenance_records',
                                             db_index=False)
    maintenance_type     = models.CharField(max_length=20, choices=TYPE)
    tanggal_pelaksanaan  = models.DateField()
    deskripsi_pekerjaan  = models.TextField()
    hasil                = models.CharField(max_length=30, choices=RESULT, default='ok')
    biaya                = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    catatan_tambahan     = models.TextField(blank=True)
    performed_by         = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='performed_maintenances'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-tanggal_pelaksanaan']
        # [INDEX 6, 7] — filter type & tanggal sangat sering dipakai
        indexes = [
            models.Index(fields=['asset', 'maintenance_type']),
            models.Index(fields=['asset', 'tanggal_pelaksanaan']),
        ]

    def __str__(self):
        return f"{self.asset.asset_number} — {self.get_maintenance_type_display()} — {self.tanggal_pelaksanaan}"

    @transaction.atomic
    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if self.maintenance_type == 'corrective' and self.hasil in self.RESOLVED_RESULTS:
            Asset.objects.filter(pk=self.asset_id).update(lifecycle_status='active')
        _generate_next_schedule(self.asset, base_date=self.tanggal_pelaksanaan)
        # Inspeksi/preventive yang menemukan masalah = temuan kerusakan → draf Berita Acara
        if is_new and self.maintenance_type != 'corrective' and self.hasil in ('minor_issue', 'major_issue'):
            # Catatan berisi temuannya; buang awalan siapa yang mengerjakan ("Dikerjakan vendor: X.")
            catatan = re.sub(r'^Dikerjakan vendor:[^.]*\.\s*', '', self.catatan_tambahan.strip()) or self.deskripsi_pekerjaan.strip()
            jenis = 'inspeksi' if self.maintenance_type == 'inspection' else 'maintenance preventive'
            hasil = self.get_hasil_display().split(' —')[0]
            catat_temuan_kerusakan(self.asset, 'inspeksi', f'{hasil} saat {jenis}: {catatan}',
                                   maintenance_record=self, by=self.performed_by)


class MaintenanceScheduleQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status__in=MaintenanceSchedule.ACTIVE_STATUS)


class MaintenanceSchedule(models.Model):
    STATUS = [
        ('upcoming', 'Akan Datang'),
        ('overdue',  'Terlambat'),
        ('done',     'Selesai'),
        ('skipped',  'Dilewati / Tidak Relevan'),
    ]
    # Jadwal yang masih berjalan — pakai .active() / konstanta ini, jangan tulis ulang daftarnya
    ACTIVE_STATUS = ('upcoming', 'overdue')

    # Tanpa index sendiri: sudah tercakup Index (asset, status)
    asset              = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='schedules', db_index=False)
    scheduled_date     = models.DateField()
    status             = models.CharField(max_length=10, choices=STATUS, default='upcoming')
    maintenance_record = models.OneToOneField(
        MaintenanceRecord, on_delete=models.SET_NULL, null=True, blank=True, related_name='schedule'
    )
    # Jadwal corrective yang lahir dari laporan kerusakan (null = jadwal preventive)
    damage_report = models.ForeignKey(
        'DamageReport', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='corrective_schedules',
    )
    notes      = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = MaintenanceScheduleQuerySet.as_manager()

    class Meta:
        ordering = ['scheduled_date']
        # [INDEX 8, 9] — paling sering di-filter di maintenance_list & dashboard
        indexes = [
            models.Index(fields=['status', 'scheduled_date']),
            models.Index(fields=['asset', 'status']),
        ]

    def days_remaining(self):
        return (self.scheduled_date - date.today()).days

    def __str__(self):
        return f"{self.asset.asset_number} — {self.scheduled_date} [{self.get_status_display()}]"


# ================================================================
# ASSET ASSIGNMENT
# ================================================================

class AssetAssignment(models.Model):
    """
    Pemegang aset (serah terima). Jangan dibuat/diubah langsung: pakai record_movement(),
    satu-satunya jalur yang menyamakan assignment aktif dengan AssetMovement.held_by terkini.
    """
    STATUS = [
        ('active',      'Aktif — Sedang digunakan'),
        ('returned',    'Dikembalikan'),
        ('transferred', 'Dipindahkan ke user lain'),
    ]

    # Tanpa index sendiri: sudah tercakup Index (asset, status) & (assigned_to, status)
    asset         = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='assignments', db_index=False)
    assigned_to   = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='held_assets', db_index=False)
    assigned_by   = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_assets'
    )
    assigned_date = models.DateField(default=date.today)
    returned_date = models.DateField(null=True, blank=True)
    status        = models.CharField(max_length=20, choices=STATUS, default='active')
    notes         = models.TextField(blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-assigned_date']
        # [INDEX 10] — filter asset + status aktif sangat sering
        indexes = [
            models.Index(fields=['asset', 'status']),
            models.Index(fields=['assigned_to', 'status']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['asset'], condition=Q(status='active'),
                                    name='uniq_active_assignment_per_asset'),
        ]

    def __str__(self):
        return f"{self.asset.asset_number} → {self.assigned_to.nama} ({self.get_status_display()})"


# ================================================================
# DAMAGE REPORT
# ================================================================

class DamageReport(models.Model):
    SEVERITY = [
        ('low',      'Ringan — Masih bisa digunakan'),
        ('medium',   'Sedang — Fungsi terganggu sebagian'),
        ('high',     'Parah — Tidak bisa digunakan sama sekali'),
        ('critical', 'Kritis — Membahayakan / darurat'),
    ]
    STATUS = [
        ('pending',      'Menunggu Tindak Lanjut'),
        ('acknowledged', 'Sudah Diterima GA'),
        ('in_progress',  'Sedang Diperbaiki'),
        ('resolved',     'Selesai Diperbaiki'),
        ('rejected',     'Ditolak / Tidak Valid'),
    ]
    # Laporan yang masih berjalan — pakai konstanta ini, jangan tulis ulang daftarnya
    ACTIVE_STATUS = ('pending', 'acknowledged', 'in_progress')

    # Tanpa index sendiri: sudah tercakup Index (asset, report_status)
    asset       = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='damage_reports', db_index=False)
    reported_by = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, related_name='submitted_damage_reports'
    )
    title       = models.CharField(max_length=200)
    description = models.TextField()
    severity    = models.CharField(max_length=10, choices=SEVERITY, default='medium')
    photo_evidence = models.ImageField(upload_to='damage_reports/%Y/%m/', null=True, blank=True)

    report_status = models.CharField(max_length=20, choices=STATUS, default='pending')
    handled_by    = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='handled_damage_reports'
    )
    ga_notes = models.TextField(blank=True)
    resolved_maintenance = models.OneToOneField(
        MaintenanceRecord, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='damage_report_source'
    )

    reported_at = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-reported_at']
        # [INDEX 11, 12]
        indexes = [
            models.Index(fields=['report_status', 'reported_at']),
            models.Index(fields=['asset', 'report_status']),
        ]

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.asset.asset_number} — {self.title}"

    def is_active(self):
        return self.report_status in self.ACTIVE_STATUS

    @transaction.atomic
    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            self._handle_new_report()

    def _handle_new_report(self):
        """
        Dipecah dari save() supaya lebih mudah di-test secara unit
        dan logic-nya lebih terbaca.
        """
        # 1. Aset → status maintenance
        Asset.objects.filter(pk=self.asset_id).update(lifecycle_status='maintenance')

        # 2. Skip jadwal preventive yang ada
        self.asset.schedules.filter(status='upcoming').update(status='skipped')

        # 3. Buat jadwal corrective urgent
        MaintenanceSchedule.objects.create(
            asset=self.asset,
            scheduled_date=date.today(),
            status='overdue',
            damage_report=self,
            notes=(
                f"[CORRECTIVE - Laporan Kerusakan #{self.pk}] {self.title} "
                f"(Dilaporkan oleh: {self.reported_by.nama if self.reported_by else 'Unknown'}, "
                f"Severity: {self.get_severity_display()})"
            )
        )

        # 4. Temuan kerusakan → draf Berita Acara Kerusakan departemen pemilik aset
        catat_temuan_kerusakan(self.asset, 'laporan', ringkas_laporan(self.title, self.description), damage_report=self)


# ================================================================
# MAINTENANCE ENGINE — HELPERS
# ================================================================

def _generate_next_schedule(asset, base_date=None):
    interval_days = asset.get_effective_interval_days()
    if not interval_days:
        return None

    interval_days = int(interval_days)

    if base_date is None:
        last = asset.maintenance_records.order_by('-tanggal_pelaksanaan').first()
        base_date = last.tanggal_pelaksanaan if last else asset.tanggal_pembelian

    next_date = base_date + timedelta(days=interval_days)

    # Hapus hanya upcoming preventive (bukan yang dari damage report)
    asset.schedules.filter(status='upcoming', damage_report__isnull=True).delete()

    return MaintenanceSchedule.objects.create(
        asset=asset,
        scheduled_date=next_date,
        status='upcoming',
    )


UPCOMING_SOON_DAYS = 7


def schedule_status(sch, today=None):
    """Status jadwal aktif terdekat → 'overdue' | 'upcoming_soon' | 'on_track' ('not_configured' bila tidak ada)."""
    if sch is None:
        return 'not_configured'
    if sch.status == 'overdue':
        return 'overdue'
    if (sch.scheduled_date - (today or date.today())).days <= UPCOMING_SOON_DAYS:
        return 'upcoming_soon'
    return 'on_track'


def nearest_active_schedules(assets):
    """{asset_id: jadwal aktif terdekat} untuk banyak aset sekaligus (1 query)."""
    nearest = {}
    for sch in (MaintenanceSchedule.objects.active().filter(asset__in=assets)
                .order_by('scheduled_date').only('asset_id', 'status', 'scheduled_date')):
        nearest.setdefault(sch.asset_id, sch)
    return nearest


def attach_maintenance_status(assets):
    """
    Hitung maintenance_status() untuk banyak aset sekaligus (1 query jadwal).
    Aset sebaiknya sudah select_related('asset_category', 'maintenance_config').
    """
    assets  = list(assets)
    nearest = nearest_active_schedules(assets)
    today   = date.today()
    for asset in assets:
        asset._maintenance_status_cache = (
            schedule_status(nearest.get(asset.pk), today)
            if asset.get_effective_interval_days() else 'not_configured'
        )
    return assets


def initialize_schedule_for_asset(asset):
    return _generate_next_schedule(asset, base_date=asset.tanggal_pembelian)


def refresh_overdue_schedules():
    """Bulk update: upcoming → overdue untuk yang sudah lewat tanggal."""
    return MaintenanceSchedule.objects.filter(
        status='upcoming',
        scheduled_date__lt=date.today(),
    ).update(status='overdue')


def refresh_overdue_schedules_daily():
    """
    Versi hemat untuk dipanggil dari view GET: UPDATE hanya jalan
    sekali per hari per proses, bukan di setiap page load.
    """
    from django.core.cache import cache
    key = f"overdue_refreshed:{date.today().isoformat()}"
    if cache.add(key, True, timeout=60 * 60 * 24):
        refresh_overdue_schedules()


# ================================================================
# ASSET MOVEMENT
# ================================================================

class AssetMovement(models.Model):
    """
    Histori perpindahan aset: siapa memegang, di departemen mana,
    di lokasi mana, dan kapan.

    Setiap kali aset berpindah tangan / lokasi, buat 1 record baru.
    Record lama (is_current=True) di-close dulu dengan moved_out diisi.
    """

    # Tanpa index sendiri: sudah tercakup Index (asset, …) di Meta
    asset       = models.ForeignKey(
        'Asset', on_delete=models.CASCADE, related_name='movements', db_index=False
    )
    # Pemegang
    held_by     = models.ForeignKey(
        'Employee', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='asset_movements'
    )
    department  = models.ForeignKey(
        'Department', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='asset_movements'
    )
    # Lokasi fisik saat dipindah ke sini
    location    = models.ForeignKey(
        'Location', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='asset_movements'
    )

    # Waktu masuk & keluar dari titik ini
    moved_in    = models.DateTimeField(default=timezone.now)
    moved_out   = models.DateTimeField(null=True, blank=True)

    # Flag record aktif — hanya 1 per aset yang True
    is_current  = models.BooleanField(default=True)

    # Opsional: catatan pemindahan
    notes       = models.TextField(blank=True)

    # Siapa yang mencatat movement ini (GA yang input)
    recorded_by = models.ForeignKey(
        'Employee', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='recorded_movements'
    )
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-moved_in']
        indexes  = [
            models.Index(fields=['asset', 'is_current']),
            models.Index(fields=['asset', 'moved_in']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['asset'], condition=Q(is_current=True),
                                    name='uniq_current_movement_per_asset'),
        ]

    def __str__(self):
        holder = self.held_by.nama if self.held_by else '—'
        loc    = self.location.name if self.location else '—'
        return f"{self.asset.asset_number} → {holder} @ {loc} ({self.moved_in:%d %b %Y})"

    @property
    def duration_display(self):
        """Berapa lama aset di titik ini."""
        end   = self.moved_out or timezone.now()
        delta = end - self.moved_in
        days  = delta.days
        if days == 0:    return "Hari ini"
        if days < 30:    return f"{days} hari"
        if days < 365:   return f"{days // 30} bulan"
        return f"{days // 365} tahun {(days % 365) // 30} bulan"


# ── Helper: buat movement baru & tutup yang lama ─────────────────
def sync_holder(asset, held_by, by=None, tanggal=None, notes=''):
    """
    Satu-satunya tempat AssetAssignment berubah: samakan assignment aktif aset dengan
    pemegang movement terkini (held_by). Pemegang sama → dibiarkan; berbeda → yang lama
    ditutup ('transferred' bila ada penerima baru, 'returned' bila tidak) dan yang baru dibuka.
    """
    tanggal = tanggal or date.today()
    current = AssetAssignment.objects.filter(asset=asset, status='active').first()
    if current and current.assigned_to_id == getattr(held_by, 'pk', None):
        return current
    if current:
        current.status        = 'transferred' if held_by else 'returned'
        current.returned_date = tanggal
        if notes and not held_by:
            current.notes = f'{current.notes}\n[Dikembalikan: {notes}]'.strip()
        current.save(update_fields=['status', 'returned_date', 'notes'])
    if held_by is None:
        return None
    return AssetAssignment.objects.create(
        asset=asset, assigned_to=held_by, assigned_by=by, assigned_date=tanggal, notes=notes,
    )


@transaction.atomic
def record_movement(asset, held_by, department, location, recorded_by=None, notes='',
                    tanggal=None, holder_notes=None):
    """
    Panggil ini setiap kali aset berpindah tangan / lokasi.
    Otomatis menutup record lama, membuat yang baru, dan menyamakan assignment (sync_holder).
    `tanggal` = tanggal serah terima/pengembalian; `holder_notes` = catatan assignment
    (default sama dengan `notes`).
    """
    now = timezone.now()

    # Tutup semua record is_current yang ada
    AssetMovement.objects.filter(asset=asset, is_current=True).update(
        is_current=False,
        moved_out=now,
    )

    # Lokasi di master aset ikut posisi terbaru supaya list aset & movement konsisten
    if location is not None:
        Asset.objects.filter(pk=asset.pk).update(lokasi_asset=location)

    # Buat record baru
    movement = AssetMovement.objects.create(
        asset=asset,
        held_by=held_by,
        department=department,
        location=location,
        moved_in=now,
        is_current=True,
        notes=notes,
        recorded_by=recorded_by,
    )
    sync_holder(asset, held_by, by=recorded_by, tanggal=tanggal,
                notes=notes if holder_notes is None else holder_notes)
    return movement


@transaction.atomic
def write_off_asset(asset, tanggal, alasan, keterangan, by=None, nilai_jual=None):
    """
    Write off resmi: simpan catatan + nilai buku saat itu, lalu tutup semua yang
    masih berjalan (jadwal, pemegang, laporan kerusakan) dan catat di histori movement.
    """
    fin = asset.financial if hasattr(asset, 'financial') else None
    wo = AssetWriteOff.objects.create(
        asset=asset, tanggal=tanggal, alasan=alasan, keterangan=keterangan, nilai_jual=nilai_jual,
        nilai_perolehan=fin.nilai_awal_aset if fin else None,
        nilai_buku=fin.book_value(tanggal) if fin else None,
        created_by=by,
    )
    Asset.objects.filter(pk=asset.pk).update(lifecycle_status='writeoff')
    asset.lifecycle_status = 'writeoff'

    active = asset.schedules.active()
    active.filter(notes='').update(notes=f'Dihentikan: aset di-write off {tanggal:%d %b %Y}')
    active.update(status='skipped')
    DamageReport.objects.filter(asset=asset, report_status__in=DamageReport.ACTIVE_STATUS).update(
        report_status='resolved', resolved_at=timezone.now(), handled_by=by,
        ga_notes='Ditutup karena aset di-write off.',
    )
    # Pemegang aktif ikut ditutup (returned per tanggal write off) lewat record_movement
    record_movement(asset, held_by=None, department=asset.owned_by_department, location=asset.lokasi_asset,
                    recorded_by=by, notes=f'Write off: {wo.get_alasan_display()}', tanggal=tanggal, holder_notes='')
    return wo


@transaction.atomic
def cancel_write_off(asset, by=None):
    """Batalkan write off yang keliru: aset aktif lagi dan jadwal maintenance dibuat ulang."""
    AssetWriteOff.objects.filter(asset=asset).delete()
    Asset.objects.filter(pk=asset.pk).update(lifecycle_status='active')
    asset.lifecycle_status = 'active'
    _generate_next_schedule(asset)
    record_movement(asset, held_by=None, department=asset.owned_by_department, location=asset.lokasi_asset,
                    recorded_by=by, notes='Write off dibatalkan')
    # BA Penghapusan yang sudah bernomor tetap disimpan sebagai dokumen; pembatalannya dicatat
    for ba in BeritaAcara.objects.filter(jenis='penghapusan', items__asset=asset):
        baris = f'{date.today():%d/%m/%Y}: write off {asset.asset_number} dibatalkan oleh {by.nama if by else "GA"}.'
        ba.catatan = f'{ba.catatan}\n{baris}' if ba.catatan else baris
        ba.save(update_fields=['catatan', 'updated_at'])


# ================================================================
# BERITA ACARA — kerusakan & penghapusan aset
# ================================================================
# Alur: setiap temuan kerusakan (laporan karyawan, inspeksi/maintenance bermasalah,
# atau ditambah GA) otomatis masuk DRAF BA Kerusakan per departemen pemilik aset.
# GA meninjau draf → terbitkan (bernomor) → cetak & tanda tangan basah → tandai
# ditandatangani → aset berekomendasi "hapus" di-write off sekaligus dan terbit
# BA Penghapusan yang merujuk nomor BA Kerusakan.

# Kode di nomor BA: 001/BIB/-HRGA/BA/II/2026 (urutan per tahun, dipakai bersama kedua jenis BA)
BA_NOMOR_FORMAT = '{urutan:03d}/BIB/-HRGA/BA/{bulan_romawi}/{tahun}'
BA_KOTA = 'Angsana'
BULAN_ROMAWI = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII']
NAMA_HARI = ['Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu', 'Minggu']
NAMA_BULAN = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus',
              'September', 'Oktober', 'November', 'Desember']


def tanggal_panjang(d):
    """date(2026, 2, 5) → "5 Februari 2026"."""
    return f'{d.day} {NAMA_BULAN[d.month - 1]} {d.year}'


class PenandaTanganBA(models.Model):
    """Lembar pengesahan BA (diatur sekali oleh GA); disalin ke setiap BA saat terbit."""
    urutan  = models.PositiveSmallIntegerField(default=0)
    peran   = models.CharField(max_length=50)      # "Dibuat oleh", "Disetujui oleh", …
    nama    = models.CharField(max_length=100)
    jabatan = models.CharField(max_length=100)

    class Meta:
        ordering = ['urutan', 'id']

    def __str__(self):
        return f'{self.peran}: {self.nama} ({self.jabatan})'


class BeritaAcara(models.Model):
    JENIS = [
        ('kerusakan',   'Berita Acara Kerusakan Asset'),
        ('penghapusan', 'Berita Acara Penghapusan Asset'),
    ]
    STATUS = [
        ('draft',          'Draf'),
        ('terbit',         'Terbit, menunggu tanda tangan'),
        ('ditandatangani', 'Ditandatangani'),
    ]

    jenis      = models.CharField(max_length=12, choices=JENIS)
    status     = models.CharField(max_length=15, choices=STATUS, default='draft')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='berita_acara')
    nomor      = models.CharField(max_length=60, unique=True, null=True, blank=True)
    urutan     = models.PositiveIntegerField(null=True, blank=True)
    tanggal    = models.DateField(null=True, blank=True)       # tanggal verifikasi / terbit
    isi        = models.TextField(blank=True)                   # paragraf pembuka; kosong = teks baku
    rujukan    = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='turunan')      # BA Penghapusan → BA Kerusakan
    penanda_tangan = models.JSONField(default=list, blank=True) # salinan saat terbit
    scan_ttd   = models.FileField(upload_to='berita_acara/%Y/', null=True, blank=True)
    catatan    = models.TextField(blank=True)                   # catatan internal (mis. write off dibatalkan)
    dibuat_oleh      = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    diterbitkan_oleh = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    diterbitkan_at     = models.DateTimeField(null=True, blank=True)
    ditandatangani_at  = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-tanggal', '-id']
        indexes = [models.Index(fields=['jenis', 'status'])]

    def __str__(self):
        return self.nomor or f'Draf {self.get_jenis_display()} — {self.department or "tanpa departemen"}'

    @property
    def judul(self):
        return self.get_jenis_display().upper()

    def isi_baku(self, tanggal=None):
        """Paragraf pembuka baku; dipakai bila GA tidak menulis sendiri."""
        d = tanggal or self.tanggal or date.today()
        hari = f'{NAMA_HARI[d.weekday()]} tanggal {tanggal_panjang(d)}'
        dept = self.department.name if self.department else 'GA'
        if self.jenis == 'penghapusan':
            dasar = f', berdasarkan Berita Acara Kerusakan Asset No: {self.rujukan.nomor}' if self.rujukan_id else ''
            return (f'Pada hari ini {hari}{dasar}, telah dilakukan penghapusan asset-asset dari Departemen {dept} '
                    f'dari daftar asset PT BIB karena tidak dapat digunakan lagi, adapun daftar asset yang dimaksud adalah:')
        rekom = set(self.items.values_list('rekomendasi', flat=True))
        if rekom == {'hapus'}:
            return (f'Pada hari ini {hari}, telah dilakukan verifikasi terhadap asset-asset yang rusak dari Departemen {dept}. '
                    f'Asset rusak tersebut tidak dapat diperbaiki lagi atau biaya perbaikannya tidak ekonomis lagi, '
                    f'adapun daftar asset yang dimaksud adalah:')
        return (f'Pada hari ini {hari}, telah dilakukan verifikasi terhadap asset-asset yang rusak dari Departemen {dept} '
                f'berdasarkan laporan karyawan dan hasil inspeksi rutin. Adapun daftar asset yang dimaksud '
                f'beserta rekomendasi tindak lanjutnya adalah:')

    @property
    def isi_final(self):
        return self.isi.strip() or self.isi_baku()

    @property
    def tanggal_tempat(self):
        return f'{BA_KOTA}, {tanggal_panjang(self.tanggal or date.today())}'

    def baris_dokumen(self):
        """
        Baris tabel dokumen: aset dengan nama, PO, kerusakan & rekomendasi yang sama
        digabung jadi satu baris "N unit", seperti BA manual.
        """
        rows = {}
        acuan = self.tanggal or date.today()
        for it in self.items.select_related('asset', 'asset__write_off').order_by('id'):
            a = it.asset
            po = a.no_po if a.no_po and a.no_po.strip(' -') else a.asset_number
            key = (a.nama_asset, po, it.kerusakan.strip(), it.rekomendasi)
            row = rows.setdefault(key, {
                'nama': a.nama_asset, 'po': po, 'tahun': a.tanggal_pembelian.year,
                'usia': selisih_tahun_bulan(a.tanggal_pembelian, acuan)[0],
                'jumlah': 0, 'kerusakan': it.kerusakan.strip(), 'rekomendasi': it.get_rekomendasi_display(),
                'nilai_perolehan': Decimal(0), 'nilai_buku': Decimal(0), 'ada_nilai': False, 'nomor_aset': [],
            })
            row['jumlah'] += 1
            row['nomor_aset'].append(a.asset_number)
            wo = a.write_off if hasattr(a, 'write_off') else None
            if wo and wo.nilai_perolehan is not None:
                row['nilai_perolehan'] += wo.nilai_perolehan
                row['nilai_buku'] += wo.nilai_buku or 0
                row['ada_nilai'] = True
        return list(rows.values())

    def lampiran_foto(self):
        """Dokumentasi: foto yang diunggah di BA, foto bukti laporan, lalu foto kerusakan aset."""
        out = []
        for it in self.items.select_related('asset', 'damage_report').prefetch_related('foto', 'asset__photos'):
            for f in it.foto.all():
                out.append({'url': f.foto.url, 'caption': f.caption or it.asset.nama_asset})
            if it.damage_report and it.damage_report.photo_evidence:
                out.append({'url': it.damage_report.photo_evidence.url, 'caption': it.asset.nama_asset})
            for p in it.asset.photos.all():
                if p.photo_type == 'damage':
                    out.append({'url': p.photo.url, 'caption': p.caption or it.asset.nama_asset})
        return out


class BeritaAcaraItem(models.Model):
    SUMBER = [
        ('laporan',  'Laporan karyawan'),
        ('inspeksi', 'Temuan inspeksi / maintenance'),
        ('manual',   'Ditambahkan GA'),
    ]
    REKOMENDASI = [
        ('perbaiki', 'Diperbaiki'),
        ('hapus',    'Dihapuskan'),
    ]

    # Tanpa index sendiri: sudah tercakup unique (ba, asset)
    ba     = models.ForeignKey(BeritaAcara, on_delete=models.CASCADE, related_name='items', db_index=False)
    asset  = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='ba_items')
    sumber = models.CharField(max_length=10, choices=SUMBER, default='manual')
    damage_report      = models.ForeignKey(DamageReport, on_delete=models.SET_NULL, null=True, blank=True, related_name='ba_items')
    maintenance_record = models.ForeignKey(MaintenanceRecord, on_delete=models.SET_NULL, null=True, blank=True, related_name='ba_items')
    kerusakan   = models.TextField(blank=True)
    rekomendasi = models.CharField(max_length=10, choices=REKOMENDASI, default='perbaiki')
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        constraints = [models.UniqueConstraint(fields=['ba', 'asset'], name='uniq_ba_asset')]

    def __str__(self):
        return f'{self.ba} — {self.asset.asset_number}'


class BeritaAcaraFoto(models.Model):
    item    = models.ForeignKey(BeritaAcaraItem, on_delete=models.CASCADE, related_name='foto')
    foto    = models.ImageField(upload_to='berita_acara/foto/%Y/%m/')
    caption = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


def ringkas_laporan(title, description):
    """Judul + deskripsi laporan tanpa mengulang judul bila deskripsi sudah memuatnya."""
    title, description = (title or '').strip(), (description or '').strip()
    if not description or description.lower().startswith(title.lower().rstrip('.')):
        return description or title
    return f'{title} — {description}'


def draf_ba_kerusakan(department, by=None):
    """Draf BA Kerusakan yang sedang terbuka untuk departemen ini (dibuat bila belum ada)."""
    ba = BeritaAcara.objects.filter(jenis='kerusakan', status='draft', department=department).first()
    return ba or BeritaAcara.objects.create(jenis='kerusakan', department=department, dibuat_oleh=by)


def catat_temuan_kerusakan(asset, sumber, kerusakan, damage_report=None, maintenance_record=None, by=None):
    """
    Masukkan temuan kerusakan ke draf BA departemen pemilik aset. Aset yang sudah ada
    di draf tidak digandakan; keterangan kerusakannya ditambahkan.
    """
    if asset.lifecycle_status == 'writeoff':
        return None
    ba = draf_ba_kerusakan(asset.owned_by_department, by=by)
    item = ba.items.filter(asset=asset).first()
    kerusakan = (kerusakan or '').strip()
    if item:
        if kerusakan and kerusakan not in item.kerusakan:
            item.kerusakan = f'{item.kerusakan}; {kerusakan}' if item.kerusakan else kerusakan
        item.damage_report = item.damage_report or damage_report
        item.maintenance_record = item.maintenance_record or maintenance_record
        item.save()
        return item
    return BeritaAcaraItem.objects.create(ba=ba, asset=asset, sumber=sumber, kerusakan=kerusakan,
                                          damage_report=damage_report, maintenance_record=maintenance_record)


def salinan_penanda_tangan():
    return [{'peran': p.peran, 'nama': p.nama, 'jabatan': p.jabatan} for p in PenandaTanganBA.objects.all()]


def nomor_ba_berikutnya(tanggal):
    terakhir = (BeritaAcara.objects.filter(urutan__isnull=False, tanggal__year=tanggal.year)
                .order_by('-urutan').values_list('urutan', flat=True).first()) or 0
    urutan = terakhir + 1
    return urutan, BA_NOMOR_FORMAT.format(urutan=urutan, bulan_romawi=BULAN_ROMAWI[tanggal.month - 1], tahun=tanggal.year)


@transaction.atomic
def terbitkan_ba(ba, tanggal, by=None):
    """Beri nomor urut tahunan, salin lembar pengesahan, ubah status jadi terbit."""
    ba.urutan, ba.nomor = nomor_ba_berikutnya(tanggal)
    ba.tanggal = tanggal
    ba.isi = ba.isi.strip() or ba.isi_baku(tanggal)
    ba.penanda_tangan = salinan_penanda_tangan()
    ba.status = 'terbit'
    ba.diterbitkan_oleh = by
    ba.diterbitkan_at = timezone.now()
    ba.save()
    return ba


@transaction.atomic
def hapuskan_aset(assets, tanggal, alasan, keterangan, by=None, rujukan=None, nilai_jual=None):
    """Write off sekumpulan aset lalu terbitkan satu BA Penghapusan (merujuk BA Kerusakan bila ada)."""
    assets = list(assets)
    for a in assets:
        write_off_asset(a, tanggal, alasan, keterangan, by=by, nilai_jual=nilai_jual if len(assets) == 1 else None)
    depts = {a.owned_by_department_id for a in assets}
    ba = BeritaAcara.objects.create(
        jenis='penghapusan', rujukan=rujukan, dibuat_oleh=by,
        department=assets[0].owned_by_department if len(depts) == 1 else None,
    )
    asal = {it.asset_id: it for it in rujukan.items.all()} if rujukan else {}
    for a in assets:
        src = asal.get(a.pk)
        BeritaAcaraItem.objects.create(ba=ba, asset=a, sumber='manual', rekomendasi='hapus',
                                       kerusakan=(src.kerusakan if src else keterangan),
                                       damage_report=src.damage_report if src else None)
    return terbitkan_ba(ba, tanggal, by=by)
