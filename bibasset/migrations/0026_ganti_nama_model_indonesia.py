# Nama kelas model disamakan ke bahasa Indonesia.
# RenameModel memindahkan tabel beserta datanya; nama tabel akhir diatur di migrasi berikutnya.
# Urutan: model yang punya FK ke model lain di-rename lebih dulu daripada model yang dirujuknya,
# supaya rollback (urutan terbalik) tidak mencari model yang sudah dikembalikan namanya.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('bibasset', '0025_berkas_media'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RenameModel('GAProfile', 'ProfilGA'),
        migrations.RenameModel('FinanceProfile', 'ProfilFinance'),
        migrations.RenameModel('UserProfile', 'ProfilKaryawan'),
        migrations.RenameModel('AssetPhoto', 'FotoAset'),
        migrations.RenameModel('AssetFinancial', 'FinansialAset'),
        migrations.RenameModel('AssetWriteOff', 'PenghapusanAset'),
        migrations.RenameModel('ComponentReplacement', 'PenggantianKomponen'),
        migrations.RenameModel('AssetComponent', 'KomponenAset'),
        migrations.RenameModel('AssetMaintenanceConfig', 'KonfigurasiMaintenance'),
        migrations.RenameModel('MaintenanceSchedule', 'JadwalMaintenance'),
        migrations.RenameModel('BeritaAcaraFoto', 'FotoBeritaAcara'),
        migrations.RenameModel('BeritaAcaraItem', 'ButirBeritaAcara'),
        migrations.RenameModel('DamageReport', 'LaporanKerusakan'),
        migrations.RenameModel('MaintenanceRecord', 'CatatanMaintenance'),
        migrations.RenameModel('AssetAssignment', 'SerahTerimaAset'),
        migrations.RenameModel('MovementReport', 'LaporanPerpindahan'),
        migrations.RenameModel('AssetMovement', 'PerpindahanAset'),
        migrations.RenameModel('Asset', 'Aset'),
        migrations.RenameModel('Employee', 'Karyawan'),
        migrations.RenameModel('Department', 'Departemen'),
        migrations.RenameModel('Location', 'Lokasi'),
        migrations.RenameModel('AssetCategory', 'KategoriAset'),
        migrations.RenameModel('AssetGroup', 'GrupAset'),
    ]
