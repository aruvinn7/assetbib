
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
REPAIR_THRESHOLD_TIERS = [
    (2_000_000,  {'warning': 20, 'waspada': 40, 'critical': 60}),
    (10_000_000, {'warning': 30, 'waspada': 55, 'critical': 75}),
    (50_000_000, {'warning': 40, 'waspada': 65, 'critical': 85}),
    (None,       {'warning': 50, 'waspada': 75, 'critical': 100}),  # > 50jt
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


# ================================================================
# ABSTRACT BASE MODELS
# ================================================================

class TimestampedModel(models.Model):
    """
    Mixin: tambahkan created_at + updated_at ke semua model yang butuh.
    Sebelumnya field ini copy-paste di tiap model.
    """
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class CreatedByModel(models.Model):
    """
    Mixin: tambahkan created_by + created_at.
    Dipakai oleh Asset, AssetFinancial, AssetComponent, dll.
    """
    created_by = models.ForeignKey(
        'Employee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


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

    class Meta:
        # [INDEX 1] — sering di-filter by department di views assignment
        indexes = [
            models.Index(fields=['department']),
        ]

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

    group = models.ForeignKey(AssetGroup, on_delete=models.CASCADE, related_name='categories')
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


# ================================================================
# ASSET QUERYSET & MANAGER
# ================================================================

class AssetQuerySet(models.QuerySet):
    """
    Query logic yang tadinya berulang di views, dipindah ke sini.
    Manfaat: bisa di-chain, di-test unit, dan reusable.

    Contoh pemakaian di view:
        Asset.objects.active().with_health_data()
        Asset.objects.unvalued()
        Asset.objects.with_stats()
    """

    def active(self):
        return self.filter(lifecycle_status='active')

    def in_maintenance(self):
        return self.filter(lifecycle_status='maintenance')

    def unvalued(self):
        """Aset yang belum punya data finansial"""
        return self.filter(financial__isnull=True)

    def with_health_data(self):
        """
        Prefetch semua data yang dibutuhkan health_status() & repair_cost_ratio().
        Panggil ini sebelum loop yang akses health_status() agar tidak N+1.
        """
        return self.select_related(
            'financial', 'asset_category', 'maintenance_config'
        ).prefetch_related('maintenance_records', 'components')

    def with_list_data(self):
        """Optimized untuk halaman list — join semua relasi tampilan"""
        return self.select_related(
            'lokasi_asset', 'owned_by_department', 'asset_group',
            'asset_category', 'financial', 'maintenance_config',
        )

    def with_detail_data(self):
        """Optimized untuk halaman detail — semua relasi + prefetch"""
        from django.db.models import Prefetch
        return self.select_related(
            'lokasi_asset', 'owned_by_department', 'asset_group',
            'asset_category', 'financial', 'maintenance_config',
        ).prefetch_related(
            Prefetch('photos',    queryset=AssetPhoto.objects.order_by('-is_primary')),
            Prefetch('schedules', queryset=MaintenanceSchedule.objects.order_by('scheduled_date')),
            Prefetch('maintenance_records',
                     queryset=MaintenanceRecord.objects.select_related('performed_by')
                                                      .order_by('-tanggal_pelaksanaan')),
            'components',
        )

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
    asset_group       = models.ForeignKey(AssetGroup,  on_delete=models.SET_NULL, null=True, blank=True)
    asset_category    = models.ForeignKey(
        AssetCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='assets'
    )
    lifecycle_status  = models.CharField(max_length=20, choices=STATUS, default='active')
    created_by        = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    objects = AssetManager()  # Custom manager

    class Meta:
        # [INDEX 2, 3, 4] — kolom paling sering dipakai sebagai filter di views
        indexes = [
            models.Index(fields=['lifecycle_status']),
            models.Index(fields=['lifecycle_status', 'asset_category']),
            models.Index(fields=['asset_group', 'lifecycle_status']),
        ]

    # ── Properties ──────────────────────────────────────────────

    @property
    def umur_asset(self):
        return (date.today() - self.tanggal_pembelian).days // 365

    @property
    def umur_asset_detail(self):
        today  = date.today()
        years  = today.year  - self.tanggal_pembelian.year
        months = today.month - self.tanggal_pembelian.month
        if months < 0:
            years  -= 1
            months += 12
        return {"years": years, "months": months}

    def __str__(self):
        return f"{self.asset_number} - {self.nama_asset}"

    # ── Helper Methods ───────────────────────────────────────────

    def has_financial(self):
        return hasattr(self, 'financial')

    def get_current_holder(self):
        return AssetAssignment.objects.filter(
            asset=self, status='active'
        ).select_related('assigned_to').first()

    def get_effective_interval_days(self):
        if hasattr(self, 'maintenance_config'):
            return self.maintenance_config.custom_interval_days
        if self.asset_category:
            return self.asset_category.default_interval_in_days()
        return None

    def get_next_schedule(self):
        return self.schedules.filter(status='upcoming').order_by('scheduled_date').first()

    def get_last_maintenance(self):
        return self.maintenance_records.order_by('-tanggal_pelaksanaan').first()

    def get_pending_damage_report(self):
        return self.damage_reports.filter(
            report_status__in=['pending', 'acknowledged', 'in_progress']
        ).first()

    def maintenance_status(self):
        # Sudah dihitung massal oleh attach_maintenance_status() → 0 query
        if hasattr(self, '_maintenance_status_cache'):
            return self._maintenance_status_cache

        if not self.get_effective_interval_days():
            return 'not_configured'

        schedule = self.schedules.filter(
            status__in=['upcoming', 'overdue']
        ).order_by('scheduled_date').first()

        if not schedule:
            return 'not_configured'
        if schedule.status == 'overdue':
            return 'overdue'
        if (schedule.scheduled_date - date.today()).days <= 7:
            return 'upcoming_soon'
        return 'on_track'

    # ── Financial / Health Methods ───────────────────────────────

    def total_biaya_perbaikan(self):
        """Biaya perbaikan (corrective) saja — dasar rasio kesehatan."""
        result = self.maintenance_records.filter(maintenance_type='corrective').aggregate(total=Sum('biaya'))
        return result['total'] or Decimal('0')

    def breakdown_count(self):
        return self.maintenance_records.filter(maintenance_type='corrective').count()

    def _nilai_awal(self):
        return self.financial.nilai_awal_aset if hasattr(self, 'financial') else None

    def get_repair_threshold(self):
        if not hasattr(self, 'financial'):
            return None
        return get_repair_threshold(self.financial.nilai_awal_aset)

    def health_report(self, total_biaya=None, breakdown=None):
        """
        Semua angka health dalam 1 dict. Kirim total_biaya & breakdown kalau
        sudah dihitung (mis. dari aggregate di view) supaya tidak query ulang.
        """
        if total_biaya is None:
            total_biaya = self.total_biaya_perbaikan()
        if breakdown is None:
            breakdown = self.breakdown_count()
        total_biaya = total_biaya or Decimal('0')

        status, ratio = compute_health(total_biaya, breakdown, self._nilai_awal())
        threshold     = self.get_repair_threshold()

        reasons = []
        if ratio is not None and threshold is not None:
            if ratio >= threshold['critical']:
                reasons.append(
                    f"Akumulasi biaya perbaikan ({ratio:.0f}%) sudah melewati batas wajar"
                )
            if total_biaya > self.financial.book_value() > 0:
                reasons.append("Total biaya perbaikan sudah melebihi nilai buku aset saat ini")

        if breakdown >= BREAKDOWN_WARNING:
            reasons.append(f"Sudah {breakdown}x mengalami kerusakan — tingkat keandalan rendah")
        elif breakdown >= 3:
            reasons.append(f"Sudah {breakdown}x mengalami kerusakan dalam riwayat operasional")

        return {
            'status': status, 'ratio': ratio,
            'threshold': threshold, 'recommendations': reasons,
        }

    def repair_cost_ratio(self):
        return self.health_report()['ratio']

    def health_status(self):
        return self.health_report()['status']

    def repair_recommendations(self):
        return self.health_report()['recommendations']


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

    asset       = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='photos')
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
    asset           = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='components')
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

    asset                = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='maintenance_records')
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
        super().save(*args, **kwargs)
        if self.maintenance_type == 'corrective' and self.hasil in self.RESOLVED_RESULTS:
            Asset.objects.filter(pk=self.asset_id).update(lifecycle_status='active')
        _generate_next_schedule(self.asset, base_date=self.tanggal_pelaksanaan)


class MaintenanceScheduleQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status__in=['upcoming', 'overdue'])

    def overdue(self):
        return self.filter(status='overdue')

    def upcoming(self):
        return self.filter(status='upcoming')

    def for_asset(self, asset):
        return self.filter(asset=asset)

    def mark_overdue(self):
        """Bulk update upcoming → overdue untuk yang sudah lewat tanggal"""
        return self.filter(
            status='upcoming', scheduled_date__lt=date.today()
        ).update(status='overdue')


class MaintenanceSchedule(models.Model):
    STATUS = [
        ('upcoming', 'Akan Datang'),
        ('overdue',  'Terlambat'),
        ('done',     'Selesai'),
        ('skipped',  'Dilewati / Tidak Relevan'),
    ]

    asset              = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='schedules')
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

    def is_overdue(self):
        return self.status == 'upcoming' and self.scheduled_date < date.today()

    def __str__(self):
        return f"{self.asset.asset_number} — {self.scheduled_date} [{self.get_status_display()}]"


# ================================================================
# ASSET ASSIGNMENT
# ================================================================

class AssetAssignment(models.Model):
    STATUS = [
        ('active',      'Aktif — Sedang digunakan'),
        ('returned',    'Dikembalikan'),
        ('transferred', 'Dipindahkan ke user lain'),
    ]

    asset         = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='assignments')
    assigned_to   = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='held_assets')
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

    # Lookup color dipindah ke dict konstanta — DRY, tidak scatter di property
    SEVERITY_COLOR = {
        'low': 'success', 'medium': 'warning', 'high': 'danger', 'critical': 'dark',
    }
    STATUS_COLOR = {
        'pending': 'warning', 'acknowledged': 'info', 'in_progress': 'primary',
        'resolved': 'success', 'rejected': 'secondary',
    }

    asset       = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='damage_reports')
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

    @property
    def severity_color(self):
        return self.SEVERITY_COLOR.get(self.severity, 'secondary')

    @property
    def status_color(self):
        return self.STATUS_COLOR.get(self.report_status, 'secondary')

    def is_active(self):
        return self.report_status in {'pending', 'acknowledged', 'in_progress'}

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


def attach_maintenance_status(assets):
    """
    Hitung maintenance_status() untuk banyak aset sekaligus (1 query jadwal).
    Aset sebaiknya sudah select_related('asset_category', 'maintenance_config').
    """
    assets = list(assets)
    nearest = {}
    for sch in (MaintenanceSchedule.objects
                .filter(asset__in=assets, status__in=['upcoming', 'overdue'])
                .order_by('scheduled_date').only('asset_id', 'status', 'scheduled_date')):
        nearest.setdefault(sch.asset_id, sch)

    today = date.today()
    for asset in assets:
        sch = nearest.get(asset.pk)
        if not asset.get_effective_interval_days() or not sch:
            status = 'not_configured'
        elif sch.status == 'overdue':
            status = 'overdue'
        elif (sch.scheduled_date - today).days <= 7:
            status = 'upcoming_soon'
        else:
            status = 'on_track'
        asset._maintenance_status_cache = status
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

    asset       = models.ForeignKey(
        'Asset', on_delete=models.CASCADE, related_name='movements'
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
        if days < 365:   return f"{days // 30} bln"
        return f"{days // 365} thn {(days % 365) // 30} bln"


# ── Helper: buat movement baru & tutup yang lama ─────────────────
@transaction.atomic
def record_movement(asset, held_by, department, location, recorded_by=None, notes=''):
    """
    Panggil ini setiap kali aset berpindah.
    Otomatis menutup record lama dan membuat yang baru.
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
    return AssetMovement.objects.create(
        asset=asset,
        held_by=held_by,
        department=department,
        location=location,
        moved_in=now,
        is_current=True,
        notes=notes,
        recorded_by=recorded_by,
    )


@transaction.atomic
def write_off_asset(asset, tanggal, alasan, keterangan, by=None, nilai_jual=None):
    """
    Write off resmi: simpan catatan + nilai buku saat itu, lalu tutup semua yang
    masih berjalan (jadwal, pemegang, laporan kerusakan) dan catat di histori movement.
    """
    fin = getattr(asset, 'financial', None) if hasattr(asset, 'financial') else None
    wo = AssetWriteOff.objects.create(
        asset=asset, tanggal=tanggal, alasan=alasan, keterangan=keterangan, nilai_jual=nilai_jual,
        nilai_perolehan=fin.nilai_awal_aset if fin else None,
        nilai_buku=fin.book_value(tanggal) if fin else None,
        created_by=by,
    )
    Asset.objects.filter(pk=asset.pk).update(lifecycle_status='writeoff')
    asset.lifecycle_status = 'writeoff'

    active = asset.schedules.filter(status__in=['upcoming', 'overdue'])
    active.filter(notes='').update(notes=f'Dihentikan: aset di-write off {tanggal:%d %b %Y}')
    active.update(status='skipped')
    AssetAssignment.objects.filter(asset=asset, status='active').update(status='returned', returned_date=tanggal)
    DamageReport.objects.filter(asset=asset, report_status__in=['pending', 'acknowledged', 'in_progress']).update(
        report_status='resolved', resolved_at=timezone.now(), handled_by=by,
        ga_notes='Ditutup karena aset di-write off.',
    )
    record_movement(asset, held_by=None, department=asset.owned_by_department, location=asset.lokasi_asset,
                    recorded_by=by, notes=f'Write off: {wo.get_alasan_display()}')
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
