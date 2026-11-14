from django import template
from django.core.files.storage import default_storage

register = template.Library()


@register.filter
def media_url(name):
    """Nama berkas tersimpan (mis. dari .values('foto')) → URL-nya di penyimpanan yang aktif."""
    return default_storage.url(name) if name else ''
