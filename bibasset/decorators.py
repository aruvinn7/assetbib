from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth import logout
from functools import wraps

from .roles import home_url_name


def redirect_home(request, error=None):
    """Ke beranda peran user. Akun tanpa akses dikeluarkan agar tidak berputar antar halaman."""
    home = home_url_name(request.user)
    if home is None:
        logout(request)
        messages.error(request, "Akun Anda belum punya akses. Hubungi GA untuk mengaturnya.")
        return redirect("login")
    if error:
        messages.error(request, error)
    return redirect(home)


def role_required(role_check_function):

    def decorator(view_func):

        @wraps(view_func)
        def wrapper(request, *args, **kwargs):

            # Belum login → ke halaman masuk, lalu kembali ke halaman ini setelah login
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), login_url="login")

            # Pastikan user punya employee
            if not hasattr(request.user, "employee"):
                logout(request)
                messages.error(request, "Akun ini belum terhubung ke data karyawan. Hubungi GA.")
                return redirect("login")

            # Jalankan function checker
            if role_check_function(request.user):
                return view_func(request, *args, **kwargs)

            return redirect_home(request, "Anda tidak memiliki akses ke halaman itu.")

        return wrapper

    return decorator
