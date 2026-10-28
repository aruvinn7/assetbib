# forms.py — validasi input semua form.
# Nama field mengikuti atribut name="" di template yang sudah ada,
# jadi template tidak perlu diubah. Kalau nama input HTML beda dengan
# nama field model, petakan lewat `aliases`.
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

from .models import (
    Asset, AssetAssignment, AssetCategory, AssetComponent, AssetFinancial, AssetGroup,
    AssetMaintenanceConfig, AssetWriteOff, ComponentReplacement, DamageReport, Department,
    Employee, FinanceProfile, GAProfile, Location, MaintenanceRecord, UserProfile,
)

MAX_UPLOAD_SIZE = 5 * 1024 * 1024


def flash_form_errors(request, form):
    """
    Kirim error form lewat messages. Error per field diberi extra_tags
    "field:<nama input HTML>|<label>" supaya base.html bisa menempelkannya
    tepat di bawah isiannya; error tanpa isian tampil sebagai pesan biasa.
    """
    html_names = {field: html for html, field in getattr(form, 'aliases', {}).items()}
    for name, errors in form.errors.items():
        if name not in form.fields:
            for err in errors:
                messages.error(request, err)
            continue
        tag = f"field:{html_names.get(name, name)}|{form[name].label}"
        for err in errors:
            messages.error(request, err, extra_tags=tag)


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
            'lokasi_asset', 'owned_by_department', 'asset_category',
        ]
        labels = {
            'no_po': 'No. PO', 'asset_number': 'Nomor aset', 'nama_asset': 'Nama aset',
            'tanggal_pembelian': 'Tanggal pembelian', 'lokasi_asset': 'Lokasi',
            'owned_by_department': 'Departemen',
            'asset_category': 'Kategori aset',
        }
        # LANGUAGE_CODE masih en-us, jadi pesan bawaan Django ditulis ulang di sini
        error_messages = {
            'asset_number': {'unique': 'Nomor aset ini sudah dipakai aset lain.'},
        }


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
        tanggal = self.cleaned_data['tanggal']
        if tanggal > timezone.localdate():
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
        fields = ['group', 'name', 'personal_use', 'default_interval_value', 'default_interval_unit',
                  'maintenance_description']
        labels = {'group': 'Grup', 'name': 'Nama kategori', 'default_interval_value': 'Interval',
                  'personal_use': 'Bisa diserahkan pribadi'}

    def clean_maintenance_description(self):
        """Checklist: satu langkah per baris, dinomori ulang otomatis (1. … 2. …)."""
        import re
        lines = [re.sub(r'^\s*\d+\s*[.)]\s*', '', l).strip()
                 for l in (self.cleaned_data.get('maintenance_description') or '').splitlines()]
        return '\n'.join(f'{i}. {l}' for i, l in enumerate((l for l in lines if l), 1))


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ['name', 'parent', 'is_storage']
        labels = {'name': 'Nama lokasi', 'parent': 'Induk lokasi', 'is_storage': 'Tempat titip GA'}

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
    """Serah terima: ke karyawan (kategori pemakaian pribadi saja) atau atas nama departemen."""
    MODE = [('personal', 'Pribadi'), ('department', 'Departemen')]

    asset_id      = forms.ModelChoiceField(Asset.objects.filter(lifecycle_status='active')
                                           .select_related('asset_category'), label='Aset')
    mode          = forms.ChoiceField(choices=MODE, label='Diserahkan ke')
    assigned_to   = forms.ModelChoiceField(Employee.objects.filter(user_profile__isnull=False, user__is_active=True),
                                           required=False, label='Karyawan')
    department    = forms.ModelChoiceField(Department.objects.all(), required=False, label='Departemen')
    location      = forms.ModelChoiceField(Location.objects.filter(is_storage=False), label='Lokasi pemakaian')
    assigned_date = forms.DateField(required=False, label='Tanggal serah terima')
    notes         = forms.CharField(required=False)

    def clean(self):
        data = super().clean()
        asset, mode = data.get('asset_id'), data.get('mode')
        if mode == 'personal':
            if not data.get('assigned_to'):
                self.add_error('assigned_to', 'Pilih karyawan penerima.')
            category = asset.asset_category if asset else None
            if asset and not (category and category.personal_use):
                self.add_error('mode', f"{category.name if category else 'Aset tanpa kategori'} dipakai bersama; "
                                       "serahkan atas nama departemen.")
        elif mode == 'department' and not data.get('department'):
            self.add_error('department', 'Pilih departemen penerima.')
        return data


class AssignmentReturnForm(forms.Form):
    location      = forms.ModelChoiceField(Location.objects.all(), label='Disimpan di')
    condition     = forms.ChoiceField(choices=AssetAssignment.CONDITION, label='Kondisi saat diterima')
    returned_date = forms.DateField(required=False, label='Tanggal kembali')
    notes         = forms.CharField(required=False, label='Catatan')

    def clean(self):
        data = super().clean()
        data['notes'] = (data.get('notes') or '').strip()
        if data.get('condition', 'baik') != 'baik' and not data['notes']:
            self.add_error('notes', 'Jelaskan kerusakannya; catatan ini jadi laporan kerusakan.')
        return data


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


# ── Karyawan & akun login (khusus GA) ─────────────────────────────
# Akses tidak eksklusif: GA/Finance bisa sekaligus "Karyawan" agar bisa memegang aset & melapor kerusakan.
ACCESS_PROFILES = {'ga': GAProfile, 'finance': FinanceProfile, 'user': UserProfile}
ACCESS_CHOICES = [('ga', 'GA'), ('finance', 'Finance'), ('user', 'Karyawan')]
# Tanpa karakter yang mudah tertukar (l/1, O/0)
PASSWORD_CHARS = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def employee_access(emp):
    return [key for key in ACCESS_PROFILES if hasattr(emp, f'{key}_profile')]


def generate_password():
    return get_random_string(10, PASSWORD_CHARS)


def check_password_rules(password, username=''):
    """Aturan password sederhana dengan pesan berbahasa Indonesia. Return pesan error atau None."""
    if len(password) < 8:
        return 'Password minimal 8 karakter.'
    if password.isdigit():
        return 'Password tidak boleh hanya angka.'
    if username and password.lower() == username.lower():
        return 'Password tidak boleh sama dengan username.'
    return None


class EmployeeForm(forms.ModelForm):
    username = forms.CharField(label='Username', max_length=150)
    access   = forms.MultipleChoiceField(label='Akses', choices=ACCESS_CHOICES)
    password = forms.CharField(label='Password', required=False, strip=False)

    class Meta:
        model = Employee
        fields = ['nama', 'employee_id', 'department', 'jabatan']
        labels = {'nama': 'Nama lengkap', 'employee_id': 'ID karyawan', 'department': 'Departemen', 'jabatan': 'Jabatan'}

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor  # Employee GA yang sedang mengisi form
        self.generated_password = None
        if self.instance.pk:
            del self.fields['password']  # password diatur lewat form tersendiri

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        for validator in User._meta.get_field('username').validators:
            try:
                validator(username)
            except forms.ValidationError:
                raise forms.ValidationError('Username hanya boleh huruf, angka, dan @ . + - _ tanpa spasi.')
        taken = User.objects.filter(username__iexact=username)
        if self.instance.pk:
            taken = taken.exclude(pk=self.instance.user_id)
        if taken.exists():
            raise forms.ValidationError('Username sudah dipakai akun lain.')
        return username

    def clean_access(self):
        access = self.cleaned_data['access']
        # GA tidak boleh mencabut akses GA miliknya sendiri (bisa terkunci dari halaman ini)
        if self.editor and self.instance.pk == self.editor.pk and 'ga' not in access:
            raise forms.ValidationError('Anda tidak bisa mencabut akses GA akun Anda sendiri.')
        # Hanya akses Karyawan yang bisa memegang aset
        if self.instance.pk and 'user' not in access and hasattr(self.instance, 'user_profile'):
            held = self.instance.held_assets.filter(status='active').count()
            if held:
                raise forms.ValidationError(f'Masih memegang {held} aset. Kembalikan asetnya dulu sebelum mencabut akses Karyawan.')
        return access

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('password')
        if 'password' in self.fields and password:
            error = check_password_rules(password, cleaned.get('username', ''))
            if error:
                self.add_error('password', error)
        return cleaned

    @transaction.atomic
    def save(self):
        emp = super().save(commit=False)
        username = self.cleaned_data['username']
        if emp.pk:
            user = emp.user
            if user.username != username:
                user.username = username
                user.save(update_fields=['username'])
        else:
            password = self.cleaned_data.get('password')
            if not password:
                password = self.generated_password = generate_password()
            emp.user = User.objects.create_user(username=username, password=password)
        emp.save()
        access = self.cleaned_data['access']
        for key, model in ACCESS_PROFILES.items():
            if key in access:
                model.objects.get_or_create(employee=emp)
            else:
                model.objects.filter(employee=emp).delete()
        return emp
