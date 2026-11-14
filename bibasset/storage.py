"""Penyimpanan file unggahan di database (tabel BerkasMedia).

Dipakai di server tanpa layanan penyimpanan terpisah (Vercel + Neon): STORAGES['default'] menunjuk
ke kelas ini, sehingga FileField/ImageField menyimpan isi file ke database dan URL-nya
mengarah ke /berkas/<nama>, yang hanya bisa dibuka pengguna yang sudah login (views/berkas.py).

Nama file tidak pernah ditimpa: nama yang sudah ada diberi akhiran acak oleh Django, sehingga
browser boleh menyimpan cache file selamanya.
"""
import mimetypes

from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.urls import reverse
from django.utils.deconstruct import deconstructible


def _clean(name):
    """Nama selalu pakai '/', juga saat Django membuat nama di Windows (os.path memakai backslash)."""
    return str(name).replace('\\', '/')


@deconstructible
class DatabaseStorage(Storage):
    def _model(self):
        from .models import BerkasMedia  # impor di sini: storage dimuat sebelum app siap
        return BerkasMedia

    def _open(self, name, mode='rb'):
        name = _clean(name)
        row = self._model().objects.filter(nama=name).values_list('isi', flat=True).first()
        if row is None:
            raise FileNotFoundError(f'Berkas tidak ada di database: {name}')
        return ContentFile(bytes(row), name=name)

    def _save(self, name, content):
        name = _clean(name)
        if hasattr(content, 'seek'):
            content.seek(0)
        data = b''.join(content.chunks()) if hasattr(content, 'chunks') else content.read()
        tipe = getattr(content, 'content_type', None) or mimetypes.guess_type(name)[0] or ''
        self._model().objects.create(nama=name, isi=data, ukuran=len(data), tipe_konten=tipe[:100])
        return name

    def exists(self, name):
        name = _clean(name)
        return self._model().objects.filter(nama=name).exists()

    def delete(self, name):
        if name:
            self._model().objects.filter(nama=_clean(name)).delete()

    def size(self, name):
        row = self._model().objects.filter(nama=_clean(name)).values_list('ukuran', flat=True).first()
        if row is None:
            raise FileNotFoundError(name)
        return row

    def url(self, name):
        return reverse('berkas_media', args=[_clean(name)])

    def listdir(self, path):
        path = _clean(path)
        prefix = f"{path.rstrip('/')}/" if path else ''
        dirs, files = set(), []
        for nama in self._model().objects.filter(nama__startswith=prefix).values_list('nama', flat=True):
            rest = nama[len(prefix):]
            if '/' in rest:
                dirs.add(rest.split('/', 1)[0])
            else:
                files.append(rest)
        return sorted(dirs), files

    def get_created_time(self, name):
        return self._model().objects.get(nama=_clean(name)).dibuat_pada

    get_modified_time = get_created_time
