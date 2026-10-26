from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def umur(detail):
    """Asset.umur_asset_detail → "5 tahun 4 bulan", "4 bulan", atau "kurang dari 1 bulan"."""
    years, months = detail.get('years', 0), detail.get('months', 0)
    parts = ([f'{years} tahun'] if years else []) + ([f'{months} bulan'] if months else [])
    return ' '.join(parts) or 'kurang dari 1 bulan'


@register.filter
def umur_singkat(detail):
    """Untuk kolom tabel: "5 tahun", atau "4 bulan" bila belum setahun."""
    years, months = detail.get('years', 0), detail.get('months', 0)
    if years:
        return f'{years} tahun'
    return f'{months} bulan' if months else '< 1 bulan'


@register.filter
def sejak(value):
    """Umur singkat dalam bahasa Indonesia: "baru saja", "40 menit lalu", "3 hari lalu"."""
    if not value:
        return ''
    detik = int((timezone.now() - value).total_seconds())
    if detik < 60:
        return 'baru saja'
    for satuan, panjang in (('hari', 86400), ('jam', 3600), ('menit', 60)):
        if detik >= panjang:
            n = detik // panjang
            if satuan == 'hari' and n >= 14:
                return f'{n // 7} minggu lalu'
            return f'{n} {satuan} lalu'
