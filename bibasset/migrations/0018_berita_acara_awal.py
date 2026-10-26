import re
from datetime import date, datetime, time, timedelta

from django.db import migrations
from django.utils import timezone

# Peran & jabatan mengikuti format BA HRGA; nama contoh (data dummy), ubah di halaman
# Berita acara → Penanda tangan.
PENANDA_TANGAN = [
    ('Dibuat oleh',    'Buyung',             'GA Supply & Inventory'),
    ('Diperiksa oleh', 'Siti Rahayu',        'GA Sec Head'),
    ('Diketahui oleh', 'Wulan Prameswari',   'HRGA Dept Head'),
    ('Disetujui oleh', 'Rudy Simanjuntak',   'BU Head'),
    ('Disetujui oleh', 'Hartono Wibowo',     'COO'),
]
AKTIF = ['pending', 'acknowledged', 'in_progress']


def isi_awal(apps, schema_editor):
    Penanda = apps.get_model('bibasset', 'PenandaTanganBA')
    BA = apps.get_model('bibasset', 'BeritaAcara')
    Item = apps.get_model('bibasset', 'BeritaAcaraItem')
    DamageReport = apps.get_model('bibasset', 'DamageReport')
    Record = apps.get_model('bibasset', 'MaintenanceRecord')

    for i, (peran, nama, jabatan) in enumerate(PENANDA_TANGAN):
        Penanda.objects.create(urutan=i, peran=peran, nama=nama, jabatan=jabatan)

    drafts = {}

    def tambah(asset, sumber, kerusakan, kapan, **link):
        dept = asset.owned_by_department_id
        if dept not in drafts:
            drafts[dept] = BA.objects.create(jenis='kerusakan', status='draft', department_id=dept)
        ba = drafts[dept]
        item = Item.objects.filter(ba=ba, asset=asset).first()
        if item:
            item.kerusakan = f'{item.kerusakan}; {kerusakan}'
            item.save()
        else:
            item = Item.objects.create(ba=ba, asset=asset, sumber=sumber, kerusakan=kerusakan, **link)
            Item.objects.filter(pk=item.pk).update(created_at=kapan)   # waktu temuan asli, bukan waktu migrasi

    # Temuan yang masih terbuka saat fitur ini dipasang
    for r in DamageReport.objects.filter(report_status__in=AKTIF).select_related('asset').order_by('reported_at'):
        teks = r.description.strip() if r.description.strip().lower().startswith(r.title.strip().lower()) else f'{r.title} — {r.description}'
        tambah(r.asset, 'laporan', teks, r.reported_at, damage_report=r)
    batas = date.today() - timedelta(days=45)
    for m in (Record.objects.exclude(maintenance_type='corrective')
              .filter(hasil__in=['minor_issue', 'major_issue'], tanggal_pelaksanaan__gte=batas)
              .exclude(asset__lifecycle_status='writeoff').select_related('asset').order_by('tanggal_pelaksanaan')):
        jenis = 'inspeksi' if m.maintenance_type == 'inspection' else 'maintenance preventive'
        hasil = 'Major issue' if m.hasil == 'major_issue' else 'Minor issue'
        kapan = datetime.combine(m.tanggal_pelaksanaan, time(14, 0), tzinfo=timezone.get_current_timezone())
        catatan = re.sub(r'^Dikerjakan vendor:[^.]*\.\s*', '', m.catatan_tambahan.strip()) or m.deskripsi_pekerjaan.strip()
        tambah(m.asset, 'inspeksi', f'{hasil} saat {jenis}: {catatan}',
               kapan, maintenance_record=m)


def hapus(apps, schema_editor):
    apps.get_model('bibasset', 'BeritaAcara').objects.all().delete()
    apps.get_model('bibasset', 'PenandaTanganBA').objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [('bibasset', '0017_berita_acara')]
    operations = [migrations.RunPython(isi_awal, hapus)]
