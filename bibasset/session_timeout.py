# Batas waktu sesi login.
# Sesi berakhir bila pengguna tidak membuka halaman apa pun selama SESSION_IDLE_TIMEOUT detik.
# Waktu aktif terakhir disimpan di sesi dan diperbarui paling sering tiap SEGARKAN detik,
# supaya tidak menulis ke database di setiap permintaan.
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout

KUNCI = '_aktif_terakhir'
SEGARKAN = 300  # detik


def tandai_aktif(sender, request, user, **kwargs):
    """Dipanggil lewat sinyal user_logged_in: mulai hitung waktu sesi sejak login."""
    request.session[KUNCI] = int(time.time())


class SessionTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            sekarang = int(time.time())
            terakhir = request.session.get(KUNCI)
            # Tanpa penanda = sesi lama dari sebelum fitur ini ada → ikut diakhiri
            if terakhir is None or sekarang - terakhir > settings.SESSION_IDLE_TIMEOUT:
                logout(request)
                messages.info(request, 'Sesi Anda berakhir karena lama tidak aktif. Silakan masuk lagi.')
            elif sekarang - terakhir > SEGARKAN:
                request.session[KUNCI] = sekarang  # menyimpan ulang sesi → masa berlaku ikut diperpanjang
        return self.get_response(request)
