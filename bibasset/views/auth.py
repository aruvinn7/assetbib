# views/auth.py
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect

from ..roles import is_user_role


def login_view(request):
    if request.method == "POST":
        user = authenticate(
            request,
            username=request.POST.get("username", ""),
            password=request.POST.get("password", ""),
        )
        if user:
            login(request, user)
            return redirect("user_dashboard" if is_user_role(user) else "dashboard")
        messages.error(request, "Username atau password salah.")
    return render(request, "login.html")


def logout_view(request):
    logout(request)
    return redirect("login")
