from django.shortcuts import redirect
from django.contrib import messages
from functools import wraps


def role_required(role_check_function):

    def decorator(view_func):

        @wraps(view_func)
        def wrapper(request, *args, **kwargs):

            # Pastikan user punya employee
            if not hasattr(request.user, "employee"):
                messages.error(request, "User belum memiliki data employee")
                return redirect("dashboard")

            # Jalankan function checker
            if role_check_function(request.user):
                return view_func(request, *args, **kwargs)

            messages.error(request, "Anda tidak memiliki akses")
            return redirect("dashboard")

        return wrapper

    return decorator
