from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations


def backfill(apps, schema_editor):
    """Aset yang sudah berstatus write off sebelum fitur ini diberi catatan write off."""
    Asset = apps.get_model('bibasset', 'Asset')
    AssetWriteOff = apps.get_model('bibasset', 'AssetWriteOff')
    MaintenanceRecord = apps.get_model('bibasset', 'MaintenanceRecord')
    AssetFinancial = apps.get_model('bibasset', 'AssetFinancial')

    for asset in Asset.objects.filter(lifecycle_status='writeoff', write_off__isnull=True):
        last = MaintenanceRecord.objects.filter(asset=asset).order_by('-tanggal_pelaksanaan').first()
        tanggal = last.tanggal_pelaksanaan if last else date.today()
        fin = AssetFinancial.objects.filter(asset=asset).first()
        book = None
        if fin:
            # Rumus sama dengan AssetFinancial.book_value()
            dep = (fin.nilai_awal_aset - fin.nilai_residual) / Decimal(fin.umur_ekonomis or 1)
            years = (Decimal(max((tanggal - asset.tanggal_pembelian).days, 0)) / Decimal('365.25')
                     ).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
            book = max(fin.nilai_awal_aset - dep * min(years, Decimal(fin.umur_ekonomis)), fin.nilai_residual)
        AssetWriteOff.objects.create(
            asset=asset, tanggal=tanggal, alasan='lainnya',
            keterangan='Dicatat otomatis dari status write off lama (sebelum ada alur write off resmi).',
            nilai_perolehan=fin.nilai_awal_aset if fin else None, nilai_buku=book,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('bibasset', '0015_asset_write_off'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
