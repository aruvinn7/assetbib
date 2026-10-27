# views/auth.py
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.utils.http import url_has_allowed_host_and_scheme

from ..roles import home_url_name


def login_view(request):
    if request.method == "POST":
        user = authenticate(
            request,
            username=request.POST.get("username", ""),
            password=request.POST.get("password", ""),
        )
        if user and home_url_name(user) is None:
            messages.error(request, "Akun Anda belum punya akses. Hubungi GA untuk mengaturnya.")
        elif user:
            login(request, user)
            # Kembali ke halaman yang tadi diminta (hanya URL di situs ini)
            next_url = request.POST.get("next") or request.GET.get("next", "")
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()},
                                                            require_https=request.is_secure()):
                return redirect(next_url)
            return redirect(home_url_name(user))
        else:
            messages.error(request, "Username atau password salah.")
    return render(request, "login.html")


def logout_view(request):
    logout(request)
    return redirect("login")
