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
    Aset, SerahTerimaAset, KategoriAset, KomponenAset, FinansialAset, GrupAset,
    KonfigurasiMaintenance, PenghapusanAset, PenggantianKomponen, LaporanKerusakan, Departemen,
    Karyawan, ProfilFinance, ProfilGA, Lokasi, CatatanMaintenance, ProfilKaryawan,
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


# ── Aset ────────────────────────────────────────────────────────

class AssetForm(AliasModelForm):
    aliases = {'department': 'departemen_pemilik'}

    class Meta:
        model = Aset
        fields = [
            'nomor_po', 'nomor_aset', 'nama_aset', 'tanggal_pembelian',
            'lokasi', 'departemen_pemilik', 'kategori_aset',
        ]
        labels = {
            'nomor_po': 'No. PO', 'nomor_aset': 'Nomor aset', 'nama_aset': 'Nama aset',
            'tanggal_pembelian': 'Tanggal pembelian', 'lokasi': 'Lokasi',
            'departemen_pemilik': 'Departemen',
            'kategori_aset': 'Kategori aset',
        }
        # LANGUAGE_CODE masih en-us, jadi pesan bawaan Django ditulis ulang di sini
        error_messages = {
            'nomor_aset': {'unique': 'Nomor aset ini sudah dipakai aset lain.'},
        }


class AssetUpdateForm(AssetForm):
    # Status tidak diubah dari form edit: 'maintenance' diatur otomatis oleh laporan kerusakan,
    # 'writeoff' lewat alur write off resmi (WriteOffForm).
    pass


class WriteOffForm(forms.ModelForm):
    konfirmasi = forms.BooleanField(
        required=True, error_messages={'required': 'Centang konfirmasi untuk melanjutkan.'})

    class Meta:
        model = PenghapusanAset
        fields = ['tanggal', 'alasan', 'keterangan', 'nilai_jual']
        labels = {'tanggal': 'Tanggal write off', 'alasan': 'Alasan', 'keterangan': 'Keterangan',
                  'nilai_jual': 'Hasil penjualan'}

    def __init__(self, *args, aset=None, **kwargs):
        self.aset = aset
        super().__init__(*args, **kwargs)
        self.fields['nilai_jual'].required = False

    def clean_tanggal(self):
        tanggal = self.cleaned_data['tanggal']
        if tanggal > timezone.localdate():
            raise forms.ValidationError('Tidak boleh di masa depan.')
        if self.aset and tanggal < self.aset.tanggal_pembelian:
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
    aliases = {'nilai_awal': 'nilai_awal'}

    class Meta:
        model = FinansialAset
        fields = ['nilai_awal', 'nilai_residual', 'umur_ekonomis']
        labels = {
            'nilai_awal': 'Nilai awal', 'nilai_residual': 'Nilai residual',
            'umur_ekonomis': 'Umur ekonomis',
        }

    def clean(self):
        cleaned = super().clean()
        awal, residual = cleaned.get('nilai_awal'), cleaned.get('nilai_residual')
        if awal is not None and awal <= 0:
            self.add_error('nilai_awal', 'Harus lebih dari 0.')
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
        model = KomponenAset
        fields = [
            'nomor_part', 'nama_komponen', 'deskripsi', 'jumlah', 'komponen_kritis',
            'nama_pemasok', 'harga_estimasi', 'penggantian_terakhir', 'catatan',
        ]
        labels = {'nomor_part': 'Part number', 'nama_komponen': 'Nama komponen', 'jumlah': 'Jumlah'}

    def clean_nomor_part(self):
        # unique_together (aset, nomor_part) tidak divalidasi otomatis karena aset bukan field form
        pn = self.cleaned_data['nomor_part']
        dup = KomponenAset.objects.filter(aset_id=self.instance.aset_id, nomor_part=pn) \
                                    .exclude(pk=self.instance.pk)
        if dup.exists():
            raise forms.ValidationError(f"Part number '{pn}' sudah ada di aset ini.")
        return pn


class ComponentReplacementForm(forms.ModelForm):
    class Meta:
        model = PenggantianKomponen
        fields = ['tanggal_penggantian', 'alasan', 'biaya', 'nomor_part_lama']
        labels = {'tanggal_penggantian': 'Tanggal ganti', 'alasan': 'Alasan', 'biaya': 'Biaya'}


# ── Maintenance ──────────────────────────────────────────────────

class MaintenanceRecordForm(forms.ModelForm):
    class Meta:
        model = CatatanMaintenance
        fields = [
            'jenis_maintenance', 'tanggal_pelaksanaan', 'deskripsi_pekerjaan',
            'hasil', 'biaya', 'catatan',
        ]
        labels = {
            'jenis_maintenance': 'Tipe maintenance', 'tanggal_pelaksanaan': 'Tanggal pelaksanaan',
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
        fields = ['tanggal_pelaksanaan', 'deskripsi_pekerjaan', 'hasil', 'biaya', 'catatan']


class MaintenanceConfigForm(forms.ModelForm):
    class Meta:
        model = KonfigurasiMaintenance
        fields = ['interval_khusus_hari', 'alasan']
        labels = {'interval_khusus_hari': 'Interval (hari)', 'alasan': 'Alasan'}

    def clean_interval_khusus_hari(self):
        days = self.cleaned_data['interval_khusus_hari']
        if days < 1:
            raise forms.ValidationError('Minimal 1 hari.')
        return days


# ── Master data ──────────────────────────────────────────────────

class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Departemen
        fields = ['nama']
        labels = {'nama': 'Nama departemen'}


class AssetGroupForm(forms.ModelForm):
    class Meta:
        model = GrupAset
        fields = ['nama']
        labels = {'nama': 'Nama grup'}


class AssetCategoryForm(forms.ModelForm):
    class Meta:
        model = KategoriAset
        fields = ['grup', 'nama', 'pemakaian_pribadi', 'interval_default', 'satuan_interval_default',
                  'deskripsi_maintenance']
        labels = {'grup': 'Grup', 'nama': 'Nama kategori', 'interval_default': 'Interval',
                  'pemakaian_pribadi': 'Bisa diserahkan pribadi'}

    def clean_deskripsi_maintenance(self):
        """Checklist: satu langkah per baris, dinomori ulang otomatis (1. … 2. …)."""
        import re
        lines = [re.sub(r'^\s*\d+\s*[.)]\s*', '', l).strip()
                 for l in (self.cleaned_data.get('deskripsi_maintenance') or '').splitlines()]
        return '\n'.join(f'{i}. {l}' for i, l in enumerate((l for l in lines if l), 1))


class LocationForm(forms.ModelForm):
    class Meta:
        model = Lokasi
        fields = ['nama', 'induk', 'tempat_penyimpanan']
        labels = {'nama': 'Nama lokasi', 'induk': 'Induk lokasi', 'tempat_penyimpanan': 'Tempat titip GA'}

    def clean_induk(self):
        induk = self.cleaned_data.get('induk')
        if induk and self.instance.pk:
            # Cegah siklus: induk tidak boleh diri sendiri atau turunannya
            node = induk
            while node:
                if node.pk == self.instance.pk:
                    raise forms.ValidationError('Induk tidak boleh lokasi ini sendiri atau sub-lokasinya.')
                node = node.induk
        return induk


# ── Assignment, movement, damage report ──────────────────────────

class AssignmentForm(forms.Form):
    """Serah terima: ke karyawan (kategori pemakaian pribadi saja) atau atas nama departemen."""
    MODE = [('personal', 'Pribadi'), ('department', 'Departemen')]

    aset_id      = forms.ModelChoiceField(Aset.objects.filter(status_siklus_hidup='active')
                                           .select_related('kategori_aset'), label='Aset')
    mode          = forms.ChoiceField(choices=MODE, label='Diserahkan ke')
    diserahkan_kepada   = forms.ModelChoiceField(Karyawan.objects.filter(user_profile__isnull=False, user__is_active=True),
                                           required=False, label='Karyawan')
    departemen    = forms.ModelChoiceField(Departemen.objects.all(), required=False, label='Departemen')
    lokasi      = forms.ModelChoiceField(Lokasi.objects.filter(tempat_penyimpanan=False), label='Lokasi pemakaian')
    tanggal_serah = forms.DateField(required=False, label='Tanggal serah terima')
    catatan       = forms.CharField(required=False)

    def clean(self):
        data = super().clean()
        asset, mode = data.get('aset_id'), data.get('mode')
        if mode == 'personal':
            if not data.get('diserahkan_kepada'):
                self.add_error('diserahkan_kepada', 'Pilih karyawan penerima.')
            category = asset.kategori_aset if asset else None
            if asset and not (category and category.pemakaian_pribadi):
                self.add_error('mode', f"{category.nama if category else 'Aset tanpa kategori'} dipakai bersama; "
                                       "serahkan atas nama departemen.")
        elif mode == 'department' and not data.get('departemen'):
            self.add_error('departemen', 'Pilih departemen penerima.')
        return data


class AssignmentReturnForm(forms.Form):
    lokasi      = forms.ModelChoiceField(Lokasi.objects.all(), label='Disimpan di')
    kondisi       = forms.ChoiceField(choices=SerahTerimaAset.CONDITION, label='Kondisi saat diterima')
    tanggal_kembali = forms.DateField(required=False, label='Tanggal kembali')
    catatan       = forms.CharField(required=False, label='Catatan')

    def clean(self):
        data = super().clean()
        data['catatan'] = (data.get('catatan') or '').strip()
        if data.get('kondisi', 'baik') != 'baik' and not data['catatan']:
            self.add_error('catatan', 'Jelaskan kerusakannya; catatan ini jadi laporan kerusakan.')
        return data


class MovementForm(forms.Form):
    dipegang_oleh    = forms.ModelChoiceField(Karyawan.objects.all(), required=False, label='Pemegang')
    departemen = forms.ModelChoiceField(Departemen.objects.all(), required=False, label='Departemen')
    lokasi   = forms.ModelChoiceField(Lokasi.objects.all(), required=False, label='Lokasi')
    catatan    = forms.CharField(required=False)

    def clean_catatan(self):
        return self.cleaned_data['catatan'].strip()


class DamageReportForm(forms.ModelForm):
    class Meta:
        model = LaporanKerusakan
        fields = ['judul', 'deskripsi', 'tingkat_keparahan', 'foto_bukti']
        labels = {'judul': 'Judul', 'deskripsi': 'Deskripsi', 'tingkat_keparahan': 'Tingkat kerusakan',
                  'foto_bukti': 'Foto bukti'}

    def clean_foto_bukti(self):
        foto = self.cleaned_data.get('foto_bukti')
        if foto and getattr(foto, 'size', 0) > MAX_UPLOAD_SIZE:
            raise forms.ValidationError('Ukuran foto maksimal 5 MB.')
        return foto


# ── Karyawan & akun login (khusus GA) ─────────────────────────────
# Akses tidak eksklusif: GA/Finance bisa sekaligus "Karyawan" agar bisa memegang aset & melapor kerusakan.
ACCESS_PROFILES = {'ga': ProfilGA, 'finance': ProfilFinance, 'user': ProfilKaryawan}
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
        model = Karyawan
        fields = ['nama', 'nomor_karyawan', 'departemen', 'jabatan']
        labels = {'nama': 'Nama lengkap', 'nomor_karyawan': 'ID karyawan', 'departemen': 'Departemen', 'jabatan': 'Jabatan'}

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor  # Karyawan GA yang sedang mengisi form
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
