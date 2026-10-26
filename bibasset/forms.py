# forms.py — validasi input semua form.
# Nama field mengikuti atribut name="" di template yang sudah ada,
# jadi template tidak perlu diubah. Kalau nama input HTML beda dengan
# nama field model, petakan lewat `aliases`.
from decimal import Decimal

from django import forms
from django.contrib import messages

from .models import (
    Asset, AssetCategory, AssetComponent, AssetFinancial, AssetGroup,
    AssetMaintenanceConfig, AssetWriteOff, ComponentReplacement, DamageReport, Department,
    Employee, Location, MaintenanceRecord,
)

MAX_UPLOAD_SIZE = 5 * 1024 * 1024


def flash_form_errors(request, form):
    """Tampilkan error form sebagai flash message (template belum render error per field)."""
    for name, errors in form.errors.items():
        label = form.fields[name].label if name in form.fields else ''
        for err in errors:
            messages.error(request, f"{label}: {err}" if label else err)


class AliasModelForm(forms.ModelForm):
    aliases = {}  # {nama input HTML: nama field model}

    def __init__(self, data=None, *args, **kwargs):
        if data is not None and self.aliases:
            data = data.copy()
            for html_name, field_name in self.aliases.items():
                if html_name in data and field_name not in data:
                    data[field_name] = data[html_name]
        super().__init__(data, *args, **kwargs)


# ── Asset ────────────────────────────────────────────────────────

class AssetForm(AliasModelForm):
    aliases = {'department': 'owned_by_department'}

    class Meta:
        model = Asset
        fields = [
            'no_po', 'asset_number', 'nama_asset', 'tanggal_pembelian',
            'lokasi_asset', 'owned_by_department', 'asset_group', 'asset_category',
        ]
        labels = {
            'no_po': 'No. PO', 'asset_number': 'Nomor aset', 'nama_asset': 'Nama aset',
            'tanggal_pembelian': 'Tanggal pembelian', 'lokasi_asset': 'Lokasi',
            'owned_by_department': 'Departemen', 'asset_group': 'Grup aset',
            'asset_category': 'Kategori aset',
        }

    def clean(self):
        cleaned = super().clean()
        group, category = cleaned.get('asset_group'), cleaned.get('asset_category')
        if group and category and category.group_id != group.pk:
            self.add_error('asset_category', f"Kategori '{category.name}' bukan bagian dari grup '{group.name}'.")
        return cleaned


class AssetUpdateForm(AssetForm):
    # Status tidak diubah dari form edit: 'maintenance' diatur otomatis oleh laporan kerusakan,
    # 'writeoff' lewat alur write off resmi (WriteOffForm).
    pass


class WriteOffForm(forms.ModelForm):
    konfirmasi = forms.BooleanField(
        required=True, error_messages={'required': 'Centang konfirmasi untuk melanjutkan.'})

    class Meta:
        model = AssetWriteOff
        fields = ['tanggal', 'alasan', 'keterangan', 'nilai_jual']
        labels = {'tanggal': 'Tanggal write off', 'alasan': 'Alasan', 'keterangan': 'Keterangan',
                  'nilai_jual': 'Hasil penjualan'}

    def __init__(self, *args, asset=None, **kwargs):
        self.asset = asset
        super().__init__(*args, **kwargs)
        self.fields['nilai_jual'].required = False

    def clean_tanggal(self):
        from datetime import date
        tanggal = self.cleaned_data['tanggal']
        if tanggal > date.today():
            raise forms.ValidationError('Tidak boleh di masa depan.')
        if self.asset and tanggal < self.asset.tanggal_pembelian:
            raise forms.ValidationError('Tidak boleh sebelum tanggal pembelian aset.')
        return tanggal

    def clean_keterangan(self):
        text = self.cleaned_data['keterangan'].strip()
        if len(text) < 10:
            raise forms.ValidationError('Jelaskan kondisi aset dan dasar keputusannya (minimal 10 karakter).')
        return text

    def clean(self):
        data = super().clean()
        jual = data.get('nilai_jual')
        if data.get('alasan') != 'dijual':
            data['nilai_jual'] = None
        elif jual is not None and jual < 0:
            self.add_error('nilai_jual', 'Tidak boleh negatif.')
        return data


# ── Financial ────────────────────────────────────────────────────

class AssetFinancialForm(AliasModelForm):
    aliases = {'nilai_awal': 'nilai_awal_aset'}

    class Meta:
        model = AssetFinancial
        fields = ['nilai_awal_aset', 'nilai_residual', 'umur_ekonomis']
        labels = {
            'nilai_awal_aset': 'Nilai awal', 'nilai_residual': 'Nilai residual',
            'umur_ekonomis': 'Umur ekonomis',
        }

    def clean(self):
        cleaned = super().clean()
        awal, residual = cleaned.get('nilai_awal_aset'), cleaned.get('nilai_residual')
        if awal is not None and awal <= 0:
            self.add_error('nilai_awal_aset', 'Harus lebih dari 0.')
        if residual is not None and residual < 0:
            self.add_error('nilai_residual', 'Tidak boleh negatif.')
        if awal is not None and residual is not None and residual > awal:
            self.add_error('nilai_residual', 'Tidak boleh lebih besar dari nilai awal.')
        if cleaned.get('umur_ekonomis') == 0:
            self.add_error('umur_ekonomis', 'Minimal 1 tahun.')
        return cleaned


# ── Component ────────────────────────────────────────────────────

class AssetComponentForm(forms.ModelForm):
    class Meta:
        model = AssetComponent
        fields = [
            'part_number', 'component_name', 'description', 'quantity', 'is_critical',
            'supplier_name', 'estimated_price', 'last_replaced', 'notes',
        ]
        labels = {'part_number': 'Part number', 'component_name': 'Nama komponen', 'quantity': 'Jumlah'}

    def clean_part_number(self):
        # unique_together (asset, part_number) tidak divalidasi otomatis karena asset bukan field form
        pn = self.cleaned_data['part_number']
        dup = AssetComponent.objects.filter(asset_id=self.instance.asset_id, part_number=pn) \
                                    .exclude(pk=self.instance.pk)
        if dup.exists():
            raise forms.ValidationError(f"Part number '{pn}' sudah ada di aset ini.")
        return pn


class ComponentReplacementForm(forms.ModelForm):
    class Meta:
        model = ComponentReplacement
        fields = ['replaced_date', 'reason', 'cost', 'old_part_number']
        labels = {'replaced_date': 'Tanggal ganti', 'reason': 'Alasan', 'cost': 'Biaya'}


# ── Maintenance ──────────────────────────────────────────────────

class MaintenanceRecordForm(forms.ModelForm):
    class Meta:
        model = MaintenanceRecord
        fields = [
            'maintenance_type', 'tanggal_pelaksanaan', 'deskripsi_pekerjaan',
            'hasil', 'biaya', 'catatan_tambahan',
        ]
        labels = {
            'maintenance_type': 'Tipe maintenance', 'tanggal_pelaksanaan': 'Tanggal pelaksanaan',
            'deskripsi_pekerjaan': 'Deskripsi pekerjaan', 'hasil': 'Hasil', 'biaya': 'Biaya',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['biaya'].required = False

    def clean_biaya(self):
        biaya = self.cleaned_data.get('biaya') or Decimal('0')
        if biaya < 0:
            raise forms.ValidationError('Tidak boleh negatif.')
        return biaya


class ResolveDamageForm(MaintenanceRecordForm):
    """Form GA saat menyelesaikan laporan kerusakan (tipe selalu corrective)."""
    class Meta(MaintenanceRecordForm.Meta):
        fields = ['tanggal_pelaksanaan', 'deskripsi_pekerjaan', 'hasil', 'biaya', 'catatan_tambahan']


class MaintenanceConfigForm(forms.ModelForm):
    class Meta:
        model = AssetMaintenanceConfig
        fields = ['custom_interval_days', 'reason']
        labels = {'custom_interval_days': 'Interval (hari)', 'reason': 'Alasan'}

    def clean_custom_interval_days(self):
        days = self.cleaned_data['custom_interval_days']
        if days < 1:
            raise forms.ValidationError('Minimal 1 hari.')
        return days


# ── Master data ──────────────────────────────────────────────────

class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['name']
        labels = {'name': 'Nama departemen'}


class AssetGroupForm(forms.ModelForm):
    class Meta:
        model = AssetGroup
        fields = ['name']
        labels = {'name': 'Nama grup'}


class AssetCategoryForm(forms.ModelForm):
    class Meta:
        model = AssetCategory
        fields = ['group', 'name', 'default_interval_value', 'default_interval_unit', 'maintenance_description']
        labels = {'group': 'Grup', 'name': 'Nama kategori', 'default_interval_value': 'Interval'}


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ['name', 'parent']
        labels = {'name': 'Nama lokasi', 'parent': 'Induk lokasi'}

    def clean_parent(self):
        parent = self.cleaned_data.get('parent')
        if parent and self.instance.pk:
            # Cegah siklus: induk tidak boleh diri sendiri atau turunannya
            node = parent
            while node:
                if node.pk == self.instance.pk:
                    raise forms.ValidationError('Induk tidak boleh lokasi ini sendiri atau sub-lokasinya.')
                node = node.parent
        return parent


# ── Assignment, movement, damage report ──────────────────────────

class AssignmentForm(forms.Form):
    asset_id      = forms.ModelChoiceField(Asset.objects.filter(lifecycle_status='active'), label='Aset')
    assigned_to   = forms.ModelChoiceField(Employee.objects.filter(user_profile__isnull=False), label='Penerima')
    assigned_date = forms.DateField(required=False, label='Tanggal serah terima')
    notes         = forms.CharField(required=False)


class MovementForm(forms.Form):
    held_by    = forms.ModelChoiceField(Employee.objects.all(), required=False, label='Pemegang')
    department = forms.ModelChoiceField(Department.objects.all(), required=False, label='Departemen')
    location   = forms.ModelChoiceField(Location.objects.all(), required=False, label='Lokasi')
    notes      = forms.CharField(required=False)

    def clean_notes(self):
        return self.cleaned_data['notes'].strip()


class DamageReportForm(forms.ModelForm):
    class Meta:
        model = DamageReport
        fields = ['title', 'description', 'severity', 'photo_evidence']
        labels = {'title': 'Judul', 'description': 'Deskripsi', 'severity': 'Tingkat kerusakan',
                  'photo_evidence': 'Foto bukti'}

    def clean_photo_evidence(self):
        photo = self.cleaned_data.get('photo_evidence')
        if photo and getattr(photo, 'size', 0) > MAX_UPLOAD_SIZE:
            raise forms.ValidationError('Ukuran foto maksimal 5 MB.')
        return photo
