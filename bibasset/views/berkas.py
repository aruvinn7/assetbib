"""Sajikan file unggahan yang disimpan di database (lihat bibasset/storage.py)."""
import hashlib
from pathlib import PurePosixPath

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseNotModified
from django.views.decorators.http import require_safe

from ..models import BerkasMedia

# Hanya tipe ini yang ditampilkan langsung di browser; selain itu diunduh,
# supaya file HTML/SVG yang diunggah tidak bisa menjalankan skrip di domain aplikasi.
INLINE = {'image/jpeg', 'image/png', 'image/webp', 'image/gif', 'application/pdf'}


@login_required(login_url='login')
@require_safe
def berkas_media(request, nama):
    row = BerkasMedia.objects.filter(nama=nama).values_list('isi', 'tipe_konten').first()
    if row is None:
        raise Http404('Berkas tidak ditemukan')
    isi, tipe = bytes(row[0]), row[1]
    etag = '"%s"' % hashlib.md5(isi, usedforsecurity=False).hexdigest()
    if request.headers.get('If-None-Match') == etag:
        return HttpResponseNotModified()

    inline = tipe in INLINE
    resp = HttpResponse(isi, content_type=tipe if inline else 'application/octet-stream')
    filename = PurePosixPath(nama).name.replace('"', '')
    resp['Content-Disposition'] = f'{"inline" if inline else "attachment"}; filename="{filename}"'
    resp['Content-Length'] = str(len(isi))
    resp['ETag'] = etag
    # Nama berkas tidak pernah ditimpa → aman di-cache lama, tapi hanya di browser pengguna (perlu login)
    resp['Cache-Control'] = 'private, max-age=31536000, immutable'
    return resp
